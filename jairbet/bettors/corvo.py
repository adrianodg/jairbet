"""Corvo: a deterministic Poisson model built from the teams' previous results.

The model estimates expected goals for each side from venue-specific attack and
defence strengths, then picks the most likely outcome (home win, draw or away
win) and the most likely scoreline within that outcome.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

from jairbet.config import (
    CORVO_DEFAULT_MU_AWAY,
    CORVO_DEFAULT_MU_HOME,
    CORVO_FORM_N,
    CORVO_HALF_LIFE,
    CORVO_LAST_N,
    CORVO_MAX_GOALS,
    CORVO_MIN_MATCHES,
    CORVO_PRIOR_K,
)
from jairbet.scoring import outcome


@dataclass(frozen=True)
class PastMatch:
    """A finished match used as model input.

    Attributes:
        played_at: Sortable UTC text of when the match was played.
        home_id: Home team identifier.
        away_id: Away team identifier.
        home_goals: Final home goals.
        away_goals: Final away goals.
    """

    played_at: str
    home_id: int
    away_id: int
    home_goals: int
    away_goals: int


@dataclass(frozen=True)
class TeamForm:
    """Recent results of one team, most recent first.

    Attributes:
        form: Results as ``W``/``D``/``L`` letters, most recent first.
        matches: Matches considered for the goal averages.
        goals_for: Average goals scored.
        goals_against: Average goals conceded.
    """

    form: str
    matches: int
    goals_for: float
    goals_against: float


@dataclass(frozen=True)
class CorvoMetrics:
    """Corvo's prediction and the numbers behind it.

    Attributes:
        prediction: Predicted home and away goals.
        lambda_home: Expected home goals.
        lambda_away: Expected away goals.
        p_home: Probability of a home win.
        p_draw: Probability of a draw.
        p_away: Probability of an away win.
        mu_home: League average home goals used.
        mu_away: League average away goals used.
        attack_home: Home team's attack strength when playing at home.
        defence_home: Home team's defence weakness at home (1.0 is average).
        attack_away: Away team's attack strength when playing away.
        defence_away: Away team's defence weakness away (1.0 is average).
        home_form: Home team's recent form.
        away_form: Away team's recent form.
        sample_size: Matches in the history window.
    """

    prediction: tuple[int, int]
    lambda_home: float
    lambda_away: float
    p_home: float
    p_draw: float
    p_away: float
    mu_home: float
    mu_away: float
    attack_home: float
    defence_home: float
    attack_away: float
    defence_away: float
    home_form: TeamForm
    away_form: TeamForm
    sample_size: int


def poisson_pmf(goals: int, expected: float) -> float:
    """Return the Poisson probability of scoring exactly ``goals``.

    Args:
        goals: Number of goals.
        expected: Expected goals (lambda).

    Returns:
        Probability between 0 and 1.
    """
    return math.exp(-expected) * expected**goals / math.factorial(goals)


def league_averages(history: Sequence[PastMatch]) -> tuple[float, float]:
    """Return average home and away goals, or defaults for small samples.

    Args:
        history: Finished matches.

    Returns:
        ``(mu_home, mu_away)``.
    """
    if len(history) < CORVO_MIN_MATCHES:
        return CORVO_DEFAULT_MU_HOME, CORVO_DEFAULT_MU_AWAY
    mu_home = sum(match.home_goals for match in history) / len(history)
    mu_away = sum(match.away_goals for match in history) / len(history)
    # Guard against degenerate samples with no goals at all.
    return max(mu_home, 0.1), max(mu_away, 0.1)


def _strength(values: Sequence[float], average: float) -> float:
    """Shrink a decayed average ratio towards 1.0.

    Args:
        values: Goal counts, most recent first.
        average: League average the goals are compared with.

    Returns:
        Strength where 1.0 equals the league average.
    """
    weights = [0.5 ** (index / CORVO_HALF_LIFE) for index in range(len(values))]
    weighted = sum(w * value / average for w, value in zip(weights, values))
    return (weighted + CORVO_PRIOR_K) / (sum(weights) + CORVO_PRIOR_K)


def venue_strengths(
    history: Sequence[PastMatch],
    team_id: int,
    at_home: bool,
    mu_home: float,
    mu_away: float,
) -> tuple[float, float]:
    """Return a team's attack and defence strength at one venue.

    Args:
        history: Finished matches, oldest first.
        team_id: Team identifier.
        at_home: Use home matches (``True``) or away matches (``False``).
        mu_home: League average home goals.
        mu_away: League average away goals.

    Returns:
        ``(attack, defence)``; defence above 1.0 means conceding more than average.
    """
    if at_home:
        games = [match for match in history if match.home_id == team_id]
        scored = [match.home_goals for match in games]
        conceded = [match.away_goals for match in games]
        scored_avg, conceded_avg = mu_home, mu_away
    else:
        games = [match for match in history if match.away_id == team_id]
        scored = [match.away_goals for match in games]
        conceded = [match.home_goals for match in games]
        scored_avg, conceded_avg = mu_away, mu_home
    recent_scored = list(reversed(scored))[:CORVO_LAST_N]
    recent_conceded = list(reversed(conceded))[:CORVO_LAST_N]
    return (
        _strength(recent_scored, scored_avg),
        _strength(recent_conceded, conceded_avg),
    )


def team_form(history: Sequence[PastMatch], team_id: int) -> TeamForm:
    """Summarise a team's latest results at any venue.

    Args:
        history: Finished matches, oldest first.
        team_id: Team identifier.

    Returns:
        Recent form and goal averages over the last matches.
    """
    games = [m for m in history if team_id in (m.home_id, m.away_id)]
    recent = list(reversed(games))[:CORVO_LAST_N]
    letters = []
    scored = conceded = 0
    for match in recent:
        is_home = match.home_id == team_id
        goals_for = match.home_goals if is_home else match.away_goals
        goals_against = match.away_goals if is_home else match.home_goals
        scored += goals_for
        conceded += goals_against
        letters.append("WDL"[1 - outcome(goals_for, goals_against)])
    count = len(recent)
    return TeamForm(
        form="".join(letters[:CORVO_FORM_N]),
        matches=count,
        goals_for=scored / count if count else 0.0,
        goals_against=conceded / count if count else 0.0,
    )


def most_likely_score(
    lambda_home: float, lambda_away: float
) -> tuple[tuple[int, int], float, float, float]:
    """Pick the best scoreline from an independent Poisson score matrix.

    The most likely outcome is chosen first (ties prefer home, then draw), then
    the most likely scoreline within it (ties prefer fewer goals).

    Args:
        lambda_home: Expected home goals.
        lambda_away: Expected away goals.

    Returns:
        ``(prediction, p_home, p_draw, p_away)`` with normalised probabilities.
    """
    home_pmf = [poisson_pmf(g, lambda_home) for g in range(CORVO_MAX_GOALS + 1)]
    away_pmf = [poisson_pmf(g, lambda_away) for g in range(CORVO_MAX_GOALS + 1)]
    cells = {
        (h, a): home_pmf[h] * away_pmf[a]
        for h in range(CORVO_MAX_GOALS + 1)
        for a in range(CORVO_MAX_GOALS + 1)
    }
    total = sum(cells.values())
    totals = {1: 0.0, 0: 0.0, -1: 0.0}
    for (h, a), probability in cells.items():
        totals[outcome(h, a)] += probability / total
    priority = {1: 2, 0: 1, -1: 0}
    best_outcome = max(totals, key=lambda key: (round(totals[key], 12), priority[key]))
    candidates = [cell for cell in cells if outcome(*cell) == best_outcome]
    prediction = max(
        candidates, key=lambda cell: (round(cells[cell], 12), -sum(cell), -cell[1])
    )
    return prediction, totals[1], totals[0], totals[-1]


def predict(history: Sequence[PastMatch], home_id: int, away_id: int) -> CorvoMetrics:
    """Predict a match from previous results only.

    Callers must pass only matches finished before the target kickoff.

    Args:
        history: Finished matches in the model window.
        home_id: Home team identifier.
        away_id: Away team identifier.

    Returns:
        Prediction and supporting metrics.
    """
    ordered = sorted(history, key=lambda match: match.played_at)
    mu_home, mu_away = league_averages(ordered)
    attack_home, defence_home = venue_strengths(
        ordered, home_id, True, mu_home, mu_away
    )
    attack_away, defence_away = venue_strengths(
        ordered, away_id, False, mu_home, mu_away
    )
    lambda_home = mu_home * attack_home * defence_away
    lambda_away = mu_away * attack_away * defence_home
    prediction, p_home, p_draw, p_away = most_likely_score(lambda_home, lambda_away)
    return CorvoMetrics(
        prediction=prediction,
        lambda_home=lambda_home,
        lambda_away=lambda_away,
        p_home=p_home,
        p_draw=p_draw,
        p_away=p_away,
        mu_home=mu_home,
        mu_away=mu_away,
        attack_home=attack_home,
        defence_home=defence_home,
        attack_away=attack_away,
        defence_away=defence_away,
        home_form=team_form(ordered, home_id),
        away_form=team_form(ordered, away_id),
        sample_size=len(ordered),
    )
