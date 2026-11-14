"""Scheduled jobs (sync, reminders, backups) and notification helpers."""

import asyncio
from datetime import datetime, time, timedelta
from pathlib import Path

from telegram import Bot
from telegram.error import TelegramError
from telegram.ext import Application, ContextTypes

from jairbet.bot import formatters, keyboards
from jairbet.config import (
    BACKUP_KEEP_DAYS,
    DAILY_BACKUP_TIME,
    DAILY_REMINDER_TIME,
    DAILY_SYNC_TIME,
    JAIR_NAME,
    LIVE_SYNC_INTERVAL_MINUTES,
    LIVE_SYNC_WINDOW_HOURS,
    PRE_KICKOFF_REMINDER_MINUTES,
    Settings,
)
from jairbet.console import error, info, warning
from jairbet.db import backup_database, connect, rotate_backups, session
from jairbet.providers.football_data import FootballDataProvider
from jairbet.repository import (
    bets_for_match,
    get_bettor_id,
    get_competitions,
    get_match,
    get_ranking,
    list_matches,
    matches_in_progress,
    matches_kicking_off,
)
from jairbet.services import bet_is_open
from jairbet.sync import SyncResult, run_sync, sync_tracked
from jairbet.timeutil import local_date, local_tz, to_db, utc_now


def _clock(text: str, tz_name: str) -> time:
    """Parse ``HH:MM`` into an aware local time.

    Args:
        text: Time text.
        tz_name: IANA timezone.

    Returns:
        Time with timezone.
    """
    hour, minute = (int(part) for part in text.split(":"))
    return time(hour, minute, tzinfo=local_tz(tz_name))


def sync_blocking(
    settings: Settings, codes: list[str] | None = None, live_only: bool = False
) -> tuple[list[SyncResult], list[str]]:
    """Run a sync in the current thread with its own connection.

    Args:
        settings: Runtime settings.
        codes: Competitions to sync; defaults to every auto-synced one.
        live_only: Only sync when matches are in progress, for today's window.

    Returns:
        Results and error messages.
    """
    if not settings.football_data_token:
        return [], ["FOOTBALL_DATA_TOKEN não configurado: sincronização desativada."]
    provider = FootballDataProvider(settings.football_data_token)
    connection = connect(settings.db_path)
    results: list[SyncResult] = []
    errors: list[str] = []
    try:
        date_from = date_to = None
        if live_only:
            now = utc_now()
            window_start = now - timedelta(hours=LIVE_SYNC_WINDOW_HOURS)
            running = matches_in_progress(connection, to_db(window_start), to_db(now))
            if not running:
                return [], []
            codes = sorted(
                {
                    row["competition_code"]
                    for row in running
                    if row["competition_auto_sync"] and row["competition_provider"]
                }
            )
            date_from, date_to = (now - timedelta(days=1)).date(), now.date()
        elif codes is None:
            codes = [
                row["code"]
                for row in get_competitions(connection)
                if row["auto_sync"] and row["provider"]
            ]
        for code in codes or []:
            try:
                results.append(
                    run_sync(
                        connection,
                        provider,
                        code,
                        date_from,
                        date_to,
                        settings.timezone,
                    )
                )
            except Exception as exc:  # reported to the admin
                errors.append(f"{code}: {exc}")
                error(f"Sync {code} failed: {exc}")
        try:
            tracked = sync_tracked(connection, provider, settings.timezone)
            if tracked.fetched:
                results.append(tracked)
        except Exception as exc:  # reported to the admin
            errors.append(f"jogos avulsos: {exc}")
            error(f"Tracked sync failed: {exc}")
    finally:
        connection.close()
        provider.close()
    return results, errors


async def send_safe(bot: Bot, chat_id: int, text: str, **kwargs: object) -> None:
    """Send a message, logging (not raising) Telegram errors.

    Args:
        bot: Telegram bot.
        chat_id: Target chat.
        text: HTML text.
        **kwargs: Extra ``send_message`` arguments.
    """
    try:
        await bot.send_message(chat_id, text, **kwargs)  # type: ignore[arg-type]
    except TelegramError as exc:
        warning(f"Could not message chat {chat_id}: {exc}")


async def notify_admins(bot: Bot, settings: Settings, text: str) -> None:
    """Send a message to every admin.

    Args:
        bot: Telegram bot.
        settings: Runtime settings.
        text: HTML text.
    """
    for admin_id in settings.admin_ids:
        await send_safe(bot, admin_id, text)


async def broadcast(bot: Bot, settings: Settings, text: str) -> None:
    """Post to the group, or privately to Jair and the admins without a group.

    Args:
        bot: Telegram bot.
        settings: Runtime settings.
        text: HTML text.
    """
    if settings.group_chat_id is not None:
        await send_safe(bot, settings.group_chat_id, text)
        return
    targets = set(settings.admin_ids)
    if settings.jair_id is not None:
        targets.add(settings.jair_id)
    for chat_id in targets:
        await send_safe(bot, chat_id, text)


async def announce_results(bot: Bot, settings: Settings, match_ids: list[int]) -> None:
    """Announce finished matches that have bets.

    Args:
        bot: Telegram bot.
        settings: Runtime settings.
        match_ids: Finished match IDs.
    """
    messages = []
    with session(settings.db_path) as connection:
        for match_id in dict.fromkeys(match_ids):
            match = get_match(connection, match_id)
            bets = bets_for_match(connection, match_id)
            if match is None or not bets:
                continue
            messages.append(
                formatters.result_announcement(match, bets, get_ranking(connection))
            )
    for text in messages:
        await broadcast(bot, settings, text)


async def handle_sync_outcome(
    application: Application,
    results: list[SyncResult],
    errors: list[str],
) -> None:
    """Announce results, report new teams, and alert admins about errors once.

    Args:
        application: Bot application.
        results: Sync results.
        errors: Error messages.
    """
    settings: Settings = application.bot_data["settings"]
    finished = [
        event.match_id
        for result in results
        for event in result.events
        if event.kind == "finished" and event.match_id is not None
    ]
    await announce_results(application.bot, settings, finished)
    new_teams = sorted(
        {
            event.detail
            for result in results
            for event in result.events
            if event.kind == "unknown_team"
        }
    )
    if new_teams:
        await notify_admins(
            application.bot,
            settings,
            "🆕 Times novos criados pela sincronização: "
            + ", ".join(formatters.esc(name) for name in new_teams)
            + "\nSe algum for outro nome de um time existente, use "
            "/alias &lt;nome&gt; = &lt;time&gt;.",
        )
    error_text = "\n".join(errors)
    if error_text and application.bot_data.get("last_sync_error") != error_text:
        await notify_admins(
            application.bot,
            settings,
            "⚠️ Falha na sincronização:\n" + formatters.esc(error_text),
        )
    application.bot_data["last_sync_error"] = error_text


async def job_daily_sync(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Full-season sync of every auto-synced competition.

    Args:
        context: Job context.
    """
    settings: Settings = context.bot_data["settings"]
    results, errors = await asyncio.to_thread(sync_blocking, settings)
    await handle_sync_outcome(context.application, results, errors)


async def job_live_sync(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sync today's window while matches are in progress.

    Args:
        context: Job context.
    """
    settings: Settings = context.bot_data["settings"]
    if not settings.football_data_token:
        return
    results, errors = await asyncio.to_thread(sync_blocking, settings, None, True)
    await handle_sync_outcome(context.application, results, errors)


def _open_without_bet(settings: Settings, day_from: str, day_to: str) -> list[object]:
    """List open matches in a local date range that Jair has not bet on.

    Args:
        settings: Runtime settings.
        day_from: First local ISO date.
        day_to: Last local ISO date.

    Returns:
        Match rows.
    """
    with session(settings.db_path) as connection:
        jair_id = get_bettor_id(connection, JAIR_NAME)
        rows = list_matches(connection, day_from, day_to, bettor_id=jair_id)
    return [
        row
        for row in rows
        if row["pred_home"] is None and bet_is_open(row, tz_name=settings.timezone)
    ]


async def job_daily_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remind Jair about today's matches without a bet.

    Args:
        context: Job context.
    """
    settings: Settings = context.bot_data["settings"]
    if settings.jair_id is None:
        return
    today = local_date(utc_now(), settings.timezone).isoformat()
    pending = _open_without_bet(settings, today, today)
    if not pending:
        return
    text = formatters.matches_message(
        pending, "⏰ Jogos de hoje sem palpite", settings.timezone, show_bet=False
    )
    await send_safe(
        context.bot,
        settings.jair_id,
        text,
        reply_markup=keyboards.matches_keyboard(pending, settings.timezone),
    )


async def job_pre_kickoff(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ping Jair about a kickoff in about an hour if he has not bet yet.

    Args:
        context: Job context.
    """
    settings: Settings = context.bot_data["settings"]
    if settings.jair_id is None:
        return
    now = utc_now()
    start = now + timedelta(
        minutes=PRE_KICKOFF_REMINDER_MINUTES - LIVE_SYNC_INTERVAL_MINUTES
    )
    end = now + timedelta(minutes=PRE_KICKOFF_REMINDER_MINUTES)
    reminded: set[int] = context.bot_data.setdefault("reminded", set())
    with session(settings.db_path) as connection:
        jair_id = get_bettor_id(connection, JAIR_NAME)
        upcoming = matches_kicking_off(connection, to_db(start), to_db(end))
        pending = [
            match
            for match in upcoming
            if match["match_id"] not in reminded
            and jair_id is not None
            and connection.execute(
                "SELECT 1 FROM bet WHERE match_id = ? AND bettor_id = ?",
                (match["match_id"], jair_id),
            ).fetchone()
            is None
        ]
    if not pending:
        return
    reminded.update(match["match_id"] for match in pending)
    text = formatters.matches_message(
        pending,
        "⏳ Começa em menos de 1 hora e você não palpitou",
        settings.timezone,
        show_bet=False,
    )
    await send_safe(
        context.bot,
        settings.jair_id,
        text,
        reply_markup=keyboards.matches_keyboard(pending, settings.timezone),
    )


def make_backup(settings: Settings) -> Path:
    """Write a timestamped backup and delete old ones.

    Args:
        settings: Runtime settings.

    Returns:
        Path of the new backup.
    """
    stamp = datetime.now(local_tz(settings.timezone)).strftime("%Y%m%d-%H%M")
    target = settings.backup_dir / f"jairbet-{stamp}.db"
    connection = connect(settings.db_path)
    try:
        backup_database(connection, target)
    finally:
        connection.close()
    rotate_backups(settings.backup_dir, BACKUP_KEEP_DAYS)
    info(f"Backup written to {target}")
    return target


async def send_backup(bot: Bot, chat_id: int, path: Path) -> None:
    """Send a backup file as a Telegram document.

    Args:
        bot: Telegram bot.
        chat_id: Target chat.
        path: Backup file.
    """
    try:
        with path.open("rb") as handle:
            await bot.send_document(chat_id, handle, filename=path.name)
    except TelegramError as exc:
        warning(f"Could not send backup to {chat_id}: {exc}")


async def job_backup(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Nightly backup; on Sundays also send it to the admins.

    Args:
        context: Job context.
    """
    settings: Settings = context.bot_data["settings"]
    try:
        path = await asyncio.to_thread(make_backup, settings)
    except OSError as exc:
        error(f"Backup failed: {exc}")
        await notify_admins(context.bot, settings, f"⚠️ Backup falhou: {exc}")
        return
    if datetime.now(local_tz(settings.timezone)).weekday() == 6:
        for admin_id in settings.admin_ids:
            await send_backup(context.bot, admin_id, path)


def register_jobs(application: Application, settings: Settings) -> None:
    """Schedule every recurring job.

    Args:
        application: Bot application with a job queue.
        settings: Runtime settings.

    Raises:
        RuntimeError: If the job queue extra is not installed.
    """
    queue = application.job_queue
    if queue is None:
        raise RuntimeError("Install python-telegram-bot[job-queue]")
    tz_name = settings.timezone
    queue.run_daily(job_daily_sync, _clock(DAILY_SYNC_TIME, tz_name), name="sync")
    queue.run_daily(
        job_daily_reminder, _clock(DAILY_REMINDER_TIME, tz_name), name="reminder"
    )
    queue.run_daily(job_backup, _clock(DAILY_BACKUP_TIME, tz_name), name="backup")
    interval = LIVE_SYNC_INTERVAL_MINUTES * 60
    queue.run_repeating(job_live_sync, interval, first=60, name="live-sync")
    queue.run_repeating(job_pre_kickoff, interval, first=90, name="pre-kickoff")
