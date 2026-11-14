"""Synchronise fixtures and results from a provider into the database.

Sync is idempotent: matches are upserted by provider ID, legacy matches are
linked instead of duplicated, and matches the admin edited
(``admin_locked = 1``) are never overwritten.
"""

import re
import sqlite3
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import cast

from jairbet.config import DEFAULT_TIMEZONE, TEAM_ALIASES, TEAM_NAME_STOPWORDS
from jairbet.console import info, warning
from jairbet.providers.base import ApiMatch, FixtureProvider
from jairbet.repository import (
    add_competition,
    add_match,
    add_team,
    add_team_alias,
    find_match_by_external,
    find_reconcile_candidate,
    finish_sync_run,
    get_team_aliases,
    get_teams,
    start_sync_run,
    tracked_unfinished_matches,
    update_match_fields,
)
from jairbet.timeutil import from_db, local_date, to_db, utc_now

PROVIDER_NAME = "football_data"
TRACKED_CODE = "TRACKED"


@dataclass(frozen=True)
class SyncEvent:
    """Something the bot may announce after a sync.

    Attributes:
        kind: ``created``, ``linked``, ``rescheduled``, ``finished`` or
            ``unknown_team``.
        match_id: Affected match, if any.
        detail: Human-readable detail (team name for ``unknown_team``).
    """

    kind: str
    match_id: int | None
    detail: str = ""


@dataclass
class SyncResult:
    """Counts and events from one sync.

    Attributes:
        competition: Competition code synced.
        fetched: Matches received.
        created: Matches created.
        updated: Matches updated or linked.
        events: Events for notifications.
    """

    competition: str
    fetched: int = 0
    created: int = 0
    updated: int = 0
    events: list[SyncEvent] = field(default_factory=list)


def normalise_team_name(name: str) -> str:
    """Reduce a team name to a comparable key.

    Accents, punctuation, case, years and club-type words (FC, EC, SE, Clube,
    ...) are removed, so ``"São Paulo FC"`` and ``"Sao Paulo"`` match.

    Args:
        name: Team name.

    Returns:
        Normalised key, possibly empty.
    """
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_text = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    words = re.split(r"[^a-z0-9]+", ascii_text.lower())
    kept = [
        word
        for word in words
        if word and word not in TEAM_NAME_STOPWORDS and not re.fullmatch(r"\d{4}", word)
    ]
    return " ".join(kept)


class TeamResolver:
    """Map provider team names to team IDs, creating unknown teams."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Build the lookup index from teams, stored aliases and config aliases.

        Args:
            connection: Open database connection.
        """
        self._connection = connection
        self._index: dict[str, int] = {}
        for team in get_teams(connection):
            self._index.setdefault(normalise_team_name(team["name"]), team["team_id"])
        for alias in get_team_aliases(connection):
            self._index.setdefault(
                normalise_team_name(alias["alias"]), alias["team_id"]
            )
        for alias, canonical in TEAM_ALIASES.items():
            team_id = self._index.get(normalise_team_name(canonical))
            if team_id is not None:
                self._index.setdefault(normalise_team_name(alias), team_id)

    def resolve(self, name: str, short_name: str | None = None) -> tuple[int, bool]:
        """Return the team ID for a provider name, creating the team if needed.

        Args:
            name: Full provider name, e.g. ``"CR Flamengo"``.
            short_name: Short provider name, e.g. ``"Flamengo"``.

        Returns:
            ``(team_id, created)``.
        """
        candidates = [value for value in (short_name, name) if value]
        for candidate in candidates:
            key = normalise_team_name(candidate)
            if key and key in self._index:
                team_id = self._index[key]
                self._remember(candidates, team_id)
                return team_id, False
        display = (short_name or name).strip()
        team_id = add_team(self._connection, display)
        self._remember(candidates, team_id)
        return team_id, True

    def _remember(self, names: Sequence[str], team_id: int) -> None:
        """Store provider spellings as aliases and in the index.

        Args:
            names: Provider names for the team.
            team_id: Resolved team ID.
        """
        for name in names:
            key = normalise_team_name(name)
            if key:
                self._index[key] = team_id
            existing = self._connection.execute(
                """SELECT 1 FROM team WHERE name = ?
                UNION ALL SELECT 1 FROM team_alias WHERE alias = ?""",
                (name.strip(), name.strip()),
            ).fetchone()
            if existing is None:
                add_team_alias(self._connection, name, team_id)


def _desired_fields(
    api: ApiMatch, competition_id: int, tz_name: str
) -> dict[str, object]:
    """Return the match columns implied by provider data.

    Args:
        api: Provider match.
        competition_id: Local competition ID.
        tz_name: IANA timezone used for the local match date.

    Returns:
        Column-to-value mapping.
    """
    finished = api.status == "FINISHED" and api.home_goals is not None
    return {
        "match_date": local_date(from_db(api.kickoff_at), tz_name).isoformat(),
        "kickoff_at": api.kickoff_at,
        "status": api.status if finished or api.status != "FINISHED" else "LIVE",
        "home_goals": api.home_goals if finished else None,
        "away_goals": api.away_goals if finished else None,
        "round": api.round_number,
        "competition_id": competition_id,
    }


def sync_matches(
    connection: sqlite3.Connection,
    matches: Sequence[ApiMatch],
    competition_code: str,
    tz_name: str = DEFAULT_TIMEZONE,
    auto_sync_new: bool = False,
) -> SyncResult:
    """Upsert provider matches. The caller manages the transaction.

    Args:
        connection: Open database connection.
        matches: Provider matches.
        competition_code: Code recorded in the result.
        tz_name: IANA timezone used for local match dates.
        auto_sync_new: ``auto_sync`` value for competitions created here.

    Returns:
        Counts and events.
    """
    result = SyncResult(competition_code, fetched=len(matches))
    resolver = TeamResolver(connection)
    competitions: dict[str, int] = {}
    for api in matches:
        code = api.competition_code or competition_code
        if code not in competitions:
            competitions[code] = add_competition(
                connection,
                code,
                api.competition_name or code,
                PROVIDER_NAME,
                auto_sync_new,
            )
        home_id, home_new = resolver.resolve(api.home_name, api.home_short)
        away_id, away_new = resolver.resolve(api.away_name, api.away_short)
        for created, team_name in (
            (home_new, api.home_name),
            (away_new, api.away_name),
        ):
            if created:
                result.events.append(SyncEvent("unknown_team", None, team_name))
        desired = _desired_fields(api, competitions[code], tz_name)
        existing = find_match_by_external(connection, api.external_id)
        if existing is None:
            _create_or_link(connection, api, home_id, away_id, desired, result)
            continue
        if existing["admin_locked"]:
            continue
        changes = {
            column: value
            for column, value in desired.items()
            if existing[column] != value
        }
        if not changes:
            continue
        try:
            update_match_fields(connection, existing["match_id"], changes)
        except sqlite3.IntegrityError as exc:
            warning(f"Could not update match {existing['match_id']}: {exc}")
            continue
        result.updated += 1
        _record_events(existing, desired, result)
    return result


def _create_or_link(
    connection: sqlite3.Connection,
    api: ApiMatch,
    home_id: int,
    away_id: int,
    desired: dict[str, object],
    result: SyncResult,
) -> None:
    """Link a provider match to a legacy match, or create a new one.

    Args:
        connection: Open database connection.
        api: Provider match.
        home_id: Local home team ID.
        away_id: Local away team ID.
        desired: Columns implied by the provider data.
        result: Result object updated in place.
    """
    candidate = find_reconcile_candidate(
        connection, home_id, away_id, str(desired["match_date"])
    )
    if candidate is not None:
        link: dict[str, object] = {"external_id": api.external_id, "source": "api"}
        if candidate["admin_locked"]:
            link.update(
                {
                    "competition_id": desired["competition_id"],
                    "round": desired["round"],
                }
            )
        else:
            link.update(desired)
            # Keep a legacy result if the provider has none yet.
            if desired["home_goals"] is None and candidate["home_goals"] is not None:
                link.update(
                    {
                        "home_goals": candidate["home_goals"],
                        "away_goals": candidate["away_goals"],
                        "status": "FINISHED",
                    }
                )
        try:
            update_match_fields(connection, candidate["match_id"], link)
        except sqlite3.IntegrityError as exc:
            warning(f"Could not link match {candidate['match_id']}: {exc}")
            return
        result.updated += 1
        result.events.append(SyncEvent("linked", candidate["match_id"]))
        if not candidate["admin_locked"]:
            _record_events(candidate, link, result)
        return
    try:
        match_id = add_match(
            connection,
            str(desired["match_date"]),
            home_id,
            away_id,
            cast(int | None, desired["home_goals"]),
            cast(int | None, desired["away_goals"]),
            competition_id=cast(int, desired["competition_id"]),
            kickoff_at=api.kickoff_at,
            status=str(desired["status"]),
            source="api",
            external_id=api.external_id,
            round_number=api.round_number,
        )
    except sqlite3.IntegrityError as exc:
        warning(
            f"Skipping {api.home_name} x {api.away_name} on "
            f"{desired['match_date']}: {exc}"
        )
        return
    result.created += 1
    result.events.append(SyncEvent("created", match_id))


def _record_events(
    before: sqlite3.Row, after: dict[str, object], result: SyncResult
) -> None:
    """Append rescheduled/finished events for a changed match.

    Args:
        before: Match row before the change.
        after: New column values.
        result: Result object updated in place.
    """
    match_id = int(before["match_id"])
    was_finished = before["status"] == "FINISHED"
    new_kickoff = after.get("kickoff_at", before["kickoff_at"])
    if (
        not was_finished
        and before["kickoff_at"] is not None
        and new_kickoff != before["kickoff_at"]
    ):
        result.events.append(SyncEvent("rescheduled", match_id))
    new_goals = (
        after.get("home_goals", before["home_goals"]),
        after.get("away_goals", before["away_goals"]),
    )
    old_goals = (before["home_goals"], before["away_goals"])
    if (
        after.get("status", before["status"]) == "FINISHED"
        and new_goals[0] is not None
        and (not was_finished or new_goals != old_goals)
    ):
        result.events.append(SyncEvent("finished", match_id))


def run_sync(
    connection: sqlite3.Connection,
    provider: FixtureProvider,
    competition_code: str,
    date_from: date | None = None,
    date_to: date | None = None,
    tz_name: str = DEFAULT_TIMEZONE,
) -> SyncResult:
    """Fetch a competition and store it in one transaction, logging the run.

    Args:
        connection: Open database connection.
        provider: Fixture provider.
        competition_code: Competition to sync.
        date_from: Optional first UTC date.
        date_to: Optional last UTC date.
        tz_name: IANA timezone used for local match dates.

    Returns:
        Counts and events.

    Raises:
        Exception: Any provider or database error, after logging it.
    """
    with connection:
        run_id = start_sync_run(connection, competition_code)
    try:
        matches = provider.fetch_matches(competition_code, date_from, date_to)
        with connection:
            result = sync_matches(
                connection, matches, competition_code, tz_name, auto_sync_new=True
            )
            finish_sync_run(
                connection, run_id, "ok", result.fetched, result.created, result.updated
            )
    except Exception as exc:
        with connection:
            finish_sync_run(connection, run_id, "error", error=str(exc)[:500])
        raise
    info(
        f"Sync {competition_code}: {result.fetched} fetched, "
        f"{result.created} created, {result.updated} updated"
    )
    return result


def sync_tracked(
    connection: sqlite3.Connection,
    provider: FixtureProvider,
    tz_name: str = DEFAULT_TIMEZONE,
) -> SyncResult:
    """Refresh single tracked matches from competitions that are not auto-synced.

    Args:
        connection: Open database connection.
        provider: Fixture provider.
        tz_name: IANA timezone used for local match dates.

    Returns:
        Combined counts and events.

    Raises:
        Exception: Any provider or database error, after logging it.
    """
    pending = tracked_unfinished_matches(connection, to_db(utc_now()))
    result = SyncResult(TRACKED_CODE)
    if not pending:
        return result
    with connection:
        run_id = start_sync_run(connection, TRACKED_CODE)
    try:
        fetched = [provider.fetch_match(row["external_id"]) for row in pending]
        with connection:
            result = sync_matches(connection, fetched, TRACKED_CODE, tz_name)
            finish_sync_run(
                connection, run_id, "ok", result.fetched, result.created, result.updated
            )
    except Exception as exc:
        with connection:
            finish_sync_run(connection, run_id, "error", error=str(exc)[:500])
        raise
    return result


def track_match(
    connection: sqlite3.Connection,
    provider: FixtureProvider,
    external_id: str,
    tz_name: str = DEFAULT_TIMEZONE,
) -> tuple[int | None, SyncResult]:
    """Add one provider match (from any competition) and keep it synced.

    Args:
        connection: Open database connection.
        provider: Fixture provider.
        external_id: Provider match ID.
        tz_name: IANA timezone used for local match dates.

    Returns:
        The local match ID (``None`` if it could not be stored) and the result.
    """
    api = provider.fetch_match(external_id)
    with connection:
        result = sync_matches(connection, [api], api.competition_code, tz_name)
    row = find_match_by_external(connection, external_id)
    return (int(row["match_id"]) if row else None), result
