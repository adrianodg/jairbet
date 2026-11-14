"""Pure functions implementing Jairbet's prediction scoring rules."""

import re
from dataclasses import dataclass

SCORE_RE = re.compile(r"^\s*(\d{1,2})\s*[xX×-]\s*(\d{1,2})\s*$")


@dataclass(frozen=True)
class Score:
    """The evaluated result of a prediction.

    Attributes:
        winner_hit: Whether the predicted outcome was correct, or ``None`` pending.
        exact_hit: Whether the full scoreline was correct, or ``None`` pending.
        points: Awarded points, or ``None`` pending.
    """

    winner_hit: bool | None
    exact_hit: bool | None
    points: int | None


def outcome(home: int, away: int) -> int:
    """Return -1, 0, or 1 for an away win, draw, or home win.

    Args:
        home: Home-team goals.
        away: Away-team goals.

    Returns:
        Signed match outcome.
    """
    return (home > away) - (home < away)


def score_prediction(
    pred_home: int,
    pred_away: int,
    home_goals: int | None,
    away_goals: int | None,
    points_winner: int = 3,
    bonus_exact: int = 3,
) -> Score:
    """Score one prediction against a final result.

    Args:
        pred_home: Predicted home goals.
        pred_away: Predicted away goals.
        home_goals: Actual home goals, or ``None`` when pending.
        away_goals: Actual away goals, or ``None`` when pending.
        points_winner: Points awarded for a correct outcome.
        bonus_exact: Additional points awarded for an exact scoreline.

    Returns:
        Evaluation values, all ``None`` for a pending match.

    Raises:
        ValueError: If just one actual goal count is supplied.
    """
    if (home_goals is None) != (away_goals is None):
        raise ValueError("Both actual goal values must be supplied together")
    if home_goals is None:
        return Score(None, None, None)
    winner_hit = outcome(pred_home, pred_away) == outcome(home_goals, away_goals)
    exact_hit = pred_home == home_goals and pred_away == away_goals
    points = (points_winner if winner_hit else 0) + (bonus_exact if exact_hit else 0)
    return Score(winner_hit, exact_hit, points)


def parse_score_text(text: str) -> tuple[int, int]:
    """Parse a typed score such as ``2x1``, ``2 x 1``, ``2X1`` or ``2-1``.

    Args:
        text: User input.

    Returns:
        Home and away goals.

    Raises:
        ValueError: If the text is not a valid score.
    """
    match = SCORE_RE.fullmatch(text)
    if not match:
        raise ValueError(f"Placar inválido {text!r}; use o formato 2x1")
    return int(match.group(1)), int(match.group(2))
