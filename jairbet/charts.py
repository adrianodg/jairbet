"""Matplotlib charts rendered as PNG bytes for Telegram.

Charts use a dark background, the Okabe-Ito colourblind-safe palette, a clear
title, labelled axes with units, and data labels on bars. They are sized to be
readable on a phone.
"""

import io

import matplotlib
import matplotlib.dates as mdates

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.ticker import PercentFormatter  # noqa: E402

from jairbet.config import PALETTE  # noqa: E402

BACKGROUND = "#14181f"
FOREGROUND = "#e6e6e6"
GRID = "#3a3f4a"
FALLBACK_COLOURS = ["#56B4E9", "#D55E00", "#F0E442", "#999999"]


def colour_for(bettor: str, index: int = 0) -> str:
    """Return the palette colour of a bettor.

    Args:
        bettor: Bettor name.
        index: Position used to pick a fallback colour.

    Returns:
        Hex colour.
    """
    return PALETTE.get(bettor, FALLBACK_COLOURS[index % len(FALLBACK_COLOURS)])


def _figure(width: float = 8.0, height: float = 5.0) -> tuple[Figure, plt.Axes]:
    """Create a styled figure and axes.

    Args:
        width: Width in inches.
        height: Height in inches.

    Returns:
        Figure and axes.
    """
    figure, axes = plt.subplots(figsize=(width, height), dpi=150)
    figure.patch.set_facecolor(BACKGROUND)
    axes.set_facecolor(BACKGROUND)
    for spine in ("top", "right"):
        axes.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        axes.spines[spine].set_color(GRID)
    axes.tick_params(colors=FOREGROUND, labelsize=10)
    axes.grid(axis="y", color=GRID, linewidth=0.6, alpha=0.7)
    axes.set_axisbelow(True)
    return figure, axes


def _finish(figure: Figure, axes: plt.Axes, title: str, x: str, y: str) -> bytes:
    """Apply titles and legend, then render the figure to PNG.

    Args:
        figure: Figure to render.
        axes: Main axes.
        title: Chart title.
        x: Horizontal axis label with units.
        y: Vertical axis label with units.

    Returns:
        PNG bytes.
    """
    axes.set_title(title, color=FOREGROUND, fontsize=14, fontweight="bold", loc="left")
    axes.set_xlabel(x, color=FOREGROUND, fontsize=11)
    axes.set_ylabel(y, color=FOREGROUND, fontsize=11)
    legend = axes.get_legend()
    if legend is not None:
        legend.get_frame().set_facecolor(BACKGROUND)
        legend.get_frame().set_edgecolor(GRID)
        for text in legend.get_texts():
            text.set_color(FOREGROUND)
    figure.tight_layout()
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", facecolor=BACKGROUND)
    plt.close(figure)
    return buffer.getvalue()


def _empty(title: str) -> bytes:
    """Render a placeholder chart when there is no data yet.

    Args:
        title: Chart title.

    Returns:
        PNG bytes.
    """
    figure, axes = _figure(6, 3)
    axes.axis("off")
    axes.text(0.5, 0.5, "Sem dados ainda", color=FOREGROUND, ha="center", fontsize=14)
    return _finish(figure, axes, title, "", "")


def cumulative_points(data: pd.DataFrame) -> bytes:
    """Line chart of cumulative points per bettor.

    Args:
        data: Output of ``stats.cumulative_points``.

    Returns:
        PNG bytes.
    """
    title = "Pontos acumulados"
    if data.empty:
        return _empty(title)
    figure, axes = _figure()
    for index, (bettor, frame) in enumerate(data.groupby("bettor", sort=False)):
        colour = colour_for(bettor, index)
        axes.plot(
            frame["match_date"],
            frame["cumulative"],
            color=colour,
            linewidth=2.2,
            label=bettor,
        )
        last = frame.iloc[-1]
        axes.annotate(
            f"{int(last['cumulative'])}",
            (last["match_date"], last["cumulative"]),
            xytext=(4, 0),
            textcoords="offset points",
            color=colour,
            fontsize=10,
            fontweight="bold",
            va="center",
        )
    axes.legend(loc="upper left", fontsize=10)
    figure.autofmt_xdate()
    axes.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
    return _finish(figure, axes, title, "Data do jogo", "Pontos (acumulado)")


def monthly_points(data: pd.DataFrame) -> bytes:
    """Grouped bar chart of points per month.

    Args:
        data: Output of ``stats.monthly_points`` (months x bettors).

    Returns:
        PNG bytes.
    """
    title = "Pontos por mês"
    if data.empty:
        return _empty(title)
    data = data.tail(6)
    figure, axes = _figure(9, 5)
    bettors = list(data.columns)
    width = 0.8 / len(bettors)
    positions = range(len(data.index))
    for index, bettor in enumerate(bettors):
        offsets = [p - 0.4 + width * (index + 0.5) for p in positions]
        bars = axes.bar(
            offsets,
            data[bettor],
            width=width,
            color=colour_for(bettor, index),
            label=bettor,
        )
        axes.bar_label(bars, color=FOREGROUND, fontsize=8, padding=2)
    labels = [f"{month[5:]}/{month[2:4]}" for month in data.index]
    axes.set_xticks(list(positions), labels)
    axes.legend(fontsize=9, ncols=min(len(bettors), 4), loc="upper left")
    return _finish(figure, axes, title, "Mês", "Pontos no mês")


def hit_rates(data: pd.DataFrame, bettor: str) -> bytes:
    """Horizontal bars of winner and exact hit rates per team.

    Args:
        data: Output of ``stats.team_hit_rates``.
        bettor: Bettor analysed, shown in the title.

    Returns:
        PNG bytes.
    """
    title = f"Acertos de {bettor} por time (mín. 3 jogos)"
    if data.empty:
        return _empty(title)
    figure, axes = _figure(8, max(4.0, 0.45 * len(data) + 1.5))
    axes.grid(axis="x", color=GRID, linewidth=0.6, alpha=0.7)
    axes.grid(axis="y", visible=False)
    rows = range(len(data))
    winner = axes.barh(
        [r + 0.2 for r in rows],
        data["winner_pct"],
        height=0.4,
        color=colour_for(bettor),
        label="Vencedor",
    )
    exact = axes.barh(
        [r - 0.2 for r in rows],
        data["exact_pct"],
        height=0.4,
        color=PALETTE["light_blue"],
        label="Placar exato",
    )
    axes.bar_label(winner, fmt="%.0f%%", color=FOREGROUND, fontsize=8, padding=2)
    axes.bar_label(exact, fmt="%.0f%%", color=FOREGROUND, fontsize=8, padding=2)
    labels = [f"{team} ({n})" for team, n in zip(data["team"], data["matches"])]
    axes.set_yticks(list(rows), labels)
    axes.set_xlim(0, 110)
    axes.legend(fontsize=9, loc="lower right")
    return _finish(figure, axes, title, "Taxa de acerto (%)", "Time (jogos)")


def predicted_scorelines(data: pd.DataFrame) -> bytes:
    """Small-multiple bar charts of the most predicted scorelines per bettor.

    Args:
        data: Output of ``stats.top_scorelines``.

    Returns:
        PNG bytes.
    """
    title = "Placares mais apostados"
    if data.empty:
        return _empty(title)
    bettors = list(dict.fromkeys(data["bettor"]))
    figure, grid = plt.subplots(
        len(bettors), 1, figsize=(7, 2.0 * len(bettors) + 0.8), dpi=150, squeeze=False
    )
    figure.patch.set_facecolor(BACKGROUND)
    for index, bettor in enumerate(bettors):
        axes = grid[index][0]
        axes.set_facecolor(BACKGROUND)
        frame = data[data["bettor"] == bettor]
        bars = axes.bar(
            frame["scoreline"], frame["count"], color=colour_for(bettor, index)
        )
        axes.bar_label(bars, color=FOREGROUND, fontsize=8, padding=2)
        axes.set_ylabel("Vezes", color=FOREGROUND, fontsize=9)
        axes.set_title(bettor, color=FOREGROUND, fontsize=11, loc="left")
        axes.tick_params(colors=FOREGROUND, labelsize=9)
        axes.set_ylim(0, frame["count"].max() * 1.25)
        for spine in ("top", "right"):
            axes.spines[spine].set_visible(False)
        for spine in ("left", "bottom"):
            axes.spines[spine].set_color(GRID)
    figure.suptitle(
        title, color=FOREGROUND, fontsize=14, fontweight="bold", x=0.02, ha="left"
    )
    grid[-1][0].set_xlabel("Placar apostado (mandante x visitante)", color=FOREGROUND)
    figure.tight_layout()
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", facecolor=BACKGROUND)
    plt.close(figure)
    return buffer.getvalue()


def _spread_labels(
    ends: list[tuple[float, object, str]], gap: float
) -> list[tuple[float, float, object, str]]:
    """Nudge end-of-line labels apart so they do not overlap.

    Args:
        ends: ``(value, x, colour)`` for the last point of each line.
        gap: Minimum vertical distance between labels, in data units.

    Returns:
        ``(value, label_y, x, colour)`` tuples, sorted by value.
    """
    placed: list[tuple[float, float, object, str]] = []
    for value, when, colour in sorted(ends, key=lambda end: end[0]):
        label_y = value
        if placed and label_y - placed[-1][1] < gap:
            label_y = placed[-1][1] + gap
        placed.append((value, label_y, when, colour))
    return placed


def hit_rate_over_time(data: pd.DataFrame, stats_min: int = 5) -> bytes:
    """Line charts of the cumulative winner and exact hit rates per bettor.

    Args:
        data: Output of ``stats.hit_rate_over_time``.
        stats_min: Matches each bettor had before the lines start, shown in the
            subtitle.

    Returns:
        PNG bytes.
    """
    title = "Taxa de acerto ao longo do tempo"
    if data.empty:
        return _empty(title)
    figure, (top, bottom) = plt.subplots(2, 1, figsize=(8, 7.5), dpi=150, sharex=True)
    figure.patch.set_facecolor(BACKGROUND)
    panels = (
        (top, "winner_pct", "Acerto do vencedor (%)"),
        (bottom, "exact_pct", "Placar exato (%)"),
    )
    for axes, column, label in panels:
        axes.set_facecolor(BACKGROUND)
        for spine in ("top", "right"):
            axes.spines[spine].set_visible(False)
        for spine in ("left", "bottom"):
            axes.spines[spine].set_color(GRID)
        axes.tick_params(colors=FOREGROUND, labelsize=10)
        axes.grid(axis="y", color=GRID, linewidth=0.6, alpha=0.7)
        axes.set_axisbelow(True)
        axes.yaxis.set_major_formatter(PercentFormatter(decimals=0))
        axes.set_ylabel(label, color=FOREGROUND, fontsize=11)
        ceiling = max(10.0, float(data[column].max()) * 1.15)
        ends = []
        for index, (bettor, frame) in enumerate(data.groupby("bettor", sort=False)):
            colour = colour_for(bettor, index)
            axes.plot(
                frame["match_date"],
                frame[column],
                color=colour,
                linewidth=2.2,
                label=bettor,
            )
            last = frame.iloc[-1]
            ends.append((float(last[column]), last["match_date"], colour))
        for value, label_y, when, colour in _spread_labels(ends, ceiling * 0.06):
            axes.annotate(
                f"{value:.0f}%",
                (when, label_y),
                xytext=(4, 0),
                textcoords="offset points",
                color=colour,
                fontsize=10,
                fontweight="bold",
                va="center",
                annotation_clip=False,
            )
        axes.set_ylim(0, ceiling)
    top.set_title(
        f"Acumulado, a partir do {stats_min}º jogo de cada apostador",
        color=FOREGROUND,
        fontsize=10,
        loc="left",
    )
    legend = top.legend(fontsize=9, ncols=min(data["bettor"].nunique(), 4))
    legend.get_frame().set_facecolor(BACKGROUND)
    legend.get_frame().set_edgecolor(GRID)
    for text in legend.get_texts():
        text.set_color(FOREGROUND)
    bottom.set_xlabel("Data do jogo", color=FOREGROUND, fontsize=11)
    bottom.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
    figure.autofmt_xdate()
    figure.suptitle(
        title, color=FOREGROUND, fontsize=14, fontweight="bold", x=0.02, ha="left"
    )
    figure.tight_layout()
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", facecolor=BACKGROUND)
    plt.close(figure)
    return buffer.getvalue()
