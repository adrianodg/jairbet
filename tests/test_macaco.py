"""Tests for Macaco's automatic random baseline predictions."""

import random

from jairbet.bettors import place_auto_bets
from jairbet.bettors.macaco import random_prediction
from jairbet.config import JAIR_NAME, MACACO_NAME
from jairbet.db import connect, init_db
from jairbet.repository import add_bettor, add_match, add_team, upsert_bet


def test_random_prediction_stays_within_zero_to_three() -> None:
    """Macaco's score generator must only use the intended inclusive range."""
    generator = random.Random(10)
    for _ in range(50):
        home, away = random_prediction(generator)
        assert 0 <= home <= 3
        assert 0 <= away <= 3


def test_macaco_bet_is_persisted_without_rerolling() -> None:
    """Automatic predictions are created once and remain fixed afterwards."""
    connection = connect()
    init_db(connection)
    home = add_team(connection, "Home")
    away = add_team(connection, "Away")
    match_id = add_match(connection, "2026-01-01", home, away)
    upsert_bet(connection, match_id, add_bettor(connection, JAIR_NAME), 1, 0)
    first = place_auto_bets(connection, match_id, rng=random.Random(1))
    upsert_bet(connection, match_id, add_bettor(connection, JAIR_NAME), 3, 3)
    second = place_auto_bets(connection, match_id, rng=random.Random(2))
    assert first[MACACO_NAME] == second[MACACO_NAME]
    row = connection.execute(
        """SELECT pred_home, pred_away, source FROM bet
        JOIN bettor USING (bettor_id) WHERE bettor.name = ?""",
        (MACACO_NAME,),
    ).fetchone()
    assert (row["pred_home"], row["pred_away"]) == first[MACACO_NAME]
    assert row["source"] == "auto"
    connection.close()
