"""Build and run the Telegram bot (long polling)."""

import logging
import sqlite3
import warnings

from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    Defaults,
    MessageHandler,
    filters,
)
from telegram.warnings import PTBUserWarning

from jairbet.bettors import backfill
from jairbet.bot.handlers import admin, user
from jairbet.bot.jobs import register_jobs
from jairbet.config import JAIR_NAME, Settings, load_settings
from jairbet.console import error, info, success, warning
from jairbet.db import connect, migrate
from jairbet.repository import seed_team_aliases, set_bettor_telegram_id
from jairbet.timeutil import local_tz

PUBLIC_COMMANDS = [
    BotCommand("jogos", "Próximos jogos"),
    BotCommand("palpitar", "Fazer um palpite"),
    BotCommand("meuspalpites", "Meus palpites em aberto"),
    BotCommand("resultados", "Últimos resultados"),
    BotCommand("ranking", "Classificação"),
    BotCommand("stats", "Gráficos"),
    BotCommand("corvo", "Palpites do Corvo"),
    BotCommand("time", "Forma de um time"),
    BotCommand("ajuda", "Ajuda"),
]

logger = logging.getLogger("jairbet")


def validate_settings(settings: Settings) -> list[str]:
    """Check settings, printing warnings for optional values.

    Args:
        settings: Runtime settings.

    Returns:
        Fatal problems (empty when the bot can start).
    """
    problems = []
    if not settings.telegram_token:
        problems.append("TELEGRAM_BOT_TOKEN is not set (create a bot with @BotFather)")
    if not settings.admin_ids:
        problems.append("ADMIN_TELEGRAM_IDS is not set (your Telegram user ID)")
    if settings.jair_id is None:
        warning("JAIR_TELEGRAM_ID is not set: nobody can place bets yet")
    if not settings.football_data_token:
        warning("FOOTBALL_DATA_TOKEN is not set: automatic sync is disabled")
    if settings.group_chat_id is None:
        warning("GROUP_CHAT_ID is not set: results are sent privately instead")
    return problems


def prepare_database(settings: Settings) -> None:
    """Migrate the database and link Jair's Telegram account.

    Args:
        settings: Runtime settings.
    """
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(settings.db_path)
    try:
        version = migrate(connection)
        with connection:
            set_bettor_telegram_id(connection, JAIR_NAME, settings.jair_id)
            seed_team_aliases(connection)
            tipped = backfill(connection)
    finally:
        connection.close()
    info(f"Database {settings.db_path} at schema v{version}, {tipped} tipped matches")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log unexpected errors and tell the user something went wrong.

    Args:
        update: The update being processed, if any.
        context: Handler context holding the error.
    """
    logger.error("Unhandled error", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message is not None:
        try:
            await update.effective_message.reply_text(
                "⚠️ Ocorreu um erro inesperado. O admin pode ver os detalhes no log."
            )
        except Exception:  # never let the error handler fail
            pass


async def post_init(application: Application) -> None:
    """Register the command menu shown by Telegram clients.

    Args:
        application: Bot application.
    """
    await application.bot.set_my_commands(PUBLIC_COMMANDS)
    me = await application.bot.get_me()
    success(f"Bot @{me.username} is running")


def build_application(settings: Settings) -> Application:
    """Create the bot application with every handler and job.

    Args:
        settings: Runtime settings.

    Returns:
        Configured application (not started).
    """
    defaults = Defaults(
        parse_mode=ParseMode.HTML,
        tzinfo=local_tz(settings.timezone),
        link_preview_options=None,
    )
    application = (
        ApplicationBuilder()
        .token(settings.telegram_token)
        .defaults(defaults)
        .post_init(post_init)
        .build()
    )
    application.bot_data["settings"] = settings
    private = filters.ChatType.PRIVATE

    application.add_handler(admin.new_match_conversation())
    commands = {
        "chatid": user.chatid,
        "start": user.start,
        "ajuda": user.start,
        "help": user.start,
        "jogos": user.jogos,
        "jogo": user.jogo,
        "palpitar": user.palpitar,
        "meuspalpites": user.meuspalpites,
        "resultados": user.resultados,
        "ranking": user.ranking,
        "stats": user.stats_cmd,
        "time": user.time_cmd,
        "corvo": user.corvo_cmd,
        "buscarjogo": admin.buscarjogo,
        "editarjogo": admin.editarjogo,
        "placar": admin.placar,
        "apagarjogo": admin.apagarjogo,
        "editarpalpite": admin.editarpalpite,
        "pontuacao": admin.pontuacao,
        "competicoes": admin.competicoes,
        "alias": admin.alias,
        "sync": admin.sync_cmd,
        "log": admin.log_cmd,
        "backup": admin.backup_cmd,
        "exportar": admin.exportar,
        "importar": admin.importar,
        "cancelar": admin.cancel,
    }
    for name, callback in commands.items():
        application.add_handler(CommandHandler(name, callback))
    callbacks = {
        r"^p:\d+$": user.on_pick_match,
        r"^gh:\d+:\d+$": user.on_home_goals,
        r"^ga:\d+:\d+:\d+$": user.on_away_goals,
        r"^x$": user.on_cancel_picker,
        r"^dm:\d+:[yn]$": admin.on_delete_match,
        r"^ct:\d+$": admin.on_toggle_competition,
        r"^tr:\w+$": admin.on_track,
    }
    for pattern, callback in callbacks.items():
        application.add_handler(CallbackQueryHandler(callback, pattern=pattern))
    application.add_handler(
        MessageHandler(filters.Document.ALL & private, admin.on_document)
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND & private, user.on_score_text)
    )
    application.add_error_handler(on_error)
    register_jobs(application, settings)
    return application


def main() -> None:
    """Load settings, prepare the database and run the bot until stopped."""
    logging.basicConfig(
        format="%(levelname)s %(name)s: %(message)s", level=logging.INFO
    )
    # httpx logs every request URL at INFO, and Telegram URLs contain the token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    warnings.filterwarnings("ignore", category=PTBUserWarning)
    try:
        settings = load_settings()
    except ValueError as exc:
        error(f"Invalid settings: {exc}")
        raise SystemExit(1) from exc
    problems = validate_settings(settings)
    if problems:
        for problem in problems:
            error(problem)
        error("Copy .env.example to .env and fill it in.")
        raise SystemExit(1)
    try:
        prepare_database(settings)
    except (OSError, sqlite3.Error) as exc:
        error(f"Database error: {exc}")
        raise SystemExit(1) from exc
    application = build_application(settings)
    info("Starting long polling (Ctrl+C to stop)")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
