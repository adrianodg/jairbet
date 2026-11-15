"""Database CRUD and reporting queries for Jairbet (all SQL lives here)."""

import json
import sqlite3
from collections.abc import Iterable, Mapping
from typing import Any

from jairbet.config import BETTOR_KINDS, TEAM_ALIASES

MATCH_SELECT = """SELECT m.*, home.name AS home_team, away.name AS away_team,
    COALESCE(c.code, 'MANUAL') AS competition_code,
    COALESCE(c.name, 'Outros') AS competition_name,
    c.provider AS competition_provider, c.auto_sync AS competition_auto_sync
FROM match AS m
JOIN team AS home ON home.team_id = m.home_team_id
JOIN team AS away ON away.team_id = m.away_team_id
LEFT JOIN competition AS c ON c.competition_id = m.competition_id"""

# Sort key putting legacy matches without kickoff at the start of their day.
PLAYED_AT = "COALESCE(m.kickoff_at, m.match_date || ' 00:00:00')"


def _kind_order(column: str) -> str:
    """Build an SQL sort key listing bettors as Jair, Macaco, Caranguejo, Corvo.

    Args:
        column: SQL expression holding the bettor kind.

    Returns:
        A ``CASE`` expression (constants only, no user values).
    """
    kinds = list(dict.fromkeys(BETTOR_KINDS.values()))
    whens = " ".join(f"WHEN '{kind}' THEN {index}" for index, kind in enumerate(kinds))
    return f"CASE {column} {whens} ELSE {len(kinds)} END"


# Display order of bettors in every listing.
BETTOR_ORDER = _kind_order("kind")
BET_ORDER = _kind_order("bettor_kind")
# Ties in the ranking keep the same order (by name, so v1 databases work too).
RANKING_ORDER = (
    "rank, CASE bettor "
    + " ".join(f"WHEN '{name}' THEN {i}" for i, name in enumerate(BETTOR_KINDS))
    + f" ELSE {len(BETTOR_KINDS)} END, bettor"
)

MATCH_UPDATABLE = frozenset(
    {
        "match_date",
        "home_team_id",
        "away_team_id",
        "home_goals",
        "away_goals",
        "kickoff_at",
        "status",
        "competition_id",
        "round",
        "admin_locked",
        "external_id",
        "source",
    }
)


def _one_id(
    connection: sqlite3.Connection, sql: str, values: tuple[object, ...]
) -> int:
    """Execute an insert/query and return its integer identifier.

    Args:
        connection: Open database connection.
        sql: Parameterised SQL statement.
        values: Statement parameter values.

    Returns:
        Database identifier from the statement's first returned column.

    Raises:
        RuntimeError: If the statement returns no row.
    """
    row = connection.execute(sql, values).fetchone()
    if row is None:
        raise RuntimeError("Expected database row was not returned")
    return int(row[0])


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    """Convert a row to a plain dictionary.

    Args:
        row: Database row or ``None``.

    Returns:
        Dictionary copy of the row, or ``None``.
    """
    return dict(row) if row is not None else None


# --- Bettors -----------------------------------------------------------------


def add_bettor(
    connection: sqlite3.Connection,
    name: str,
    active: bool = True,
    kind: str | None = None,
) -> int:
    """Create or update a bettor and return its ID.

    Args:
        connection: Open database connection.
        name: Bettor name.
        active: Whether this bettor is available for new bets.
        kind: Bettor kind; defaults to the configured kind or ``human``.

    Returns:
        Bettor identifier.
    """
    clean = name.strip()
    bettor_kind = kind or BETTOR_KINDS.get(clean, "human")
    return _one_id(
        connection,
        """INSERT INTO bettor (name, active, kind) VALUES (?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET active = excluded.active
        RETURNING bettor_id""",
        (clean, int(active), bettor_kind),
    )


def get_bettor(connection: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    """Find a bettor by case-insensitive name.

    Args:
        connection: Open database connection.
        name: Bettor name.

    Returns:
        Bettor row, or ``None``.
    """
    return connection.execute(
        "SELECT * FROM bettor WHERE name = ?", (name.strip(),)
    ).fetchone()


def get_bettor_id(connection: sqlite3.Connection, name: str) -> int | None:
    """Find a bettor ID by name.

    Args:
        connection: Open database connection.
        name: Bettor name.

    Returns:
        Bettor ID, or ``None``.
    """
    row = get_bettor(connection, name)
    return int(row["bettor_id"]) if row else None


def set_bettor_telegram_id(
    connection: sqlite3.Connection, name: str, telegram_user_id: int | None
) -> None:
    """Link a bettor to a Telegram account.

    Args:
        connection: Open database connection.
        name: Bettor name.
        telegram_user_id: Telegram user ID, or ``None`` to unlink.
    """
    if telegram_user_id is not None:
        connection.execute(
            """UPDATE bettor SET telegram_user_id = NULL
            WHERE telegram_user_id = ? AND name <> ?""",
            (telegram_user_id, name),
        )
    connection.execute(
        "UPDATE bettor SET telegram_user_id = ? WHERE name = ?",
        (telegram_user_id, name),
    )


def get_bettors(
    connection: sqlite3.Connection, active_only: bool = False
) -> list[sqlite3.Row]:
    """List bettors, humans first, then by name.

    Args:
        connection: Open database connection.
        active_only: Limit results to active bettors.

    Returns:
        Bettor rows.
    """
    return connection.execute(
        f"""SELECT * FROM bettor WHERE (? = 0 OR active = 1)
        ORDER BY {BETTOR_ORDER}, name""",
        (int(active_only),),
    ).fetchall()


# --- Teams -------------------------------------------------------------------


def add_team(connection: sqlite3.Connection, name: str) -> int:
    """Create or find a team by name.

    Args:
        connection: Open database connection.
        name: Team's display name.

    Returns:
        Team identifier.
    """
    return _one_id(
        connection,
        """INSERT INTO team (name) VALUES (?)
        ON CONFLICT(name) DO UPDATE SET name = team.name
        RETURNING team_id""",
        (name.strip(),),
    )


def add_team_alias(connection: sqlite3.Connection, alias: str, team_id: int) -> None:
    """Create or update a lookup alias for a canonical team.

    Args:
        connection: Open database connection.
        alias: Alternate team spelling.
        team_id: Canonical team identifier.
    """
    connection.execute(
        """INSERT INTO team_alias (alias, team_id) VALUES (?, ?)
        ON CONFLICT(alias) DO UPDATE SET team_id = excluded.team_id""",
        (alias.strip(), team_id),
    )


def find_team_id(connection: sqlite3.Connection, name: str) -> int | None:
    """Find a team ID by exact name or alias (case-insensitive).

    Args:
        connection: Open database connection.
        name: Team or alias name.

    Returns:
        Team ID if present, otherwise ``None``.
    """
    clean = name.strip()
    row = connection.execute(
        """SELECT team_id FROM team WHERE name = ?
        UNION ALL SELECT team_id FROM team_alias WHERE alias = ? LIMIT 1""",
        (clean, clean),
    ).fetchone()
    return int(row["team_id"]) if row else None


def seed_team_aliases(connection: sqlite3.Connection) -> int:
    """Store the configured aliases whose canonical team already exists.

    Args:
        connection: Open database connection.

    Returns:
        Number of aliases written.
    """
    written = 0
    for alias, canonical in TEAM_ALIASES.items():
        team_id = find_team_id(connection, canonical)
        if team_id is not None and find_team_id(connection, alias) in (None, team_id):
            add_team_alias(connection, alias, team_id)
            written += 1
    return written


def get_team(connection: sqlite3.Connection, team_id: int) -> sqlite3.Row | None:
    """Return one team row.

    Args:
        connection: Open database connection.
        team_id: Team identifier.

    Returns:
        Team row, or ``None``.
    """
    return connection.execute(
        "SELECT * FROM team WHERE team_id = ?", (team_id,)
    ).fetchone()


def get_teams(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    """List teams sorted by display name.

    Args:
        connection: Open database connection.

    Returns:
        Team rows.
    """
    return connection.execute("SELECT * FROM team ORDER BY name").fetchall()


def get_team_aliases(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    """List every alias with its team.

    Args:
        connection: Open database connection.

    Returns:
        Rows with ``alias``, ``team_id`` and ``team``.
    """
    return connection.execute(
        """SELECT a.alias, a.team_id, t.name AS team FROM team_alias AS a
        JOIN team AS t ON t.team_id = a.team_id ORDER BY t.name, a.alias"""
    ).fetchall()


def merge_teams(connection: sqlite3.Connection, from_id: int, into_id: int) -> None:
    """Merge a duplicate team into a canonical one and keep its name as alias.

    Args:
        connection: Open database connection.
        from_id: Duplicate team to remove.
        into_id: Canonical team that receives matches and aliases.

    Raises:
        ValueError: If both IDs are the same or a team does not exist.
        sqlite3.IntegrityError: If merging would duplicate a fixture.
    """
    if from_id == into_id:
        raise ValueError("Cannot merge a team into itself")
    duplicate = get_team(connection, from_id)
    if duplicate is None or get_team(connection, into_id) is None:
        raise ValueError("Team not found")
    connection.execute(
        "UPDATE match SET home_team_id = ? WHERE home_team_id = ?", (into_id, from_id)
    )
    connection.execute(
        "UPDATE match SET away_team_id = ? WHERE away_team_id = ?", (into_id, from_id)
    )
    connection.execute(
        "UPDATE team_alias SET team_id = ? WHERE team_id = ?", (into_id, from_id)
    )
    connection.execute("DELETE FROM team WHERE team_id = ?", (from_id,))
    add_team_alias(connection, duplicate["name"], into_id)


# --- Competitions ------------------------------------------------------------


def add_competition(
    connection: sqlite3.Connection,
    code: str,
    name: str,
    provider: str | None = None,
    auto_sync: bool = False,
) -> int:
    """Create a competition, or return the existing one with the same code.

    Args:
        connection: Open database connection.
        code: Short unique code, e.g. ``BSA`` or ``CLI``.
        name: Display name.
        provider: Data provider name, or ``None`` for manual competitions.
        auto_sync: Whether all fixtures are synced automatically.

    Returns:
        Competition identifier.
    """
    return _one_id(
        connection,
        """INSERT INTO competition (code, name, provider, auto_sync)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(code) DO UPDATE SET
            provider = COALESCE(competition.provider, excluded.provider)
        RETURNING competition_id""",
        (code.strip().upper(), name.strip(), provider, int(auto_sync)),
    )


def get_competition(connection: sqlite3.Connection, code: str) -> sqlite3.Row | None:
    """Find a competition by code.

    Args:
        connection: Open database connection.
        code: Competition code.

    Returns:
        Competition row, or ``None``.
    """
    return connection.execute(
        "SELECT * FROM competition WHERE code = ?", (code.strip(),)
    ).fetchone()


def get_competition_by_id(
    connection: sqlite3.Connection, competition_id: int
) -> sqlite3.Row | None:
    """Find a competition by ID.

    Args:
        connection: Open database connection.
        competition_id: Competition identifier.

    Returns:
        Competition row, or ``None``.
    """
    return connection.execute(
        "SELECT * FROM competition WHERE competition_id = ?", (competition_id,)
    ).fetchone()


def get_competitions(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    """List competitions with their match counts.

    Args:
        connection: Open database connection.

    Returns:
        Competition rows with a ``matches`` column.
    """
    return connection.execute(
        """SELECT c.*, COUNT(m.match_id) AS matches FROM competition AS c
        LEFT JOIN match AS m ON m.competition_id = c.competition_id
        GROUP BY c.competition_id ORDER BY c.auto_sync DESC, c.name"""
    ).fetchall()


def set_competition_auto_sync(
    connection: sqlite3.Connection, competition_id: int, enabled: bool
) -> None:
    """Turn full automatic syncing on or off for a competition.

    Args:
        connection: Open database connection.
        competition_id: Competition identifier.
        enabled: New ``auto_sync`` value.
    """
    connection.execute(
        "UPDATE competition SET auto_sync = ? WHERE competition_id = ?",
        (int(enabled), competition_id),
    )


# --- Matches -----------------------------------------------------------------


def add_match(
    connection: sqlite3.Connection,
    match_date: str,
    home_team_id: int,
    away_team_id: int,
    home_goals: int | None = None,
    away_goals: int | None = None,
    *,
    competition_id: int | None = None,
    kickoff_at: str | None = None,
    status: str | None = None,
    source: str = "manual",
    external_id: str | None = None,
    round_number: int | None = None,
) -> int:
    """Create a match and return its ID.

    Args:
        connection: Open database connection.
        match_date: ISO match date (local calendar date).
        home_team_id: Home team identifier.
        away_team_id: Away team identifier.
        home_goals: Actual home goals or ``None``.
        away_goals: Actual away goals or ``None``.
        competition_id: Competition identifier.
        kickoff_at: UTC kickoff as ``YYYY-MM-DD HH:MM:SS``.
        status: Match status; inferred from the goals when omitted.
        source: ``api``, ``manual`` or ``import``.
        external_id: Provider match ID.
        round_number: Round or matchday number.

    Returns:
        Match identifier.
    """
    if status is None:
        status = "SCHEDULED" if home_goals is None else "FINISHED"
    return _one_id(
        connection,
        """INSERT INTO match
        (match_date, home_team_id, away_team_id, home_goals, away_goals,
         competition_id, kickoff_at, status, source, external_id, round)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING match_id""",
        (
            match_date,
            home_team_id,
            away_team_id,
            home_goals,
            away_goals,
            competition_id,
            kickoff_at,
            status,
            source,
            external_id,
            round_number,
        ),
    )


def upsert_match(
    connection: sqlite3.Connection,
    match_date: str,
    home_team_id: int,
    away_team_id: int,
    home_goals: int | None,
    away_goals: int | None,
    competition_id: int | None = None,
) -> int:
    """Create or update a match by its fixture identity (used by the importer).

    Args:
        connection: Open database connection.
        match_date: ISO match date.
        home_team_id: Home team identifier.
        away_team_id: Away team identifier.
        home_goals: Actual home goals or ``None``.
        away_goals: Actual away goals or ``None``.
        competition_id: Competition for newly created matches.

    Returns:
        Match identifier.
    """
    status = "SCHEDULED" if home_goals is None else "FINISHED"
    return _one_id(
        connection,
        """INSERT INTO match
        (match_date, home_team_id, away_team_id, home_goals, away_goals,
         competition_id, status, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'import')
        ON CONFLICT(match_date, home_team_id, away_team_id) DO UPDATE SET
            home_goals = excluded.home_goals, away_goals = excluded.away_goals,
            status = CASE WHEN excluded.home_goals IS NOT NULL THEN 'FINISHED'
                          WHEN match.status = 'FINISHED' THEN 'SCHEDULED'
                          ELSE match.status END
        RETURNING match_id""",
        (
            match_date,
            home_team_id,
            away_team_id,
            home_goals,
            away_goals,
            competition_id,
            status,
        ),
    )


def update_match_fields(
    connection: sqlite3.Connection, match_id: int, fields: Mapping[str, object]
) -> None:
    """Update selected match columns and stamp ``updated_at``.

    Column names are checked against a whitelist; values are parameterised.

    Args:
        connection: Open database connection.
        match_id: Match identifier.
        fields: Column-to-value mapping.

    Raises:
        ValueError: If a column is not editable.
    """
    unknown = set(fields) - MATCH_UPDATABLE
    if unknown:
        raise ValueError(f"Columns not editable: {', '.join(sorted(unknown))}")
    if not fields:
        return
    columns = sorted(fields)
    assignments = ", ".join(f"{column} = ?" for column in columns)
    connection.execute(
        f"UPDATE match SET {assignments}, updated_at = CURRENT_TIMESTAMP "
        "WHERE match_id = ?",
        [fields[column] for column in columns] + [match_id],
    )


def update_match(
    connection: sqlite3.Connection,
    match_id: int,
    match_date: str,
    home_team_id: int,
    away_team_id: int,
    home_goals: int | None,
    away_goals: int | None,
) -> None:
    """Update the core match attributes.

    Args:
        connection: Open database connection.
        match_id: Match identifier.
        match_date: ISO match date.
        home_team_id: Home team identifier.
        away_team_id: Away team identifier.
        home_goals: Actual home goals or ``None``.
        away_goals: Actual away goals or ``None``.
    """
    update_match_fields(
        connection,
        match_id,
        {
            "match_date": match_date,
            "home_team_id": home_team_id,
            "away_team_id": away_team_id,
            "home_goals": home_goals,
            "away_goals": away_goals,
        },
    )


def delete_match(connection: sqlite3.Connection, match_id: int) -> None:
    """Delete a match and its bets through the cascade constraint.

    Args:
        connection: Open database connection.
        match_id: Match identifier.
    """
    connection.execute("DELETE FROM match WHERE match_id = ?", (match_id,))


def get_match(connection: sqlite3.Connection, match_id: int) -> sqlite3.Row | None:
    """Return one match with team and competition names.

    Args:
        connection: Open database connection.
        match_id: Match identifier.

    Returns:
        Match row, or ``None``.
    """
    return connection.execute(
        MATCH_SELECT + " WHERE m.match_id = ?", (match_id,)
    ).fetchone()


def find_match_by_external(
    connection: sqlite3.Connection, external_id: str
) -> sqlite3.Row | None:
    """Find a synced match by provider ID.

    Args:
        connection: Open database connection.
        external_id: Provider match ID.

    Returns:
        Match row, or ``None``.
    """
    return connection.execute(
        MATCH_SELECT + " WHERE m.source = 'api' AND m.external_id = ?",
        (external_id,),
    ).fetchone()


def find_reconcile_candidate(
    connection: sqlite3.Connection,
    home_team_id: int,
    away_team_id: int,
    match_date: str,
    tolerance_days: int = 1,
    score: tuple[int, int] | None = None,
) -> sqlite3.Row | None:
    """Find a legacy match (no provider ID) for the same fixture near a date.

    Args:
        connection: Open database connection.
        home_team_id: Home team identifier.
        away_team_id: Away team identifier.
        match_date: ISO date reported by the provider.
        tolerance_days: Allowed date difference in days.
        score: When given, only legacy matches with this exact score qualify.

    Returns:
        Closest matching legacy match row, or ``None``.
    """
    score_filter = ""
    params: list[object] = [home_team_id, away_team_id, match_date, tolerance_days]
    if score is not None:
        score_filter = " AND m.home_goals = ? AND m.away_goals = ?"
        params.extend(score)
    params.append(match_date)
    return connection.execute(
        MATCH_SELECT
        + """ WHERE m.external_id IS NULL AND m.home_team_id = ?
        AND m.away_team_id = ?
        AND ABS(julianday(m.match_date) - julianday(?)) <= ?"""
        + score_filter
        + """ ORDER BY ABS(julianday(m.match_date) - julianday(?)) LIMIT 1""",
        params,
    ).fetchone()


def get_matches(
    connection: sqlite3.Connection,
    match_date: str | None = None,
    pending_only: bool = False,
) -> list[sqlite3.Row]:
    """List matches with team names, newest first.

    Args:
        connection: Open database connection.
        match_date: Optional ISO date filter.
        pending_only: Limit results to unscored matches.

    Returns:
        Match rows.
    """
    return connection.execute(
        MATCH_SELECT
        + """ WHERE (? IS NULL OR m.match_date = ?)
        AND (? = 0 OR m.home_goals IS NULL)
        ORDER BY m.match_date DESC, home.name, away.name""",
        (match_date, match_date, int(pending_only)),
    ).fetchall()


def list_matches(
    connection: sqlite3.Connection,
    date_from: str | None = None,
    date_to: str | None = None,
    competition: str | None = None,
    round_number: int | None = None,
    bettor_id: int | None = None,
    limit: int | None = None,
) -> list[sqlite3.Row]:
    """List matches in chronological order with an optional bettor's bet.

    Args:
        connection: Open database connection.
        date_from: First ISO date (inclusive).
        date_to: Last ISO date (inclusive).
        competition: Competition code filter.
        round_number: Round filter.
        bettor_id: Bettor whose ``pred_home``/``pred_away`` columns are joined.
        limit: Maximum rows.

    Returns:
        Match rows with ``pred_home``/``pred_away`` (``NULL`` when no bet).
    """
    return connection.execute(
        """SELECT x.*, b.pred_home, b.pred_away FROM ("""
        + MATCH_SELECT
        + f""") AS x
        LEFT JOIN bet AS b ON b.match_id = x.match_id AND b.bettor_id = ?
        WHERE (? IS NULL OR x.match_date >= ?) AND (? IS NULL OR x.match_date <= ?)
        AND (? IS NULL OR x.competition_code = ?) AND (? IS NULL OR x.round = ?)
        ORDER BY {PLAYED_AT.replace("m.", "x.")}, x.match_id
        LIMIT ?""",
        (
            bettor_id,
            date_from,
            date_from,
            date_to,
            date_to,
            competition,
            competition,
            round_number,
            round_number,
            -1 if limit is None else limit,
        ),
    ).fetchall()


def upcoming_matches(
    connection: sqlite3.Connection,
    today: str,
    bettor_id: int | None = None,
    limit: int | None = None,
) -> list[sqlite3.Row]:
    """List not-yet-played matches from a local date onwards.

    Args:
        connection: Open database connection.
        today: Local ISO date; earlier matches are excluded.
        bettor_id: Bettor whose prediction columns are joined.
        limit: Maximum rows.

    Returns:
        Match rows in kickoff order (callers still check the bet lock).
    """
    return connection.execute(
        """SELECT x.*, b.pred_home, b.pred_away FROM ("""
        + MATCH_SELECT
        + f""") AS x
        LEFT JOIN bet AS b ON b.match_id = x.match_id AND b.bettor_id = ?
        WHERE x.match_date >= ? AND x.status IN ('SCHEDULED', 'POSTPONED')
        AND x.home_goals IS NULL
        ORDER BY {PLAYED_AT.replace("m.", "x.")}, x.match_id LIMIT ?""",
        (bettor_id, today, -1 if limit is None else limit),
    ).fetchall()


def recent_results(
    connection: sqlite3.Connection, limit: int = 5, competition: str | None = None
) -> list[sqlite3.Row]:
    """List the most recent finished matches, newest first.

    Args:
        connection: Open database connection.
        limit: Maximum rows.
        competition: Optional competition code.

    Returns:
        Match rows.
    """
    return connection.execute(
        MATCH_SELECT
        + f""" WHERE m.home_goals IS NOT NULL AND m.status <> 'CANCELLED'
        AND (? IS NULL OR COALESCE(c.code, 'MANUAL') = ?)
        ORDER BY {PLAYED_AT} DESC, m.match_id DESC LIMIT ?""",
        (competition, competition, limit),
    ).fetchall()


def set_match_result(
    connection: sqlite3.Connection,
    match_id: int,
    home_goals: int | None,
    away_goals: int | None,
    lock: bool = False,
) -> None:
    """Save (or clear) the final score of a match.

    Args:
        connection: Open database connection.
        match_id: Match identifier.
        home_goals: Final home goals, or ``None`` to clear the result.
        away_goals: Final away goals, or ``None`` to clear the result.
        lock: Also set ``admin_locked`` so sync will not overwrite the result.
    """
    fields: dict[str, object] = {
        "home_goals": home_goals,
        "away_goals": away_goals,
        "status": "SCHEDULED" if home_goals is None else "FINISHED",
    }
    if lock:
        fields["admin_locked"] = 1
    update_match_fields(connection, match_id, fields)


def matches_in_progress(
    connection: sqlite3.Connection, since: str, until: str
) -> list[sqlite3.Row]:
    """List provider-backed matches that kicked off in a window and are unfinished.

    Args:
        connection: Open database connection.
        since: Earliest UTC kickoff text.
        until: Latest UTC kickoff text.

    Returns:
        Match rows with competition columns.
    """
    return connection.execute(
        MATCH_SELECT
        + """ WHERE m.kickoff_at BETWEEN ? AND ? AND m.source = 'api'
        AND m.status NOT IN ('FINISHED', 'CANCELLED') AND m.admin_locked = 0
        ORDER BY m.kickoff_at""",
        (since, until),
    ).fetchall()


def tracked_unfinished_matches(
    connection: sqlite3.Connection, until: str
) -> list[sqlite3.Row]:
    """List single API matches outside auto-synced competitions still unfinished.

    Args:
        connection: Open database connection.
        until: Only matches that kicked off before this UTC text.

    Returns:
        Match rows.
    """
    return connection.execute(
        MATCH_SELECT
        + """ WHERE m.source = 'api' AND COALESCE(c.auto_sync, 0) = 0
        AND m.status NOT IN ('FINISHED', 'CANCELLED') AND m.admin_locked = 0
        AND m.kickoff_at <= ? ORDER BY m.kickoff_at""",
        (until,),
    ).fetchall()


def matches_kicking_off(
    connection: sqlite3.Connection, start: str, end: str
) -> list[sqlite3.Row]:
    """List matches with a kickoff in ``(start, end]``.

    Args:
        connection: Open database connection.
        start: Exclusive UTC start text.
        end: Inclusive UTC end text.

    Returns:
        Match rows.
    """
    return connection.execute(
        MATCH_SELECT
        + """ WHERE m.kickoff_at > ? AND m.kickoff_at <= ?
        AND m.status IN ('SCHEDULED', 'POSTPONED') ORDER BY m.kickoff_at""",
        (start, end),
    ).fetchall()


def finished_history(
    connection: sqlite3.Connection,
    before: str,
    since: str,
    exclude_match_id: int | None = None,
) -> list[sqlite3.Row]:
    """List finished matches played in ``[since, before)`` for Corvo.

    Args:
        connection: Open database connection.
        before: Exclusive upper bound, ``YYYY-MM-DD HH:MM:SS`` UTC.
        since: Inclusive lower bound, same format.
        exclude_match_id: Match to leave out (the one being predicted).

    Returns:
        Rows with ``played_at``, team IDs and goals, oldest first.
    """
    return connection.execute(
        f"""SELECT m.match_id, {PLAYED_AT} AS played_at, m.home_team_id,
        m.away_team_id, m.home_goals, m.away_goals FROM match AS m
        WHERE m.home_goals IS NOT NULL AND m.status = 'FINISHED'
        AND {PLAYED_AT} < ? AND {PLAYED_AT} >= ? AND m.match_id IS NOT ?
        ORDER BY played_at, m.match_id""",
        (before, since, exclude_match_id),
    ).fetchall()


# --- Bets --------------------------------------------------------------------


def upsert_bet(
    connection: sqlite3.Connection,
    match_id: int,
    bettor_id: int,
    pred_home: int,
    pred_away: int,
    source: str = "user",
) -> int:
    """Create or update a bettor's prediction for a match.

    Args:
        connection: Open database connection.
        match_id: Match identifier.
        bettor_id: Bettor identifier.
        pred_home: Predicted home goals.
        pred_away: Predicted away goals.
        source: ``user``, ``auto`` or ``admin``.

    Returns:
        Bet identifier.
    """
    return _one_id(
        connection,
        """INSERT INTO bet (match_id, bettor_id, pred_home, pred_away, source)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(match_id, bettor_id) DO UPDATE SET
            pred_home = excluded.pred_home, pred_away = excluded.pred_away,
            source = excluded.source, updated_at = CURRENT_TIMESTAMP
        RETURNING bet_id""",
        (match_id, bettor_id, pred_home, pred_away, source),
    )


def get_bet(
    connection: sqlite3.Connection, match_id: int, bettor_id: int
) -> sqlite3.Row | None:
    """Return one bettor's bet on a match.

    Args:
        connection: Open database connection.
        match_id: Match identifier.
        bettor_id: Bettor identifier.

    Returns:
        Bet row, or ``None``.
    """
    return connection.execute(
        "SELECT * FROM bet WHERE match_id = ? AND bettor_id = ?",
        (match_id, bettor_id),
    ).fetchone()


def delete_bet(connection: sqlite3.Connection, match_id: int, bettor_id: int) -> None:
    """Delete one bettor's bet on a match.

    Args:
        connection: Open database connection.
        match_id: Match identifier.
        bettor_id: Bettor identifier.
    """
    connection.execute(
        "DELETE FROM bet WHERE match_id = ? AND bettor_id = ?", (match_id, bettor_id)
    )


def delete_auto_bets(connection: sqlite3.Connection, match_id: int) -> None:
    """Delete the automatic (non-admin) bets of the animal bettors on a match.

    Args:
        connection: Open database connection.
        match_id: Match identifier.
    """
    connection.execute(
        """DELETE FROM bet WHERE match_id = ? AND source = 'auto'
        AND bettor_id IN (SELECT bettor_id FROM bettor WHERE kind <> 'human')""",
        (match_id,),
    )


def bets_for_match(connection: sqlite3.Connection, match_id: int) -> list[sqlite3.Row]:
    """List every bet on a match with its score (``NULL`` while pending).

    Args:
        connection: Open database connection.
        match_id: Match identifier.

    Returns:
        ``v_bet_score`` rows: Jair, Macaco, Caranguejo, Corvo.
    """
    return connection.execute(
        f"""SELECT * FROM v_bet_score WHERE match_id = ?
        ORDER BY {BET_ORDER}, bettor""",
        (match_id,),
    ).fetchall()


def matches_with_bettor_bet(
    connection: sqlite3.Connection, bettor_id: int
) -> list[int]:
    """List the IDs of matches a bettor has bet on, in chronological order.

    Args:
        connection: Open database connection.
        bettor_id: Bettor identifier.

    Returns:
        Match IDs.
    """
    rows = connection.execute(
        f"""SELECT m.match_id FROM match AS m JOIN bet AS b
        ON b.match_id = m.match_id AND b.bettor_id = ?
        ORDER BY {PLAYED_AT}, m.match_id""",
        (bettor_id,),
    ).fetchall()
    return [int(row["match_id"]) for row in rows]


# --- Ranking and scoring -----------------------------------------------------


def get_ranking(
    connection: sqlite3.Connection,
    competition: str | None = None,
    month: str | None = None,
    season: str | None = None,
) -> list[sqlite3.Row]:
    """Return the scored-bets ranking, optionally filtered.

    Args:
        connection: Open database connection.
        competition: Competition code filter.
        month: ``YYYY-MM`` filter on the match date.
        season: ``YYYY`` filter on the match date.

    Returns:
        Ranking rows with the ``v_ranking`` columns.
    """
    if competition is None and month is None and season is None:
        return connection.execute(
            f"SELECT * FROM v_ranking ORDER BY {RANKING_ORDER}"
        ).fetchall()
    return connection.execute(
        f"""SELECT bettor, COUNT(*) AS matches, SUM(winner_hit) AS winner_hits,
        SUM(exact_hit) AS exact_hits,
        ROUND(100.0 * SUM(winner_hit) / COUNT(*), 1) AS pct_winner,
        ROUND(100.0 * SUM(exact_hit) / COUNT(*), 1) AS pct_exact,
        SUM(points) AS points,
        RANK() OVER (ORDER BY SUM(points) DESC) AS rank
        FROM v_bet_score WHERE points IS NOT NULL
        AND (? IS NULL OR competition = ?)
        AND (? IS NULL OR substr(match_date, 1, 7) = ?)
        AND (? IS NULL OR substr(match_date, 1, 4) = ?)
        GROUP BY bettor ORDER BY {RANKING_ORDER}""",
        (competition, competition, month, month, season, season),
    ).fetchall()


def get_scoring_config(connection: sqlite3.Connection) -> sqlite3.Row:
    """Return the single scoring-configuration row.

    Args:
        connection: Open database connection.

    Returns:
        Scoring configuration row.

    Raises:
        RuntimeError: If the row is missing.
    """
    row = connection.execute(
        "SELECT * FROM scoring_config WHERE config_id = 1"
    ).fetchone()
    if row is None:
        raise RuntimeError("Scoring configuration is missing")
    return row


def update_scoring_config(
    connection: sqlite3.Connection, points_winner: int, bonus_exact: int
) -> None:
    """Update the global scoring configuration.

    Args:
        connection: Open database connection.
        points_winner: Points for a correct outcome.
        bonus_exact: Bonus points for an exact scoreline.
    """
    connection.execute(
        """UPDATE scoring_config SET points_winner = ?, bonus_exact = ?
        WHERE config_id = 1""",
        (points_winner, bonus_exact),
    )


# --- Audit and sync logs -----------------------------------------------------


def log_audit(
    connection: sqlite3.Connection,
    actor_telegram_id: int | None,
    action: str,
    entity: str,
    entity_id: int | None,
    before: Mapping[str, Any] | None = None,
    after: Mapping[str, Any] | None = None,
) -> None:
    """Record an admin change.

    Args:
        connection: Open database connection.
        actor_telegram_id: Telegram user who made the change.
        action: Short verb such as ``update`` or ``delete``.
        entity: Table or concept changed, e.g. ``match``.
        entity_id: Identifier of the changed row.
        before: State before the change.
        after: State after the change.
    """
    connection.execute(
        """INSERT INTO audit_log
        (actor_telegram_id, action, entity, entity_id, before_json, after_json)
        VALUES (?, ?, ?, ?, ?, ?)""",
        (
            actor_telegram_id,
            action,
            entity,
            entity_id,
            None if before is None else json.dumps(dict(before), default=str),
            None if after is None else json.dumps(dict(after), default=str),
        ),
    )


def start_sync_run(connection: sqlite3.Connection, competition_code: str) -> int:
    """Insert a running sync log entry.

    Args:
        connection: Open database connection.
        competition_code: Competition being synced.

    Returns:
        Sync run identifier.
    """
    return _one_id(
        connection,
        "INSERT INTO sync_run (competition_code) VALUES (?) RETURNING run_id",
        (competition_code,),
    )


def finish_sync_run(
    connection: sqlite3.Connection,
    run_id: int,
    status: str,
    fetched: int = 0,
    created: int = 0,
    updated: int = 0,
    error: str | None = None,
) -> None:
    """Close a sync log entry.

    Args:
        connection: Open database connection.
        run_id: Sync run identifier.
        status: ``ok`` or ``error``.
        fetched: Matches received from the provider.
        created: Matches created.
        updated: Matches updated or linked.
        error: Error message when the run failed.
    """
    connection.execute(
        """UPDATE sync_run SET finished_at = CURRENT_TIMESTAMP, status = ?,
        fetched = ?, created = ?, updated = ?, error = ? WHERE run_id = ?""",
        (status, fetched, created, updated, error, run_id),
    )


def recent_sync_runs(
    connection: sqlite3.Connection, limit: int = 10
) -> list[sqlite3.Row]:
    """List the latest sync runs, newest first.

    Args:
        connection: Open database connection.
        limit: Maximum rows.

    Returns:
        Sync run rows.
    """
    return connection.execute(
        "SELECT * FROM sync_run ORDER BY run_id DESC LIMIT ?", (limit,)
    ).fetchall()


def query_rows(
    connection: sqlite3.Connection,
    sql: str,
    values: Iterable[object] = (),
) -> list[sqlite3.Row]:
    """Run a read-only parameterised query used by reporting code.

    Args:
        connection: Open database connection.
        sql: SQL query with placeholders.
        values: Values for placeholders.

    Returns:
        Query result rows.
    """
    return connection.execute(sql, tuple(values)).fetchall()
