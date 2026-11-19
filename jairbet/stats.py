"""Data preparation for statistics messages and charts."""

import sqlite3

import pandas as pd

from jairbet.config import BETTOR_KINDS, JAIR_NAME
from jairbet.repository import query_rows

MIN_TEAM_MATCHES = 3
MIN_RATE_MATCHES = 5


def bettor_order(names: list[str]) -> list[str]:
    """Sort bettor names: Jair, Macaco, Caranguejo, Corvo, then others.

    Args:
        names: Bettor names.

    Returns:
        Sorted names.
    """
    order = {name: index for index, name in enumerate(BETTOR_KINDS)}
    return sorted(names, key=lambda name: (order.get(name, 99), name))


def scored_bets(connection: sqlite3.Connection) -> pd.DataFrame:
    """Load every scored bet from ``v_bet_score``.

    Args:
        connection: Open database connection.

    Returns:
        One row per scored bet, in chronological order.
    """
    rows = query_rows(
        connection,
        """SELECT bettor, bettor_kind, match_id, match_date, kickoff_at,
        competition, home_team, away_team, pred_home, pred_away, home_goals,
        away_goals, winner_hit, exact_hit, points
        FROM v_bet_score WHERE points IS NOT NULL
        ORDER BY COALESCE(kickoff_at, match_date || ' 00:00:00'), match_id""",
    )
    columns = [
        "bettor",
        "bettor_kind",
        "match_id",
        "match_date",
        "kickoff_at",
        "competition",
        "home_team",
        "away_team",
        "pred_home",
        "pred_away",
        "home_goals",
        "away_goals",
        "winner_hit",
        "exact_hit",
        "points",
    ]
    return pd.DataFrame([tuple(row) for row in rows], columns=columns)


def all_predictions(connection: sqlite3.Connection) -> pd.DataFrame:
    """Load every bet, including pending ones.

    Args:
        connection: Open database connection.

    Returns:
        Columns ``bettor``, ``pred_home`` and ``pred_away``.
    """
    rows = query_rows(
        connection, "SELECT bettor, pred_home, pred_away FROM v_bet_score"
    )
    return pd.DataFrame(
        [tuple(row) for row in rows], columns=["bettor", "pred_home", "pred_away"]
    )


def cumulative_points(scored: pd.DataFrame) -> pd.DataFrame:
    """Cumulative points per bettor by match date.

    Args:
        scored: Output of :func:`scored_bets`.

    Returns:
        Columns ``match_date`` (datetime), ``bettor`` and ``cumulative``.
    """
    if scored.empty:
        return pd.DataFrame(columns=["match_date", "bettor", "cumulative"])
    daily = scored.groupby(["match_date", "bettor"], as_index=False)["points"].sum()
    daily["match_date"] = pd.to_datetime(daily["match_date"])
    daily = daily.sort_values("match_date")
    daily["cumulative"] = daily.groupby("bettor")["points"].cumsum()
    rank = {
        name: index
        for index, name in enumerate(bettor_order(list(daily["bettor"].unique())))
    }
    daily = daily.sort_values(
        ["bettor", "match_date"],
        key=lambda col: col.map(rank) if col.name == "bettor" else col,
    )
    return daily[["match_date", "bettor", "cumulative"]].reset_index(drop=True)


def monthly_points(scored: pd.DataFrame) -> pd.DataFrame:
    """Points per month and bettor.

    Args:
        scored: Output of :func:`scored_bets`.

    Returns:
        Pivot table indexed by ``YYYY-MM`` with one column per bettor.
    """
    if scored.empty:
        return pd.DataFrame()
    frame = scored.assign(month=scored["match_date"].str.slice(0, 7))
    table = frame.pivot_table(
        index="month", columns="bettor", values="points", aggfunc="sum", fill_value=0
    ).sort_index()
    return table[bettor_order(list(table.columns))]


def team_hit_rates(
    scored: pd.DataFrame,
    bettor: str = JAIR_NAME,
    min_matches: int = MIN_TEAM_MATCHES,
    top: int = 12,
) -> pd.DataFrame:
    """Winner and exact hit rates of one bettor in matches of each team.

    Args:
        scored: Output of :func:`scored_bets`.
        bettor: Bettor to analyse.
        min_matches: Minimum matches for a team to be listed.
        top: Number of teams kept (those with most matches).

    Returns:
        Columns ``team``, ``matches``, ``winner_pct`` and ``exact_pct``.
    """
    mine = scored[scored["bettor"] == bettor]
    if mine.empty:
        return pd.DataFrame(columns=["team", "matches", "winner_pct", "exact_pct"])
    long = pd.concat(
        [
            mine.rename(columns={"home_team": "team"})[
                ["team", "winner_hit", "exact_hit"]
            ],
            mine.rename(columns={"away_team": "team"})[
                ["team", "winner_hit", "exact_hit"]
            ],
        ]
    )
    grouped = long.groupby("team").agg(
        matches=("winner_hit", "size"),
        winner_pct=("winner_hit", "mean"),
        exact_pct=("exact_hit", "mean"),
    )
    grouped = grouped[grouped["matches"] >= min_matches]
    grouped[["winner_pct", "exact_pct"]] *= 100
    grouped = grouped.sort_values("matches", ascending=False).head(top)
    return grouped.sort_values("winner_pct").reset_index()


def top_scorelines(predictions: pd.DataFrame, top: int = 6) -> pd.DataFrame:
    """Most frequently predicted scorelines per bettor.

    Args:
        predictions: Output of :func:`all_predictions`.
        top: Scorelines kept per bettor.

    Returns:
        Columns ``bettor``, ``scoreline`` and ``count``.
    """
    if predictions.empty:
        return pd.DataFrame(columns=["bettor", "scoreline", "count"])
    frame = predictions.assign(
        scoreline=predictions["pred_home"].astype(str)
        + "x"
        + predictions["pred_away"].astype(str)
    )
    counts = frame.groupby(["bettor", "scoreline"]).size().reset_index(name="count")
    counts = counts.sort_values(["bettor", "count"], ascending=[True, False])
    return counts.groupby("bettor").head(top).reset_index(drop=True)


def hit_rate_over_time(
    scored: pd.DataFrame, min_matches: int = MIN_RATE_MATCHES
) -> pd.DataFrame:
    """Cumulative winner and exact hit rates per bettor by match date.

    The first dates of each bettor are dropped until they have ``min_matches``
    scored bets, because rates over one or two matches swing from 0 to 100%.

    Args:
        scored: Output of :func:`scored_bets`.
        min_matches: Scored bets a bettor needs before rates are reported.

    Returns:
        Columns ``match_date`` (datetime), ``bettor``, ``matches``,
        ``winner_pct`` and ``exact_pct`` (0-100), one row per bettor and date.
    """
    columns = ["match_date", "bettor", "matches", "winner_pct", "exact_pct"]
    if scored.empty:
        return pd.DataFrame(columns=columns)
    daily = scored.groupby(["match_date", "bettor"], as_index=False).agg(
        played=("winner_hit", "size"),
        winners=("winner_hit", "sum"),
        exacts=("exact_hit", "sum"),
    )
    daily["match_date"] = pd.to_datetime(daily["match_date"])
    daily = daily.sort_values("match_date")
    totals = daily.groupby("bettor")[["played", "winners", "exacts"]].cumsum()
    daily["matches"] = totals["played"]
    daily["winner_pct"] = 100 * totals["winners"] / totals["played"]
    daily["exact_pct"] = 100 * totals["exacts"] / totals["played"]
    daily = daily[daily["matches"] >= min_matches]
    rank = {
        name: index
        for index, name in enumerate(bettor_order(list(daily["bettor"].unique())))
    }
    daily = daily.sort_values(
        ["bettor", "match_date"],
        key=lambda col: col.map(rank) if col.name == "bettor" else col,
    )
    return daily[columns].reset_index(drop=True)
