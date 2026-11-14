"""SQLite connection, migration and backup helpers."""

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from jairbet.config import (
    BETTOR_KINDS,
    MANUAL_CODE,
    MIGRATIONS_DIR,
    SEED_COMPETITIONS,
)
from jairbet.console import info, success

MIGRATION_RE = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


def connect(path: str | Path = ":memory:") -> sqlite3.Connection:
    """Open a SQLite connection with foreign keys and WAL enabled.

    Args:
        path: Database file path or SQLite special path.

    Returns:
        A configured SQLite connection returning mapping-like rows.
    """
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        connection.execute("PRAGMA journal_mode = WAL")
    return connection


@contextmanager
def session(path: str | Path) -> Iterator[sqlite3.Connection]:
    """Open a connection, commit on success, roll back on error, then close.

    Args:
        path: Database file path.

    Yields:
        Open database connection inside a transaction.
    """
    connection = connect(path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _migrations() -> list[tuple[int, Path]]:
    """List migration files ordered by version.

    Returns:
        Pairs of version number and SQL file path.
    """
    found = []
    for path in MIGRATIONS_DIR.iterdir():
        match = MIGRATION_RE.match(path.name)
        if match:
            found.append((int(match.group(1)), path))
    return sorted(found)


def latest_version() -> int:
    """Return the newest schema version available on disk.

    Returns:
        Highest migration number.
    """
    return _migrations()[-1][0]


def _database_file(connection: sqlite3.Connection) -> Path | None:
    """Return the main database file of a connection, if it is file-backed.

    Args:
        connection: Open database connection.

    Returns:
        Database path, or ``None`` for in-memory databases.
    """
    row = connection.execute("PRAGMA database_list").fetchone()
    file_name = row["file"] if row else ""
    return Path(file_name) if file_name else None


def _has_v1_schema(connection: sqlite3.Connection) -> bool:
    """Detect a v1 database created before migrations were tracked.

    Args:
        connection: Open database connection.

    Returns:
        Whether the v1 ``match`` table already exists.
    """
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'match'"
    ).fetchone()
    return row is not None


def backup_database(connection: sqlite3.Connection, destination: str | Path) -> Path:
    """Copy a live database to a file using the SQLite online backup API.

    Args:
        connection: Open source connection.
        destination: Target backup file.

    Returns:
        The written backup path.
    """
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = sqlite3.connect(target)
    try:
        connection.backup(backup)
    finally:
        backup.close()
    return target


def rotate_backups(backup_dir: str | Path, keep_days: int) -> list[Path]:
    """Delete ``jairbet-*.db`` backups older than the retention period.

    Args:
        backup_dir: Folder holding backups.
        keep_days: Number of days to keep.

    Returns:
        Deleted backup paths.
    """
    folder = Path(backup_dir)
    if not folder.exists():
        return []
    cutoff = datetime.now() - timedelta(days=keep_days)
    deleted = []
    for path in folder.glob("jairbet-*.db"):
        if datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
            path.unlink()
            deleted.append(path)
    return deleted


def _seed(connection: sqlite3.Connection) -> None:
    """Insert default bettors, competitions and the v2 data fixes.

    Args:
        connection: Open database connection at the latest schema.
    """
    for name, kind in BETTOR_KINDS.items():
        connection.execute(
            """INSERT INTO bettor (name, active, kind) VALUES (?, 1, ?)
            ON CONFLICT(name) DO UPDATE SET kind = excluded.kind""",
            (name, kind),
        )
    connection.executemany(
        """INSERT OR IGNORE INTO competition (code, name, provider, auto_sync)
        VALUES (?, ?, ?, ?)""",
        SEED_COMPETITIONS,
    )
    connection.execute(
        """UPDATE bet SET source = 'auto'
        WHERE source = 'user' AND bettor_id IN
            (SELECT bettor_id FROM bettor WHERE kind <> 'human')"""
    )
    connection.execute(
        """UPDATE match SET competition_id =
            (SELECT competition_id FROM competition WHERE code = ?)
        WHERE competition_id IS NULL""",
        (MANUAL_CODE,),
    )


def migrate(connection: sqlite3.Connection, backup: bool = True) -> int:
    """Apply pending schema migrations and seed default rows.

    A v1 database without a ``user_version`` is treated as version 1. File
    databases are backed up next to the database file before any change.

    Args:
        connection: Open SQLite connection to migrate.
        backup: Whether to back up a file database before migrating it.

    Returns:
        The schema version after migrating.
    """
    current = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if current == 0 and _has_v1_schema(connection):
        current = 1
        connection.execute("PRAGMA user_version = 1")
    pending = [(version, path) for version, path in _migrations() if version > current]
    db_file = _database_file(connection)
    if pending and backup and db_file is not None and current > 0:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = db_file.with_name(f"{db_file.stem}.pre-v{pending[-1][0]}-{stamp}.db")
        backup_database(connection, target)
        info(f"Backed up database to {target}")
    for version, path in pending:
        try:
            connection.executescript(
                "BEGIN;\n"
                + path.read_text(encoding="utf-8")
                + f"\nPRAGMA user_version = {int(version)};\nCOMMIT;"
            )
        except sqlite3.Error:
            if connection.in_transaction:
                connection.rollback()
            raise
        current = version
        if db_file is not None:
            success(f"Applied migration {path.name}")
    connection.execute("PRAGMA foreign_keys = ON")
    with connection:
        _seed(connection)
    return current


def init_db(connection: sqlite3.Connection) -> None:
    """Create or upgrade the Jairbet schema with its default rows.

    Args:
        connection: Open SQLite connection to initialise.
    """
    migrate(connection)
