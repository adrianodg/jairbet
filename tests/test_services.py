"""Tests for bot use cases: bet locking, admin edits and the audit log."""

import sqlite3
from datetime import date, datetime, time, timezone

import pytest

from jairbet.config import CORVO_NAME, JAIR_NAME
from jairbet.db import connect, init_db
from jairbet.repository import (
    add_match,
    add_team,
    bets_for_match,
    get_bettors,
    get_match,
)
from jairbet.services import (
    ServiceError,
    create_manual_match,
    edit_match,
    parse_date_text,
    parse_time_text,
    remove_bet,
    save_bet,
    search_teams,
    set_alias,
    set_result,
)

BEFORE = datetime(2026, 11, 15, 18, 0, tzinfo=timezone.utc)
AFTER = datetime(2026, 11, 15, 19, 0, tzinfo=timezone.utc)


@pytest.fixture()
def database() -> sqlite3.Connection:
    """Provide a database with one match kicking off at 19:00 UTC.

    Yields:
        Open database connection.
    """
    connection = connect()
    init_db(connection)
    add_match(
        connection,
        "2026-11-15",
        add_team(connection, "Flamengo"),
        add_team(connection, "Palmeiras"),
        kickoff_at="2026-11-15 19:00:00",
    )
    yield connection
    connection.close()


def _audit_count(connection: sqlite3.Connection) -> int:
    """Count audit rows.

    Args:
        connection: Open database connection.

    Returns:
        Number of audit entries.
    """
    return connection.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]


def test_jair_bets_before_kickoff_and_animals_follow(
    database: sqlite3.Connection,
) -> None:
    """A bet before kickoff is saved with all three animal bets."""
    outcome = save_bet(database, 1, JAIR_NAME, 2, 1, 99, now=BEFORE)
    assert outcome.prediction == (2, 1)
    assert set(outcome.animals) == {"Macaco", "Caranguejo", "Corvo"}
    assert outcome.animals["Caranguejo"] == (1, 2)
    assert len(bets_for_match(database, 1)) == 4
    assert _audit_count(database) == 0


def test_bettors_are_listed_in_display_order(database: sqlite3.Connection) -> None:
    """Bets and bettors always read Jair, Macaco, Caranguejo, Corvo."""
    save_bet(database, 1, JAIR_NAME, 2, 1, 99, now=BEFORE)
    order = ["Jair", "Macaco", "Caranguejo", "Corvo"]
    assert [row["bettor"] for row in bets_for_match(database, 1)] == order
    assert [row["name"] for row in get_bettors(database)] == order


def test_jair_cannot_bet_after_kickoff(database: sqlite3.Connection) -> None:
    """The lock closes bets at kickoff."""
    with pytest.raises(ServiceError, match="fechadas"):
        save_bet(database, 1, JAIR_NAME, 2, 1, 99, now=AFTER)


def test_admin_can_edit_any_bet_after_kickoff(database: sqlite3.Connection) -> None:
    """Admin edits ignore the lock, are audited and survive recomputation."""
    save_bet(database, 1, JAIR_NAME, 2, 1, 99, now=BEFORE)
    save_bet(database, 1, CORVO_NAME, 4, 4, 7, as_admin=True, now=AFTER)
    save_bet(database, 1, JAIR_NAME, 0, 3, 7, as_admin=True, now=AFTER)
    bets = {row["bettor"]: row for row in bets_for_match(database, 1)}
    assert (bets["Corvo"]["pred_home"], bets["Corvo"]["bet_source"]) == (4, "admin")
    assert (bets["Caranguejo"]["pred_home"], bets["Caranguejo"]["pred_away"]) == (3, 0)
    assert _audit_count(database) == 2


def test_removing_jair_bet_removes_automatic_bets(
    database: sqlite3.Connection,
) -> None:
    """Animals only bet where Jair bet."""
    save_bet(database, 1, JAIR_NAME, 1, 1, 99, now=BEFORE)
    remove_bet(database, 1, JAIR_NAME, 7)
    assert bets_for_match(database, 1) == []


def test_result_is_locked_and_reported(database: sqlite3.Connection) -> None:
    """Admin results lock the match and report new results once."""
    assert set_result(database, 1, (2, 0), 7) is True
    assert set_result(database, 1, (2, 0), 7) is False
    match = get_match(database, 1)
    assert (match["status"], match["admin_locked"]) == ("FINISHED", 1)
    set_result(database, 1, None, 7)
    assert get_match(database, 1)["status"] == "SCHEDULED"


def test_manual_match_from_other_championship(database: sqlite3.Connection) -> None:
    """Admin-created matches get a kickoff in UTC and reject duplicates."""
    match_id = create_manual_match(
        database, "CDB", "Santos", "Flamengo", date(2026, 11, 20), time(21, 30), 7
    )
    match = get_match(database, match_id)
    assert match["kickoff_at"] == "2026-11-21 00:30:00"
    assert match["competition_code"] == "CDB"
    assert match["source"] == "manual"
    with pytest.raises(ServiceError, match="já está cadastrado"):
        create_manual_match(
            database, "CDB", "Santos", "Flamengo", date(2026, 11, 20), None, 7
        )
    with pytest.raises(ServiceError):
        create_manual_match(
            database, "CDB", "Santos", "santos", date(2026, 1, 1), None, 7
        )


def test_edit_match_moves_kickoff_and_locks(database: sqlite3.Connection) -> None:
    """Changing the date keeps the local time and locks the match."""
    edit_match(database, 1, "data", "16/11/2026", 7)
    match = get_match(database, 1)
    assert (match["match_date"], match["kickoff_at"]) == (
        "2026-11-16",
        "2026-11-16 19:00:00",
    )
    assert match["admin_locked"] == 1
    edit_match(database, 1, "status", "adiado", 7)
    assert get_match(database, 1)["status"] == "POSTPONED"
    with pytest.raises(ServiceError):
        edit_match(database, 1, "cor", "azul", 7)


def test_alias_merges_duplicate_team(database: sqlite3.Connection) -> None:
    """An auto-created duplicate is merged into the canonical team."""
    duplicate = add_team(database, "CR Flamengo")
    add_match(database, "2026-12-01", duplicate, add_team(database, "Santos"))
    set_alias(database, "CR Flamengo", "Flamengo", 7)
    assert search_teams(database, "CR Flamengo") == [(1, "Flamengo")]
    homes = {
        row["home_team"]
        for row in database.execute(
            "SELECT home.name AS home_team FROM match JOIN team AS home "
            "ON home.team_id = match.home_team_id"
        )
    }
    assert homes == {"Flamengo"}


def test_search_teams_tolerates_typos(database: sqlite3.Connection) -> None:
    """Accents, suffixes and small typos still find the team."""
    assert search_teams(database, "flamengo fc")[0][1] == "Flamengo"
    assert search_teams(database, "Palmeras")[0][1] == "Palmeiras"


@pytest.mark.parametrize(
    ("text", "expected"),
    [("20/11/2026", date(2026, 11, 20)), ("2026-11-20", date(2026, 11, 20))],
)
def test_parse_date_text(text: str, expected: date) -> None:
    """Dates accept the Brazilian and ISO formats."""
    assert parse_date_text(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("21:30", time(21, 30)), ("16h", time(16, 0)), ("-", None)],
)
def test_parse_time_text(text: str, expected: time | None) -> None:
    """Kickoff times accept HH:MM, 16h and '-' for unknown."""
    assert parse_time_text(text) == expected
