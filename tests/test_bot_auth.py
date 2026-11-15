"""Tests for bot access rules."""

from dataclasses import replace
from pathlib import Path

import pytest

from jairbet.bot.auth import resolve_access
from jairbet.bot.main import validate_settings
from jairbet.config import Settings, load_settings

SETTINGS = Settings(
    telegram_token="t",
    football_data_token="",
    admin_ids=frozenset({1}),
    jair_id=2,
    group_chat_id=-100,
    db_path=Path("x.db"),
    backup_dir=Path("b"),
    timezone="America/Sao_Paulo",
)


@pytest.mark.parametrize(
    ("user", "chat", "chat_type", "read", "bet", "admin"),
    [
        (1, 1, "private", True, False, True),
        (2, 2, "private", True, True, False),
        (2, -100, "group", True, False, False),
        (3, -100, "supergroup", True, False, False),
        (3, 3, "private", False, False, False),
        (1, -555, "group", True, False, False),
        (3, -555, "group", False, False, False),
    ],
)
def test_access_matrix(
    user: int, chat: int, chat_type: str, read: bool, bet: bool, admin: bool
) -> None:
    """Admins edit, Jair bets in private, the group only reads."""
    access = resolve_access(SETTINGS, user, chat, chat_type)
    assert (access.can_read, access.can_bet, access.can_admin) == (read, bet, admin)


def test_missing_ids_allow_setup_mode() -> None:
    """Only the bot token is fatal, so /chatid can be used to find the IDs."""
    settings = replace(load_settings(None), telegram_token="x", admin_ids=frozenset())
    assert validate_settings(settings) == []
    assert validate_settings(replace(settings, telegram_token="")) != []
