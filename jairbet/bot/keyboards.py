"""Inline keyboards. Callback data stays well under Telegram's 64-byte limit.

Callback prefixes:
    ``p:<match>``            open the goal picker for a match
    ``gh:<match>:<h>``       home goals chosen
    ``ga:<match>:<h>:<a>``   away goals chosen (saves the bet)
    ``x``                    close the picker
    ``dm:<match>:<y|n>``     confirm deleting a match
    ``ct:<competition>``     toggle a competition's auto sync
    ``tr:<external_id>``     track a provider match
    ``ns:<team_id|new>``     pick a suggested team in /novojogo
    ``nc:<code|+>``          pick a competition in /novojogo
    ``nk:<y|n>``             confirm the new match in /novojogo
"""

import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from jairbet.config import MAX_GOALS_KEYBOARD
from jairbet.providers.base import ApiMatch
from jairbet.timeutil import format_kickoff

Row = Mapping[str, Any] | sqlite3.Row


def _rows(
    buttons: Sequence[InlineKeyboardButton], per_row: int
) -> list[list[InlineKeyboardButton]]:
    """Split buttons into keyboard rows.

    Args:
        buttons: Buttons in order.
        per_row: Buttons per row.

    Returns:
        Keyboard layout.
    """
    return [list(buttons[i : i + per_row]) for i in range(0, len(buttons), per_row)]


def matches_keyboard(
    matches: Iterable[Row], tz_name: str, prefix: str = "p"
) -> InlineKeyboardMarkup | None:
    """One button per match.

    Args:
        matches: Match rows.
        tz_name: IANA timezone.
        prefix: Callback prefix.

    Returns:
        Keyboard, or ``None`` when there are no matches.
    """
    buttons = []
    for match in matches:
        when = format_kickoff(match["match_date"], match["kickoff_at"], tz_name)
        keys = match.keys() if hasattr(match, "keys") else []
        bet = (
            f" · {match['pred_home']}x{match['pred_away']}"
            if "pred_home" in keys and match["pred_home"] is not None
            else ""
        )
        label = f"{match['home_team']} x {match['away_team']} · {when}{bet}"
        buttons.append(
            [
                InlineKeyboardButton(
                    label[:60], callback_data=f"{prefix}:{match['match_id']}"
                )
            ]
        )
    return InlineKeyboardMarkup(buttons) if buttons else None


def goals_keyboard(match_id: int, home: int | None = None) -> InlineKeyboardMarkup:
    """Goal picker: first the home goals, then the away goals.

    Args:
        match_id: Match identifier.
        home: Home goals already chosen, or ``None`` for the first step.

    Returns:
        Keyboard with 0..N goal buttons and a cancel button.
    """
    goals = range(MAX_GOALS_KEYBOARD + 1)
    if home is None:
        buttons = [
            InlineKeyboardButton(str(g), callback_data=f"gh:{match_id}:{g}")
            for g in goals
        ]
    else:
        buttons = [
            InlineKeyboardButton(str(g), callback_data=f"ga:{match_id}:{home}:{g}")
            for g in goals
        ]
    layout = _rows(buttons, 4)
    back = [InlineKeyboardButton("✖️ Cancelar", callback_data="x")]
    if home is not None:
        back.insert(0, InlineKeyboardButton("↩️ Voltar", callback_data=f"p:{match_id}"))
    layout.append(back)
    return InlineKeyboardMarkup(layout)


def confirm_delete_keyboard(match_id: int) -> InlineKeyboardMarkup:
    """Yes/no buttons for deleting a match.

    Args:
        match_id: Match identifier.

    Returns:
        Keyboard.
    """
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🗑 Apagar", callback_data=f"dm:{match_id}:y"),
                InlineKeyboardButton("Cancelar", callback_data=f"dm:{match_id}:n"),
            ]
        ]
    )


def competitions_keyboard(rows: Iterable[Row]) -> InlineKeyboardMarkup:
    """Toggle buttons for competitions with a provider.

    Args:
        rows: Competition rows.

    Returns:
        Keyboard.
    """
    buttons = [
        [
            InlineKeyboardButton(
                f"{'🔄' if row['auto_sync'] else '✋'} {row['code']}",
                callback_data=f"ct:{row['competition_id']}",
            )
        ]
        for row in rows
        if row["provider"]
    ]
    return InlineKeyboardMarkup(buttons)


def track_keyboard(matches: Iterable[ApiMatch], tz_name: str) -> InlineKeyboardMarkup:
    """Buttons to track provider fixtures.

    Args:
        matches: Provider matches.
        tz_name: IANA timezone.

    Returns:
        Keyboard.
    """
    buttons = []
    for match in matches:
        when = format_kickoff(match.kickoff_at[:10], match.kickoff_at, tz_name)
        home = match.home_short or match.home_name
        away = match.away_short or match.away_name
        buttons.append(
            [
                InlineKeyboardButton(
                    f"{home} x {away} · {when}"[:60],
                    callback_data=f"tr:{match.external_id}",
                )
            ]
        )
    return InlineKeyboardMarkup(buttons)


def team_suggestions_keyboard(
    suggestions: Iterable[tuple[int, str]], typed: str
) -> InlineKeyboardMarkup:
    """Suggested existing teams plus a "create new" button.

    Args:
        suggestions: ``(team_id, name)`` pairs.
        typed: Name typed by the admin.

    Returns:
        Keyboard.
    """
    buttons = [
        [InlineKeyboardButton(f"✅ {name}", callback_data=f"ns:{team_id}")]
        for team_id, name in suggestions
    ]
    buttons.append(
        [InlineKeyboardButton(f"🆕 Criar “{typed[:30]}”", callback_data="ns:new")]
    )
    return InlineKeyboardMarkup(buttons)


def competition_picker_keyboard(rows: Iterable[Row]) -> InlineKeyboardMarkup:
    """Competition choice for a new manual match.

    Args:
        rows: Competition rows.

    Returns:
        Keyboard with a "new competition" button.
    """
    buttons = [
        InlineKeyboardButton(row["name"][:30], callback_data=f"nc:{row['code']}")
        for row in rows
    ]
    layout = _rows(buttons, 2)
    layout.append([InlineKeyboardButton("➕ Nova competição", callback_data="nc:+")])
    return InlineKeyboardMarkup(layout)


def confirm_new_match_keyboard() -> InlineKeyboardMarkup:
    """Create/cancel buttons for /novojogo.

    Returns:
        Keyboard.
    """
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Criar jogo", callback_data="nk:y"),
                InlineKeyboardButton("✖️ Cancelar", callback_data="nk:n"),
            ]
        ]
    )
