"""Automatic "animal" bettors and the glue that stores their bets.

Animals only bet on matches Jair bet on, at the moment Jair's bet is saved, so
every bettor is ranked on the same fixtures. Bets edited by the admin
(``source='admin'``) are never regenerated.
"""

import random
import sqlite3
from collections.abc import Callable
from datetime import timedelta

from jairbet.bettors.caranguejo import caranguejo_rng, invert
from jairbet.bettors.corvo import (
    CorvoMetrics,
    PastMatch,
    TeamForm,
    league_averages,
    predict,
    team_form,
    venue_strengths,
)
from jairbet.bettors.macaco import random_prediction
from jairbet.config import (
    CARANGUEJO_NAME,
    CORVO_NAME,
    CORVO_WINDOW_DAYS,
    DEFAULT_TIMEZONE,
    JAIR_NAME,
    MACACO_NAME,
)
from jairbet.repository import (
    add_bettor,
    delete_auto_bets,
    finished_history,
    get_bet,
    get_match,
    matches_with_bettor_bet,
    upsert_bet,
)
from jairbet.timeutil import from_db, lock_time, to_db, utc_now

__all__ = [
    "CorvoMetrics",
    "backfill",
    "corvo_for_match",
    "history_for_match",
    "place_auto_bets",
    "team_profile",
]


def _past_matches(rows: list[sqlite3.Row]) -> list[PastMatch]:
    """Convert history rows to model input.

    Args:
        rows: Rows from ``repository.finished_history``.

    Returns:
        Past matches.
    """
    return [
        PastMatch(
            played_at=row["played_at"],
            home_id=int(row["home_team_id"]),
            away_id=int(row["away_team_id"]),
            home_goals=int(row["home_goals"]),
            away_goals=int(row["away_goals"]),
        )
        for row in rows
    ]


def team_profile(
    connection: sqlite3.Connection, team_id: int
) -> tuple[TeamForm, tuple[float, float], tuple[float, float], int]:
    """Describe a team's current form with Corvo's strengths.

    Args:
        connection: Open database connection.
        team_id: Team identifier.

    Returns:
        Form, home (attack, defence), away (attack, defence), sample size.
    """
    now = utc_now()
    since = now - timedelta(days=CORVO_WINDOW_DAYS)
    history = _past_matches(finished_history(connection, to_db(now), to_db(since)))
    mu_home, mu_away = league_averages(history)
    return (
        team_form(history, team_id),
        venue_strengths(history, team_id, True, mu_home, mu_away),
        venue_strengths(history, team_id, False, mu_home, mu_away),
        len(history),
    )


def history_for_match(
    connection: sqlite3.Connection,
    match_id: int,
    tz_name: str = DEFAULT_TIMEZONE,
) -> list[PastMatch]:
    """Load the finished matches Corvo may use for one target match.

    Only matches played before the target's kickoff (or its local date for
    legacy rows) and inside the model window are returned.

    Args:
        connection: Open database connection.
        match_id: Target match identifier.
        tz_name: IANA timezone for legacy matches without a kickoff.

    Returns:
        Past matches, oldest first.

    Raises:
        ValueError: If the match does not exist.
    """
    match = get_match(connection, match_id)
    if match is None:
        raise ValueError(f"Match {match_id} not found")
    reference = (
        from_db(match["kickoff_at"])
        if match["kickoff_at"]
        else lock_time(match["match_date"], None, tz_name) - timedelta(days=1)
    )
    since = reference - timedelta(days=CORVO_WINDOW_DAYS)
    rows = finished_history(connection, to_db(reference), to_db(since), match_id)
    return _past_matches(rows)


def corvo_for_match(
    connection: sqlite3.Connection, match_id: int, tz_name: str = DEFAULT_TIMEZONE
) -> CorvoMetrics:
    """Compute Corvo's metrics for a match.

    Args:
        connection: Open database connection.
        match_id: Match identifier.
        tz_name: IANA timezone for legacy matches without a kickoff.

    Returns:
        Corvo's prediction and supporting numbers.

    Raises:
        ValueError: If the match does not exist.
    """
    match = get_match(connection, match_id)
    if match is None:
        raise ValueError(f"Match {match_id} not found")
    history = history_for_match(connection, match_id, tz_name)
    return predict(history, int(match["home_team_id"]), int(match["away_team_id"]))


def _keep_or_place(
    connection: sqlite3.Connection,
    match_id: int,
    bettor_name: str,
    compute: Callable[[], tuple[int, int]],
    refresh: bool,
) -> tuple[int, int]:
    """Keep an existing animal bet or store a freshly computed one.

    Args:
        connection: Open database connection.
        match_id: Match identifier.
        bettor_name: Animal bettor name.
        compute: Function producing a new prediction.
        refresh: Replace an existing automatic bet.

    Returns:
        The prediction stored for this bettor.
    """
    bettor_id = add_bettor(connection, bettor_name)
    existing = get_bet(connection, match_id, bettor_id)
    if existing is not None and (existing["source"] == "admin" or not refresh):
        return int(existing["pred_home"]), int(existing["pred_away"])
    prediction = compute()
    upsert_bet(connection, match_id, bettor_id, *prediction, source="auto")
    return prediction


def place_auto_bets(
    connection: sqlite3.Connection,
    match_id: int,
    overwrite: bool = True,
    rng: random.Random | random.SystemRandom | None = None,
    tz_name: str = DEFAULT_TIMEZONE,
) -> dict[str, tuple[int, int]]:
    """Create or refresh the animal bets that follow Jair's bet on a match.

    Macaco is drawn once and kept. Caranguejo and Corvo are recomputed when
    ``overwrite`` is set, unless the admin edited them. If Jair has no bet, the
    automatic bets are removed.

    Args:
        connection: Open database connection.
        match_id: Match identifier.
        overwrite: Recompute existing Caranguejo/Corvo automatic bets.
        rng: Random generator for Macaco (tests pass a seeded one).
        tz_name: IANA timezone for legacy matches without a kickoff.

    Returns:
        Bettor name to the prediction now stored, for each animal.
    """
    jair_id = add_bettor(connection, JAIR_NAME)
    jair_bet = get_bet(connection, match_id, jair_id)
    if jair_bet is None:
        delete_auto_bets(connection, match_id)
        return {}
    predictions: dict[str, tuple[int, int]] = {}
    pred_home, pred_away = int(jair_bet["pred_home"]), int(jair_bet["pred_away"])
    strategies: list[tuple[str, Callable[[], tuple[int, int]], bool]] = [
        (MACACO_NAME, lambda: random_prediction(rng), False),
        (
            CARANGUEJO_NAME,
            lambda: invert(pred_home, pred_away, caranguejo_rng(match_id)),
            overwrite,
        ),
        (
            CORVO_NAME,
            lambda: corvo_for_match(connection, match_id, tz_name).prediction,
            overwrite,
        ),
    ]
    for name, compute, refresh in strategies:
        predictions[name] = _keep_or_place(connection, match_id, name, compute, refresh)
    return predictions


def backfill(connection: sqlite3.Connection, recompute: bool = False) -> int:
    """Create missing animal bets for every match Jair bet on.

    Args:
        connection: Open database connection (the caller commits).
        recompute: Also recompute existing automatic Caranguejo/Corvo bets.

    Returns:
        Number of matches processed.
    """
    jair_id = add_bettor(connection, JAIR_NAME)
    match_ids = matches_with_bettor_bet(connection, jair_id)
    for match_id in match_ids:
        place_auto_bets(connection, match_id, overwrite=recompute)
    return len(match_ids)
