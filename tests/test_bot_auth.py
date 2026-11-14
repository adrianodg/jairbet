"""Tests for bot access rules."""

from pathlib import Path

import pytest

from jairbet.bot.auth import resolve_access
from jairbet.config import Settings

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
