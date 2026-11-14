"""Macaco: the random baseline bettor (each side drawn from 0 to 3 goals)."""

import random

from jairbet.config import RANDOM_SCORE_MAX, RANDOM_SCORE_MIN


def random_prediction(
    rng: random.Random | random.SystemRandom | None = None,
) -> tuple[int, int]:
    """Generate a random 0–3 score prediction for Macaco.

    Args:
        rng: Optional random-number generator, primarily for deterministic tests.

    Returns:
        Predicted home and away goals, each inclusively between 0 and 3.
    """
    generator = rng or random.SystemRandom()
    return (
        generator.randint(RANDOM_SCORE_MIN, RANDOM_SCORE_MAX),
        generator.randint(RANDOM_SCORE_MIN, RANDOM_SCORE_MAX),
    )
