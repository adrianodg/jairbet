"""Tests for pure scoring rules and their SQL-view equivalent."""

import pytest

from jairbet.db import connect, init_db
from jairbet.repository import add_bettor, add_match, add_team, upsert_bet
from jairbet.scoring import score_prediction


@pytest.mark.parametrize(
    ("prediction", "actual", "winner", "exact", "points"),
    [
        ((2, 1), (1, 0), True, False, 3),
        ((0, 2), (1, 2), True, False, 3),
        ((1, 1), (0, 0), True, False, 3),
        ((2, 1), (2, 1), True, True, 6),
        ((0, 0), (0, 0), True, True, 6),
        ((1, 0), (None, None), None, None, None),
    ],
)
def test_score_prediction(
    prediction: tuple[int, int],
    actual: tuple[int | None, int | None],
    winner: bool | None,
    exact: bool | None,
    points: int | None,
) -> None:
    """Score all important result shapes, including pending matches."""
    result = score_prediction(*prediction, *actual)
    assert (result.winner_hit, result.exact_hit, result.points) == (
        winner,
        exact,
        points,
    )


@pytest.mark.parametrize(
    ("prediction", "actual"),
    [((2, 1), (1, 0)), ((1, 1), (0, 0)), ((0, 2), (1, 2)), ((2, 1), (2, 1))],
)
def test_sql_view_matches_pure_scoring(
    prediction: tuple[int, int], actual: tuple[int, int]
) -> None:
    """Ensure SQL sign logic remains identical to the Python implementation."""
    connection = connect()
    init_db(connection)
    home = add_team(connection, "Home")
    away = add_team(connection, "Away")
    bettor = add_bettor(connection, "Jair")
    match = add_match(connection, "2026-01-01", home, away, *actual)
    upsert_bet(connection, match, bettor, *prediction)
    row = connection.execute("SELECT * FROM v_bet_score").fetchone()
    expected = score_prediction(*prediction, *actual)
    assert row is not None
    assert (row["winner_hit"], row["exact_hit"], row["points"]) == (
        int(expected.winner_hit),
        int(expected.exact_hit),
        expected.points,
    )
    connection.close()
