"""Use cases shared by the bot handlers: betting, admin edits and auditing.

Every function here works on an open connection inside the caller's
transaction and has no Telegram dependency, so it can be tested directly.
"""

import difflib
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any

from jairbet.bettors import place_auto_bets
from jairbet.config import DEFAULT_TIMEZONE, JAIR_NAME, MANUAL_CODE
from jairbet.repository import (
    add_competition,
    add_match,
    add_team,
    add_team_alias,
    delete_auto_bets,
    delete_bet,
    delete_match,
    find_team_id,
    get_bet,
    get_bettor,
    get_competition,
    get_match,
    get_scoring_config,
    get_team,
    get_team_aliases,
    get_teams,
    log_audit,
    merge_teams,
    row_dict,
    set_match_result,
    update_match_fields,
    update_scoring_config,
    upsert_bet,
)
from jairbet.scoring import parse_score_text
from jairbet.sync import normalise_team_name
from jairbet.timeutil import (
    from_db,
    is_locked,
    local_datetime_to_utc,
    local_tz,
    to_db,
)

STATUS_LABELS = {
    "SCHEDULED": "agendado",
    "LIVE": "em andamento",
    "FINISHED": "encerrado",
    "POSTPONED": "adiado",
    "CANCELLED": "cancelado",
}
STATUS_ALIASES = {
    "agendado": "SCHEDULED",
    "aovivo": "LIVE",
    "encerrado": "FINISHED",
    "adiado": "POSTPONED",
    "cancelado": "CANCELLED",
}


class ServiceError(ValueError):
    """A user-facing validation error (message is in pt-BR)."""


@dataclass(frozen=True)
class BetOutcome:
    """Result of saving a bet.

    Attributes:
        match: Match row.
        prediction: Saved prediction.
        animals: Animal predictions now stored for the match.
    """

    match: sqlite3.Row
    prediction: tuple[int, int]
    animals: dict[str, tuple[int, int]]


def _require_match(connection: sqlite3.Connection, match_id: int) -> sqlite3.Row:
    """Fetch a match or raise a friendly error.

    Args:
        connection: Open database connection.
        match_id: Match identifier.

    Returns:
        Match row.

    Raises:
        ServiceError: If the match does not exist.
    """
    match = get_match(connection, match_id)
    if match is None:
        raise ServiceError(f"Jogo #{match_id} não encontrado.")
    return match


def bet_is_open(
    match: sqlite3.Row, now: datetime | None = None, tz_name: str = DEFAULT_TIMEZONE
) -> bool:
    """Tell whether Jair may still bet on a match.

    Args:
        match: Match row.
        now: Current time.
        tz_name: IANA timezone.

    Returns:
        ``True`` before the lock for scheduled or postponed matches.
    """
    if match["status"] not in ("SCHEDULED", "POSTPONED"):
        return False
    if match["home_goals"] is not None:
        return False
    return not is_locked(match["match_date"], match["kickoff_at"], now, tz_name)


def save_bet(
    connection: sqlite3.Connection,
    match_id: int,
    bettor_name: str,
    pred_home: int,
    pred_away: int,
    actor_telegram_id: int | None,
    as_admin: bool = False,
    now: datetime | None = None,
    tz_name: str = DEFAULT_TIMEZONE,
) -> BetOutcome:
    """Save a bet, enforce the lock for Jair, and refresh the animal bets.

    Args:
        connection: Open database connection.
        match_id: Match identifier.
        bettor_name: Bettor name.
        pred_home: Predicted home goals.
        pred_away: Predicted away goals.
        actor_telegram_id: Telegram user making the change.
        as_admin: Admin edit: ignores the lock and marks ``source='admin'``.
        now: Current time.
        tz_name: IANA timezone.

    Returns:
        The saved bet and the animal predictions.

    Raises:
        ServiceError: For unknown matches/bettors, locked matches or bad goals.
    """
    if not (0 <= pred_home <= 30 and 0 <= pred_away <= 30):
        raise ServiceError("Os gols precisam estar entre 0 e 30.")
    match = _require_match(connection, match_id)
    bettor = get_bettor(connection, bettor_name)
    if bettor is None:
        raise ServiceError(f"Apostador {bettor_name!r} não encontrado.")
    if not as_admin and not bet_is_open(match, now, tz_name):
        raise ServiceError("As apostas para este jogo já estão fechadas.")
    before = row_dict(get_bet(connection, match_id, bettor["bettor_id"]))
    upsert_bet(
        connection,
        match_id,
        bettor["bettor_id"],
        pred_home,
        pred_away,
        source="admin" if as_admin else "user",
    )
    if as_admin:
        log_audit(
            connection,
            actor_telegram_id,
            "upsert",
            "bet",
            match_id,
            before,
            {"bettor": bettor["name"], "pred_home": pred_home, "pred_away": pred_away},
        )
    animals = (
        place_auto_bets(connection, match_id, overwrite=True, tz_name=tz_name)
        if bettor["name"] == JAIR_NAME
        else {}
    )
    return BetOutcome(match, (pred_home, pred_away), animals)


def remove_bet(
    connection: sqlite3.Connection,
    match_id: int,
    bettor_name: str,
    actor_telegram_id: int | None,
) -> None:
    """Delete a bet (admin). Removing Jair's bet also removes automatic bets.

    Args:
        connection: Open database connection.
        match_id: Match identifier.
        bettor_name: Bettor name.
        actor_telegram_id: Telegram user making the change.

    Raises:
        ServiceError: If the bettor or bet does not exist.
    """
    bettor = get_bettor(connection, bettor_name)
    if bettor is None:
        raise ServiceError(f"Apostador {bettor_name!r} não encontrado.")
    before = get_bet(connection, match_id, bettor["bettor_id"])
    if before is None:
        raise ServiceError("Esse palpite não existe.")
    delete_bet(connection, match_id, bettor["bettor_id"])
    if bettor["name"] == JAIR_NAME:
        delete_auto_bets(connection, match_id)
    log_audit(
        connection, actor_telegram_id, "delete", "bet", match_id, row_dict(before)
    )


def set_result(
    connection: sqlite3.Connection,
    match_id: int,
    score: tuple[int, int] | None,
    actor_telegram_id: int | None,
) -> bool:
    """Set or clear a match result as admin (locks it against sync).

    Args:
        connection: Open database connection.
        match_id: Match identifier.
        score: Final score, or ``None`` to clear it.
        actor_telegram_id: Telegram user making the change.

    Returns:
        ``True`` if the match changed to a new final result.

    Raises:
        ServiceError: If the match does not exist.
    """
    before = _require_match(connection, match_id)
    home, away = score if score else (None, None)
    set_match_result(connection, match_id, home, away, lock=True)
    log_audit(
        connection,
        actor_telegram_id,
        "result",
        "match",
        match_id,
        {"home_goals": before["home_goals"], "away_goals": before["away_goals"]},
        {"home_goals": home, "away_goals": away},
    )
    return score is not None and (before["home_goals"], before["away_goals"]) != score


def resolve_or_create_team(
    connection: sqlite3.Connection, name: str
) -> tuple[int, bool]:
    """Find a team by name/alias, creating it when unknown.

    Args:
        connection: Open database connection.
        name: Team name typed by the admin.

    Returns:
        ``(team_id, created)``.

    Raises:
        ServiceError: If the name is empty.
    """
    clean = name.strip()
    if not clean:
        raise ServiceError("Informe o nome do time.")
    team_id = find_team_id(connection, clean)
    if team_id is not None:
        return team_id, False
    return add_team(connection, clean), True


def search_teams(
    connection: sqlite3.Connection, text: str, limit: int = 3
) -> list[tuple[int, str]]:
    """Find teams by exact name/alias, normalised name, or similar spelling.

    Args:
        connection: Open database connection.
        text: Name typed by a user.
        limit: Maximum suggestions.

    Returns:
        ``(team_id, name)`` pairs, best match first.
    """
    exact = find_team_id(connection, text)
    if exact is not None:
        team = get_team(connection, exact)
        return [(exact, team["name"])] if team else []
    names: dict[str, tuple[int, str]] = {}
    for team in get_teams(connection):
        names[normalise_team_name(team["name"])] = (team["team_id"], team["name"])
    for alias in get_team_aliases(connection):
        names.setdefault(
            normalise_team_name(alias["alias"]), (alias["team_id"], alias["team"])
        )
    key = normalise_team_name(text)
    if key in names:
        return [names[key]]
    found: list[tuple[int, str]] = []
    for close in difflib.get_close_matches(key, list(names), n=limit * 2, cutoff=0.6):
        if names[close] not in found:
            found.append(names[close])
    return found[:limit]


def parse_date_text(text: str) -> date:
    """Parse ``dd/mm/aaaa``, ``dd/mm`` (current year) or ``aaaa-mm-dd``.

    Args:
        text: User input.

    Returns:
        Parsed date.

    Raises:
        ServiceError: If the date is invalid.
    """
    clean = text.strip()
    try:
        if "-" in clean:
            return date.fromisoformat(clean)
        parts = [int(part) for part in clean.split("/")]
        if len(parts) == 2:
            parts.append(date.today().year)
        day, month, year = parts
        return date(year if year > 100 else 2000 + year, month, day)
    except ValueError as exc:
        raise ServiceError("Data inválida. Use dd/mm/aaaa.") from exc


def parse_time_text(text: str) -> time | None:
    """Parse ``HH:MM`` (or ``-`` for no kickoff time).

    Args:
        text: User input.

    Returns:
        Parsed time, or ``None`` when the admin skips it.

    Raises:
        ServiceError: If the time is invalid.
    """
    clean = text.strip().lower().replace("h", ":")
    if clean in {"-", "", "sem"}:
        return None
    try:
        hour, _, minute = clean.partition(":")
        return time(int(hour), int(minute or 0))
    except ValueError as exc:
        raise ServiceError("Horário inválido. Use HH:MM ou - para pular.") from exc


def create_manual_match(
    connection: sqlite3.Connection,
    competition_code: str,
    home_name: str,
    away_name: str,
    match_day: date,
    kickoff: time | None,
    actor_telegram_id: int | None,
    tz_name: str = DEFAULT_TIMEZONE,
) -> int:
    """Create a match typed in by the admin (any championship).

    Args:
        connection: Open database connection.
        competition_code: Competition code (created as manual when unknown).
        home_name: Home team name.
        away_name: Away team name.
        match_day: Local match date.
        kickoff: Local kickoff time, or ``None``.
        actor_telegram_id: Telegram user making the change.
        tz_name: IANA timezone.

    Returns:
        New match ID.

    Raises:
        ServiceError: For identical teams or a duplicate fixture.
    """
    competition = get_competition(connection, competition_code or MANUAL_CODE)
    competition_id = (
        int(competition["competition_id"])
        if competition
        else add_competition(connection, competition_code, competition_code)
    )
    home_id, _ = resolve_or_create_team(connection, home_name)
    away_id, _ = resolve_or_create_team(connection, away_name)
    if home_id == away_id:
        raise ServiceError("Mandante e visitante precisam ser diferentes.")
    kickoff_at = (
        to_db(local_datetime_to_utc(match_day, kickoff, tz_name)) if kickoff else None
    )
    try:
        match_id = add_match(
            connection,
            match_day.isoformat(),
            home_id,
            away_id,
            competition_id=competition_id,
            kickoff_at=kickoff_at,
            source="manual",
        )
    except sqlite3.IntegrityError as exc:
        raise ServiceError("Esse jogo já está cadastrado nessa data.") from exc
    log_audit(
        connection,
        actor_telegram_id,
        "create",
        "match",
        match_id,
        None,
        row_dict(get_match(connection, match_id)),
    )
    return match_id


def edit_match(
    connection: sqlite3.Connection,
    match_id: int,
    field: str,
    value: str,
    actor_telegram_id: int | None,
    tz_name: str = DEFAULT_TIMEZONE,
) -> str:
    """Apply one admin edit to a match and lock it against sync.

    Supported fields: ``data``, ``hora``, ``mandante``, ``visitante``,
    ``status``, ``competicao``, ``rodada``, ``travar`` and ``destravar``.

    Args:
        connection: Open database connection.
        match_id: Match identifier.
        field: Field name in pt-BR.
        value: New value as typed.
        actor_telegram_id: Telegram user making the change.
        tz_name: IANA timezone.

    Returns:
        Short confirmation text.

    Raises:
        ServiceError: For unknown fields or invalid values.
    """
    match = _require_match(connection, match_id)
    key = field.strip().lower()
    changes: dict[str, Any] = {}
    if key == "data":
        new_day = parse_date_text(value)
        changes["match_date"] = new_day.isoformat()
        if match["kickoff_at"]:
            old = from_db(match["kickoff_at"]).astimezone(local_tz(tz_name))
            changes["kickoff_at"] = to_db(
                local_datetime_to_utc(new_day, old.time(), tz_name)
            )
    elif key == "hora":
        clock = parse_time_text(value)
        changes["kickoff_at"] = (
            to_db(
                local_datetime_to_utc(
                    date.fromisoformat(match["match_date"]), clock, tz_name
                )
            )
            if clock
            else None
        )
    elif key in ("mandante", "visitante"):
        team_id, _ = resolve_or_create_team(connection, value)
        changes["home_team_id" if key == "mandante" else "away_team_id"] = team_id
    elif key == "status":
        status = STATUS_ALIASES.get(value.strip().lower(), value.strip().upper())
        if status not in STATUS_LABELS:
            raise ServiceError("Status: agendado, adiado, cancelado ou encerrado.")
        changes["status"] = status
    elif key in ("competicao", "competição"):
        competition = get_competition(connection, value)
        if competition is None:
            raise ServiceError(f"Competição {value!r} não existe.")
        changes["competition_id"] = competition["competition_id"]
    elif key == "rodada":
        changes["round"] = int(value) if value.strip().isdigit() else None
    elif key == "travar":
        changes["admin_locked"] = 1
    elif key == "destravar":
        changes["admin_locked"] = 0
    else:
        raise ServiceError(
            "Campo inválido. Use data, hora, mandante, visitante, status, "
            "competicao, rodada, travar ou destravar."
        )
    if key not in ("travar", "destravar"):
        changes["admin_locked"] = 1
    before = {column: match[column] for column in changes}
    try:
        update_match_fields(connection, match_id, changes)
    except sqlite3.IntegrityError as exc:
        raise ServiceError(
            "Não foi possível salvar: já existe esse jogo nessa data."
        ) from exc
    log_audit(
        connection, actor_telegram_id, "update", "match", match_id, before, changes
    )
    return f"Jogo #{match_id} atualizado ({key})."


def remove_match(
    connection: sqlite3.Connection, match_id: int, actor_telegram_id: int | None
) -> sqlite3.Row:
    """Delete a match and its bets.

    Args:
        connection: Open database connection.
        match_id: Match identifier.
        actor_telegram_id: Telegram user making the change.

    Returns:
        The deleted match row.

    Raises:
        ServiceError: If the match does not exist.
    """
    match = _require_match(connection, match_id)
    delete_match(connection, match_id)
    log_audit(
        connection, actor_telegram_id, "delete", "match", match_id, row_dict(match)
    )
    return match


def change_scoring(
    connection: sqlite3.Connection,
    points_winner: int,
    bonus_exact: int,
    actor_telegram_id: int | None,
) -> None:
    """Change the global scoring (applies retroactively).

    Args:
        connection: Open database connection.
        points_winner: Points for the right winner/draw.
        bonus_exact: Bonus for the exact score.
        actor_telegram_id: Telegram user making the change.

    Raises:
        ServiceError: For negative values.
    """
    if points_winner < 0 or bonus_exact < 0:
        raise ServiceError("A pontuação não pode ser negativa.")
    before = row_dict(get_scoring_config(connection))
    update_scoring_config(connection, points_winner, bonus_exact)
    log_audit(
        connection,
        actor_telegram_id,
        "update",
        "scoring_config",
        1,
        before,
        {"points_winner": points_winner, "bonus_exact": bonus_exact},
    )


def set_alias(
    connection: sqlite3.Connection,
    alias: str,
    team_name: str,
    actor_telegram_id: int | None,
) -> str:
    """Map a spelling to a team, merging a duplicate team if one exists.

    Args:
        connection: Open database connection.
        alias: Alternate spelling (possibly an auto-created duplicate team).
        team_name: Canonical team name.
        actor_telegram_id: Telegram user making the change.

    Returns:
        Confirmation text.

    Raises:
        ServiceError: If the canonical team does not exist or merging fails.
    """
    target = find_team_id(connection, team_name)
    if target is None:
        raise ServiceError(f"Time {team_name!r} não encontrado.")
    duplicate = connection.execute(
        "SELECT team_id FROM team WHERE name = ?", (alias.strip(),)
    ).fetchone()
    if duplicate is not None and duplicate["team_id"] != target:
        try:
            merge_teams(connection, int(duplicate["team_id"]), target)
        except sqlite3.IntegrityError as exc:
            raise ServiceError(
                "Não foi possível unir os times: haveria jogos duplicados."
            ) from exc
        message = f"Time {alias!r} unido a {team_name!r}."
    else:
        add_team_alias(connection, alias, target)
        message = f"Apelido {alias!r} → {team_name!r} salvo."
    log_audit(
        connection,
        actor_telegram_id,
        "alias",
        "team",
        target,
        None,
        {"alias": alias, "team": team_name},
    )
    return message


__all__ = [
    "BetOutcome",
    "STATUS_LABELS",
    "ServiceError",
    "bet_is_open",
    "change_scoring",
    "create_manual_match",
    "edit_match",
    "parse_date_text",
    "parse_score_text",
    "parse_time_text",
    "remove_bet",
    "remove_match",
    "resolve_or_create_team",
    "save_bet",
    "search_teams",
    "set_alias",
    "set_result",
]
