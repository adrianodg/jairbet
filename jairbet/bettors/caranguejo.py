"""Caranguejo: always bets the opposite of Jair."""

import random


def caranguejo_rng(match_id: int) -> random.Random:
    """Return the reproducible random generator used for one match.

    Args:
        match_id: Match identifier used as the seed.

    Returns:
        Seeded generator, so the same draw always inverts the same way.
    """
    return random.Random(match_id)


def invert(pred_home: int, pred_away: int, rng: random.Random) -> tuple[int, int]:
    """Return the opposite of a prediction.

    A win becomes the mirrored score (``1x2 -> 2x1``). A draw has no mirror, so
    one side gets a goal less (or a goal more for ``0x0``), picked at random:
    ``0x0 -> 1x0 | 0x1`` and ``2x2 -> 2x1 | 1x2``.

    Args:
        pred_home: Jair's predicted home goals.
        pred_away: Jair's predicted away goals.
        rng: Random generator used to break draws.

    Returns:
        Caranguejo's home and away goals.

    Raises:
        ValueError: If a prediction is negative.
    """
    if pred_home < 0 or pred_away < 0:
        raise ValueError("Predictions cannot be negative")
    if pred_home != pred_away:
        return pred_away, pred_home
    goals = pred_home
    if goals == 0:
        return rng.choice([(1, 0), (0, 1)])
    return rng.choice([(goals, goals - 1), (goals - 1, goals)])
