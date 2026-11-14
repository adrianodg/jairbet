"""Constraint and cascade behaviour tests for the repository layer."""

import sqlite3

import pytest

from jairbet.db import connect, init_db
from jairbet.repository import (
    add_bettor,
    add_match,
    add_team,
    delete_match,
    get_matches,
)


@pytest.fixture()
def database() -> sqlite3.Connection:
    """Provide an initialised in-memory database.

    Yields:
        Open Jairbet database connection.
    """
    connection = connect()
    init_db(connection)
    yield connection
    connection.close()


def test_duplicate_fixture_is_rejected(database: sqlite3.Connection) -> None:
    """The fixture unique key must reject a duplicate date and teams."""
    home, away = add_team(database, "Home"), add_team(database, "Away")
    add_match(database, "2026-01-01", home, away)
    with pytest.raises(sqlite3.IntegrityError):
        add_match(database, "2026-01-01", home, away)


def test_duplicate_bet_is_rejected(database: sqlite3.Connection) -> None:
    """The raw schema must reject two bets from the same bettor per match."""
    home, away = add_team(database, "Home"), add_team(database, "Away")
    match = add_match(database, "2026-01-01", home, away)
    bettor = add_bettor(database, "Jair")
    database.execute(
        """INSERT INTO bet (match_id, bettor_id, pred_home, pred_away)
        VALUES (?, ?, ?, ?)""",
        (match, bettor, 1, 0),
    )
    with pytest.raises(sqlite3.IntegrityError):
        database.execute(
            """INSERT INTO bet (match_id, bettor_id, pred_home, pred_away)
            VALUES (?, ?, ?, ?)""",
            (match, bettor, 2, 0),
        )


def test_negative_goals_are_rejected(database: sqlite3.Connection) -> None:
    """Scores cannot contain a negative goal count."""
    home, away = add_team(database, "Home"), add_team(database, "Away")
    with pytest.raises(sqlite3.IntegrityError):
        add_match(database, "2026-01-01", home, away, -1, 0)


def test_deleting_match_cascades_to_bets(database: sqlite3.Connection) -> None:
    """Deleting a match removes dependent bets."""
    home, away = add_team(database, "Home"), add_team(database, "Away")
    match = add_match(database, "2026-01-01", home, away)
    bettor = add_bettor(database, "Jair")
    database.execute(
        """INSERT INTO bet (match_id, bettor_id, pred_home, pred_away)
        VALUES (?, ?, ?, ?)""",
        (match, bettor, 1, 0),
    )
    delete_match(database, match)
    assert database.execute("SELECT COUNT(*) FROM bet").fetchone()[0] == 0


def test_matches_are_listed_most_recent_first(database: sqlite3.Connection) -> None:
    """Fixture lists should put the newest date first for the app's workflows."""
    home, away = add_team(database, "Home"), add_team(database, "Away")
    add_match(database, "2026-01-01", home, away)
    add_match(database, "2026-02-01", home, away)
    assert [row["match_date"] for row in get_matches(database)] == [
        "2026-02-01",
        "2026-01-01",
    ]
