"""Tests for Corvo, the Poisson model bettor."""

import pytest

from jairbet.bettors import corvo_for_match, place_auto_bets
from jairbet.bettors.corvo import PastMatch, most_likely_score, predict
from jairbet.config import CORVO_DEFAULT_MU_AWAY, CORVO_DEFAULT_MU_HOME, JAIR_NAME
from jairbet.db import connect, init_db
from jairbet.repository import add_bettor, add_match, add_team, upsert_bet

STRONG, WEAK, OTHER_A, OTHER_B = 1, 2, 3, 4


def _history() -> list[PastMatch]:
    """Build a season where team 1 thrashes everyone and team 2 always loses.

    Returns:
        Finished matches, oldest first.
    """
    matches = []
    day = 1
    for opponent in (OTHER_A, OTHER_B) * 6:
        matches.append(PastMatch(f"2026-03-{day:02d} 20:00:00", STRONG, opponent, 3, 0))
        matches.append(PastMatch(f"2026-03-{day:02d} 21:00:00", opponent, WEAK, 2, 0))
        matches.append(PastMatch(f"2026-03-{day:02d} 22:00:00", OTHER_A, OTHER_B, 1, 1))
        day += 1
    return matches


def test_no_history_uses_league_defaults() -> None:
    """Without data the expected goals equal the default league averages."""
    metrics = predict([], STRONG, WEAK)
    assert metrics.lambda_home == pytest.approx(CORVO_DEFAULT_MU_HOME)
    assert metrics.lambda_away == pytest.approx(CORVO_DEFAULT_MU_AWAY)
    assert metrics.prediction == (1, 0)
    assert metrics.sample_size == 0
    assert metrics.p_home + metrics.p_draw + metrics.p_away == pytest.approx(1.0)


def test_strong_home_team_is_predicted_to_win() -> None:
    """A dominant home side facing a poor away side is tipped to win."""
    metrics = predict(_history(), STRONG, WEAK)
    home, away = metrics.prediction
    assert home > away
    assert metrics.lambda_home > metrics.lambda_away
    assert metrics.p_home > 0.5
    assert metrics.home_form.form == "WWWWW"
    assert metrics.away_form.form == "LLLLL"


def test_strong_away_team_is_predicted_to_win() -> None:
    """Venue does not hide a big quality gap."""
    history = _history() + [
        PastMatch("2026-04-01 20:00:00", OTHER_A, STRONG, 0, 3),
        PastMatch("2026-04-02 20:00:00", OTHER_B, STRONG, 0, 2),
        PastMatch("2026-04-03 20:00:00", WEAK, OTHER_A, 0, 2),
    ]
    home, away = predict(history, WEAK, STRONG).prediction
    assert away > home


def test_prediction_is_deterministic() -> None:
    """The same history always yields the same metrics."""
    assert predict(_history(), STRONG, WEAK) == predict(_history(), STRONG, WEAK)


def test_most_likely_score_picks_outcome_first() -> None:
    """Equal teams with low scoring still produce a valid scoreline."""
    prediction, p_home, p_draw, p_away = most_likely_score(1.0, 1.0)
    assert p_home == pytest.approx(p_away)
    assert prediction in {(1, 0), (1, 1)}


def test_future_matches_are_ignored() -> None:
    """Results after the target kickoff must never influence the prediction."""
    connection = connect()
    init_db(connection)
    strong, weak = add_team(connection, "Strong"), add_team(connection, "Weak")
    target = add_match(
        connection, "2026-05-01", strong, weak, kickoff_at="2026-05-01 22:00:00"
    )
    before = corvo_for_match(connection, target)
    for day in range(2, 28):
        add_match(
            connection,
            f"2026-05-{day:02d}",
            strong,
            weak,
            0,
            5,
            kickoff_at=f"2026-05-{day:02d} 22:00:00",
        )
    assert corvo_for_match(connection, target) == before
    assert before.sample_size == 0
    connection.close()


def test_corvo_bets_only_when_jair_bets() -> None:
    """No Jair bet means no animal bets."""
    connection = connect()
    init_db(connection)
    match_id = add_match(
        connection, "2026-05-01", add_team(connection, "A"), add_team(connection, "B")
    )
    assert place_auto_bets(connection, match_id) == {}
    upsert_bet(connection, match_id, add_bettor(connection, JAIR_NAME), 2, 2)
    assert set(place_auto_bets(connection, match_id)) == {
        "Macaco",
        "Caranguejo",
        "Corvo",
    }
    connection.close()
