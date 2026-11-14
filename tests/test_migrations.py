"""Tests for upgrading a v1 database to the current schema."""

import sqlite3
from pathlib import Path

from jairbet.config import MIGRATIONS_DIR
from jairbet.db import connect, latest_version, migrate
from jairbet.repository import get_ranking


def _v1_database(path: Path) -> None:
    """Create a v1-style database (no user_version) with two scored bets.

    Args:
        path: Database file to create.
    """
    connection = sqlite3.connect(path)
    connection.executescript((MIGRATIONS_DIR / "001_initial.sql").read_text())
    connection.executescript(
        """INSERT INTO bettor (name) VALUES ('Jair'), ('Macaco');
        INSERT INTO team (name) VALUES ('A'), ('B');
        INSERT INTO match (match_date, home_team_id, away_team_id,
                           home_goals, away_goals)
        VALUES ('2026-01-01', 1, 2, 2, 1), ('2026-01-08', 2, 1, NULL, NULL);
        INSERT INTO bet (match_id, bettor_id, pred_home, pred_away)
        VALUES (1, 1, 2, 1), (1, 2, 0, 0), (2, 1, 1, 1);"""
    )
    connection.commit()
    connection.close()


def test_v1_database_migrates_with_unchanged_ranking(tmp_path: Path) -> None:
    """The upgrade keeps every score and seeds the new bettors."""
    database = tmp_path / "v1.db"
    _v1_database(database)
    with connect(database) as connection:
        before = [tuple(row) for row in get_ranking(connection)]
    connection = connect(database)
    try:
        assert migrate(connection) == latest_version()
        after = [tuple(row) for row in get_ranking(connection)]
        kinds = {
            row["name"]: row["kind"]
            for row in connection.execute("SELECT name, kind FROM bettor")
        }
        statuses = [
            row["status"]
            for row in connection.execute("SELECT status FROM match ORDER BY match_id")
        ]
        macaco_source = connection.execute(
            "SELECT source FROM bet WHERE bettor_id = 2"
        ).fetchone()["source"]
        competition = connection.execute(
            """SELECT DISTINCT c.code FROM match JOIN competition AS c
            USING (competition_id)"""
        ).fetchall()
    finally:
        connection.close()
    assert after == before
    assert kinds == {
        "Jair": "human",
        "Macaco": "random",
        "Caranguejo": "inverse",
        "Corvo": "model",
    }
    assert statuses == ["FINISHED", "SCHEDULED"]
    assert macaco_source == "auto"
    assert [row["code"] for row in competition] == ["MANUAL"]
    assert list(tmp_path.glob("v1.pre-v*.db"))


def test_migrate_is_idempotent() -> None:
    """Running the migrations twice changes nothing."""
    connection = connect()
    assert migrate(connection) == latest_version()
    assert migrate(connection) == latest_version()
    assert connection.execute("SELECT COUNT(*) FROM bettor").fetchone()[0] == 4
    connection.close()
