"""Tests for chart data preparation in ``jairbet.stats``."""

import pandas as pd
import pytest

from jairbet import charts, stats


def _scored() -> pd.DataFrame:
    """Build a tiny scored-bets frame with two bettors over two dates.

    Returns:
        Frame shaped like ``stats.scored_bets`` (only the used columns).
    """
    return pd.DataFrame(
        {
            "bettor": ["Jair", "Macaco", "Jair", "Jair", "Macaco"],
            "match_id": [1, 1, 2, 3, 2],
            "match_date": [
                "2026-05-01",
                "2026-05-01",
                "2026-05-08",
                "2026-05-08",
                "2026-05-08",
            ],
            "winner_hit": [1, 0, 1, 0, 1],
            "exact_hit": [1, 0, 0, 0, 0],
            "points": [6, 0, 3, 0, 3],
        }
    )


def test_hit_rate_over_time_is_cumulative() -> None:
    """Rates accumulate across dates and are expressed in percent."""
    data = stats.hit_rate_over_time(_scored(), min_matches=1)
    jair = data[data["bettor"] == "Jair"].reset_index(drop=True)
    assert list(jair["matches"]) == [1, 3]
    assert list(jair["winner_pct"]) == pytest.approx([100.0, 200 / 3])
    assert list(jair["exact_pct"]) == pytest.approx([100.0, 100 / 3])
    macaco = data[data["bettor"] == "Macaco"].reset_index(drop=True)
    assert list(macaco["winner_pct"]) == pytest.approx([0.0, 50.0])
    assert list(data["bettor"].drop_duplicates()) == ["Jair", "Macaco"]


def test_hit_rate_over_time_skips_first_matches() -> None:
    """Dates before a bettor reaches ``min_matches`` are left out."""
    data = stats.hit_rate_over_time(_scored(), min_matches=2)
    assert list(data["bettor"]) == ["Jair", "Macaco"]
    assert list(data["matches"]) == [3, 2]


def test_hit_rate_over_time_empty_and_chart() -> None:
    """No data gives an empty frame; both cases render a PNG."""
    empty = stats.hit_rate_over_time(_scored().iloc[0:0])
    assert empty.empty
    for frame in (empty, stats.hit_rate_over_time(_scored(), 1)):
        assert charts.hit_rate_over_time(frame).startswith(b"\x89PNG")
