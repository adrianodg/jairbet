"""Timezone helpers: database UTC timestamps, local dates and bet locking."""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from jairbet.config import DEFAULT_TIMEZONE

DB_FORMAT = "%Y-%m-%d %H:%M:%S"


def local_tz(name: str = DEFAULT_TIMEZONE) -> ZoneInfo:
    """Return the configured local timezone.

    Args:
        name: IANA timezone name.

    Returns:
        Timezone object.
    """
    return ZoneInfo(name)


def utc_now() -> datetime:
    """Return the current time as an aware UTC datetime.

    Returns:
        Current UTC time.
    """
    return datetime.now(timezone.utc)


def to_db(moment: datetime) -> str:
    """Format an aware datetime as the UTC text stored in SQLite.

    Args:
        moment: Aware datetime.

    Returns:
        ``YYYY-MM-DD HH:MM:SS`` in UTC.

    Raises:
        ValueError: If the datetime is naive.
    """
    if moment.tzinfo is None:
        raise ValueError("Datetime must be timezone-aware")
    return moment.astimezone(timezone.utc).strftime(DB_FORMAT)


def from_db(text: str) -> datetime:
    """Parse a UTC timestamp stored in SQLite.

    Args:
        text: ``YYYY-MM-DD HH:MM:SS`` UTC text.

    Returns:
        Aware UTC datetime.
    """
    return datetime.strptime(text, DB_FORMAT).replace(tzinfo=timezone.utc)


def parse_iso_utc(text: str) -> datetime:
    """Parse an ISO-8601 timestamp such as ``2026-05-01T22:00:00Z``.

    Args:
        text: ISO timestamp; naive values are assumed to be UTC.

    Returns:
        Aware UTC datetime.
    """
    moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def local_date(moment: datetime, tz_name: str = DEFAULT_TIMEZONE) -> date:
    """Return the calendar date of an instant in the local timezone.

    Args:
        moment: Aware datetime.
        tz_name: IANA timezone name.

    Returns:
        Local date.
    """
    return moment.astimezone(local_tz(tz_name)).date()


def local_datetime_to_utc(
    day: date, clock: time, tz_name: str = DEFAULT_TIMEZONE
) -> datetime:
    """Combine a local date and time into an aware UTC datetime.

    Args:
        day: Local calendar date.
        clock: Local wall-clock time.
        tz_name: IANA timezone name.

    Returns:
        Aware UTC datetime.
    """
    return datetime.combine(day, clock, tzinfo=local_tz(tz_name)).astimezone(
        timezone.utc
    )


def lock_time(
    match_date: str, kickoff_at: str | None, tz_name: str = DEFAULT_TIMEZONE
) -> datetime:
    """Return when bets on a match close.

    Matches with a kickoff lock at kickoff. Legacy matches without one lock at
    the end of their local match date.

    Args:
        match_date: ISO match date.
        kickoff_at: Stored UTC kickoff text, or ``None``.
        tz_name: IANA timezone name.

    Returns:
        Aware UTC lock time.
    """
    if kickoff_at:
        return from_db(kickoff_at)
    next_day = date.fromisoformat(match_date) + timedelta(days=1)
    return local_datetime_to_utc(next_day, time(0, 0), tz_name)


def is_locked(
    match_date: str,
    kickoff_at: str | None,
    now: datetime | None = None,
    tz_name: str = DEFAULT_TIMEZONE,
) -> bool:
    """Tell whether bets on a match are closed.

    Args:
        match_date: ISO match date.
        kickoff_at: Stored UTC kickoff text, or ``None``.
        now: Current time, defaulting to the real clock.
        tz_name: IANA timezone name.

    Returns:
        ``True`` once the lock time has passed.
    """
    return (now or utc_now()) >= lock_time(match_date, kickoff_at, tz_name)


def format_kickoff(
    match_date: str, kickoff_at: str | None, tz_name: str = DEFAULT_TIMEZONE
) -> str:
    """Format a match time for pt-BR messages, e.g. ``sáb 15/11 16:00``.

    Args:
        match_date: ISO match date.
        kickoff_at: Stored UTC kickoff text, or ``None``.
        tz_name: IANA timezone name.

    Returns:
        Short local date and time text.
    """
    weekdays = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
    if kickoff_at:
        moment = from_db(kickoff_at).astimezone(local_tz(tz_name))
        return f"{weekdays[moment.weekday()]} {moment:%d/%m %H:%M}"
    day = date.fromisoformat(match_date)
    return f"{weekdays[day.weekday()]} {day:%d/%m}"
