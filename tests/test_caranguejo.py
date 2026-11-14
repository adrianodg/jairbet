"""Tests for Caranguejo, the bettor that always bets against Jair."""

import random

import pytest

from jairbet.bettors import place_auto_bets
from jairbet.bettors.caranguejo import caranguejo_rng, invert
from jairbet.config import CARANGUEJO_NAME, JAIR_NAME
from jairbet.db import connect, init_db
from jairbet.repository import add_bettor, add_match, add_team, get_bet, upsert_bet


@pytest.mark.parametrize(
    ("jair", "allowed"),
    [
        ((1, 2), {(2, 1)}),
        ((1, 0), {(0, 1)}),
        ((3, 1), {(1, 3)}),
        ((0, 0), {(1, 0), (0, 1)}),
        ((2, 2), {(2, 1), (1, 2)}),
        ((3, 3), {(3, 2), (2, 3)}),
    ],
)
def test_invert_follows_the_rules(
    jair: tuple[int, int], allowed: set[tuple[int, int]]
) -> None:
    """Wins are mirrored and draws become a one-goal win for a random side."""
    for seed in range(20):
        assert invert(*jair, random.Random(seed)) in allowed


@pytest.mark.parametrize("draw", [(0, 0), (1, 1), (2, 2)])
def test_draws_use_both_sides_across_seeds(draw: tuple[int, int]) -> None:
    """The random tie-break must be able to pick either team."""
    results = {invert(*draw, random.Random(seed)) for seed in range(40)}
    assert len(results) == 2


def test_seeded_inversion_is_reproducible() -> None:
    """The same match always inverts a draw the same way."""
    assert invert(2, 2, caranguejo_rng(42)) == invert(2, 2, caranguejo_rng(42))


def test_negative_prediction_is_rejected() -> None:
    """Invalid input must not produce a bet."""
    with pytest.raises(ValueError):
        invert(-1, 0, random.Random(0))


def test_follows_jair_edits_but_not_admin_overrides() -> None:
    """Caranguejo is recomputed on Jair's edits unless the admin set it."""
    connection = connect()
    init_db(connection)
    match_id = add_match(
        connection, "2026-01-01", add_team(connection, "A"), add_team(connection, "B")
    )
    jair = add_bettor(connection, JAIR_NAME)
    crab = add_bettor(connection, CARANGUEJO_NAME)
    upsert_bet(connection, match_id, jair, 1, 2)
    assert place_auto_bets(connection, match_id)[CARANGUEJO_NAME] == (2, 1)
    upsert_bet(connection, match_id, jair, 1, 0)
    assert place_auto_bets(connection, match_id)[CARANGUEJO_NAME] == (0, 1)
    upsert_bet(connection, match_id, crab, 5, 5, source="admin")
    upsert_bet(connection, match_id, jair, 3, 0)
    assert place_auto_bets(connection, match_id)[CARANGUEJO_NAME] == (5, 5)
    bet = get_bet(connection, match_id, crab)
    assert bet is not None and bet["source"] == "admin"
    connection.close()
