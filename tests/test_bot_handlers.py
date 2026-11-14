"""Smoke tests driving the bot handlers with mocked Telegram objects."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from jairbet.bot.handlers import admin, user
from jairbet.config import Settings
from jairbet.db import connect, init_db
from jairbet.repository import add_match, add_team, bets_for_match
from jairbet.timeutil import to_db, utc_now

ADMIN, JAIR, STRANGER = 1, 2, 3


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    """Create a database with one upcoming and one past match.

    Args:
        tmp_path: Temporary folder.

    Returns:
        Settings pointing at the database.
    """
    db_path = tmp_path / "bot.db"
    connection = connect(db_path)
    init_db(connection)
    flamengo, santos = add_team(connection, "Flamengo"), add_team(connection, "Santos")
    tomorrow = utc_now() + timedelta(days=1)
    add_match(
        connection,
        tomorrow.date().isoformat(),
        flamengo,
        santos,
        kickoff_at=to_db(tomorrow),
    )
    connection.commit()
    connection.close()
    return Settings(
        telegram_token="t",
        football_data_token="",
        admin_ids=frozenset({ADMIN}),
        jair_id=JAIR,
        group_chat_id=None,
        db_path=db_path,
        backup_dir=tmp_path / "backups",
        timezone="America/Sao_Paulo",
    )


def _update(user_id: int, data: str | None = None) -> MagicMock:
    """Build a private-chat update from a user.

    Args:
        user_id: Telegram user ID.
        data: Callback data, for callback-query updates.

    Returns:
        Mocked update.
    """
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat.id = user_id
    update.effective_chat.type = "private"
    update.effective_message.reply_text = AsyncMock()
    update.effective_message.reply_media_group = AsyncMock()
    if data is None:
        update.callback_query = None
    else:
        update.callback_query.data = data
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()
    return update


def _context(settings: Settings, *args: str) -> SimpleNamespace:
    """Build a handler context.

    Args:
        settings: Runtime settings.
        *args: Command arguments.

    Returns:
        Context-like object.
    """
    return SimpleNamespace(
        bot_data={"settings": settings},
        user_data={},
        args=list(args),
        bot=AsyncMock(),
        application=SimpleNamespace(bot=AsyncMock(), bot_data={"settings": settings}),
    )


def _run(
    handler: Callable[..., Awaitable[Any]], update: MagicMock, context: Any
) -> None:
    """Run an async handler.

    Args:
        handler: Handler coroutine function.
        update: Mocked update.
        context: Context-like object.
    """
    asyncio.run(handler(update, context))


def _replies(update: MagicMock) -> str:
    """Join every text the handler sent.

    Args:
        update: Mocked update.

    Returns:
        Concatenated reply texts.
    """
    calls = update.effective_message.reply_text.call_args_list
    if update.callback_query is not None:
        calls += update.callback_query.edit_message_text.call_args_list
    return "\n".join(str(call.args[0]) for call in calls)


def _bet_count(settings: Settings) -> int:
    """Count bets on match 1.

    Args:
        settings: Runtime settings.

    Returns:
        Number of bets.
    """
    with connect(settings.db_path) as connection:
        return len(bets_for_match(connection, 1))


def test_jair_bets_with_a_command(settings: Settings) -> None:
    """/palpitar 1 2x1 saves the bet and the animals answer."""
    update = _update(JAIR)
    _run(user.palpitar, update, _context(settings, "1", "2x1"))
    assert "Palpite salvo" in _replies(update)
    assert "Caranguejo" in _replies(update)
    assert _bet_count(settings) == 4


def test_jair_bets_with_buttons(settings: Settings) -> None:
    """The match → home goals → away goals flow saves the bet."""
    context = _context(settings)
    for data, handler in (
        ("p:1", user.on_pick_match),
        ("gh:1:3", user.on_home_goals),
        ("ga:1:3:0", user.on_away_goals),
    ):
        update = _update(JAIR, data)
        _run(handler, update, context)
    assert "Palpite salvo" in _replies(update)
    assert _bet_count(settings) == 4


def test_typed_score_after_picking_a_match(settings: Settings) -> None:
    """Typing 1x1 after choosing a match saves it."""
    context = _context(settings)
    _run(user.on_pick_match, _update(JAIR, "p:1"), context)
    update = _update(JAIR)
    update.effective_message.text = "1 x 1"
    _run(user.on_score_text, update, context)
    assert "Palpite salvo" in _replies(update)


def test_strangers_and_admin_cannot_bet(settings: Settings) -> None:
    """Only Jair places bets."""
    for user_id in (STRANGER, ADMIN):
        update = _update(user_id)
        _run(user.palpitar, update, _context(settings, "1", "2x1"))
        assert "🚫" in _replies(update)
    assert _bet_count(settings) == 0


def test_admin_result_announces_and_ranks(settings: Settings) -> None:
    """/placar sets the result, notifies and the ranking shows the points."""
    _run(user.palpitar, _update(JAIR), _context(settings, "1", "2x1"))
    context = _context(settings, "1", "2x1")
    update = _update(ADMIN)
    _run(admin.placar, update, context)
    assert "Placar salvo" in _replies(update)
    sent = [call.args[1] for call in context.bot.send_message.call_args_list]
    assert any("Fim de jogo" in text for text in sent)
    ranking_update = _update(JAIR)
    _run(user.ranking, ranking_update, _context(settings))
    assert "Jair" in _replies(ranking_update)
    assert "6" in _replies(ranking_update)


def test_read_commands_work_on_empty_and_full_db(settings: Settings) -> None:
    """Listing commands answer without errors."""
    for handler, args in (
        (user.jogos, ()),
        (user.jogos, ("semana",)),
        (user.resultados, ()),
        (user.ranking, ("mes",)),
        (user.corvo_cmd, ()),
        (user.corvo_cmd, ("1",)),
        (user.time_cmd, ("flamengo",)),
        (user.jogo, ("1",)),
        (user.start, ()),
    ):
        update = _update(JAIR)
        _run(handler, update, _context(settings, *args))
        assert update.effective_message.reply_text.await_count >= 1


def test_stats_sends_five_charts(settings: Settings) -> None:
    """/stats renders an album of charts, even with little data."""
    update = _update(JAIR)
    _run(user.stats_cmd, update, _context(settings))
    media = update.effective_message.reply_media_group.call_args.args[0]
    assert len(media) == 5


def test_admin_edit_commands(settings: Settings) -> None:
    """Admin can edit a match, any bet and the scoring."""
    _run(user.palpitar, _update(JAIR), _context(settings, "1", "1x0"))
    for handler, args, expected in (
        (admin.editarjogo, ("1", "hora", "20:00"), "atualizado"),
        (admin.editarpalpite, ("1", "Corvo", "3x3"), "não será recalculado"),
        (admin.pontuacao, ("2", "5"), "já disputados"),
        (admin.alias, ("Mengão", "=", "Flamengo"), "salvo"),
        (admin.log_cmd, (), "alterações"),
    ):
        update = _update(ADMIN)
        _run(handler, update, _context(settings, *args))
        assert expected in _replies(update)
