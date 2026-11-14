"""Who may do what: admins, Jair and the notification group."""

import functools
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from jairbet.config import Settings

Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[Any]]
LEVELS = ("read", "bet", "admin")


@dataclass(frozen=True)
class Access:
    """Permissions of one Telegram user in one chat.

    Attributes:
        user_id: Telegram user ID, if known.
        is_admin: The user is an admin.
        is_jair: The user is Jair.
        is_private: The chat is a private chat with the bot.
        in_group: The chat is the configured notification group.
    """

    user_id: int | None
    is_admin: bool
    is_jair: bool
    is_private: bool
    in_group: bool

    @property
    def can_read(self) -> bool:
        """Whether read-only commands are allowed."""
        return self.is_admin or self.is_jair or self.in_group

    @property
    def can_bet(self) -> bool:
        """Whether the user can place Jair's bets (private chat only)."""
        return self.is_jair and self.is_private

    @property
    def can_admin(self) -> bool:
        """Whether admin commands are allowed (private chat only)."""
        return self.is_admin and self.is_private

    def allows(self, level: str) -> bool:
        """Check a permission level.

        Args:
            level: ``read``, ``bet`` or ``admin``.

        Returns:
            Whether the level is allowed.
        """
        return {
            "read": self.can_read,
            "bet": self.can_bet,
            "admin": self.can_admin,
        }[level]


def resolve_access(
    settings: Settings, user_id: int | None, chat_id: int | None, chat_type: str | None
) -> Access:
    """Compute the permissions for a user in a chat.

    Args:
        settings: Runtime settings with the authorised IDs.
        user_id: Telegram user ID.
        chat_id: Telegram chat ID.
        chat_type: Telegram chat type (``private``, ``group``, ...).

    Returns:
        The user's access.
    """
    return Access(
        user_id=user_id,
        is_admin=user_id is not None and user_id in settings.admin_ids,
        is_jair=user_id is not None and user_id == settings.jair_id,
        is_private=chat_type == ChatType.PRIVATE,
        in_group=settings.group_chat_id is not None
        and chat_id == settings.group_chat_id,
    )


def access_for(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Access:
    """Compute the access of the user behind an update.

    Args:
        update: Telegram update.
        context: Handler context holding ``settings`` in ``bot_data``.

    Returns:
        The user's access.
    """
    settings: Settings = context.bot_data["settings"]
    user = update.effective_user
    chat = update.effective_chat
    return resolve_access(
        settings,
        user.id if user else None,
        chat.id if chat else None,
        chat.type if chat else None,
    )


REFUSALS = {
    "read": "🚫 Este bot é privado.",
    "bet": "🚫 Só o Jair pode palpitar, e apenas no chat privado com o bot.",
    "admin": "🚫 Comando exclusivo do admin (no chat privado com o bot).",
}


def restricted(level: str) -> Callable[[Handler], Handler]:
    """Decorate a handler so it only runs for users with ``level`` access.

    Args:
        level: ``read``, ``bet`` or ``admin``.

    Returns:
        Decorator.

    Raises:
        ValueError: If the level is unknown.
    """
    if level not in LEVELS:
        raise ValueError(f"Unknown access level {level!r}")

    def decorator(handler: Handler) -> Handler:
        @functools.wraps(handler)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Any:
            access = access_for(update, context)
            if access.allows(level):
                return await handler(update, context)
            if update.callback_query is not None:
                await update.callback_query.answer(REFUSALS[level], show_alert=True)
            elif update.effective_message is not None and (
                access.is_private or access.can_read
            ):
                await update.effective_message.reply_text(REFUSALS[level])
            return None

        return wrapper

    return decorator
