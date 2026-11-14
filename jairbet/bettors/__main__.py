"""Command line: ``python -m jairbet.bettors --backfill [--recompute] [--db PATH]``."""

import argparse
import sqlite3
from pathlib import Path

from jairbet.bettors import backfill
from jairbet.config import load_settings
from jairbet.console import error, info, success
from jairbet.db import connect, migrate


def run_backfill(db_path: str | Path, recompute: bool = False) -> int:
    """Migrate a database and create missing animal bets.

    Args:
        db_path: SQLite database path.
        recompute: Also recompute existing automatic Caranguejo/Corvo bets.

    Returns:
        Number of matches processed.
    """
    connection = connect(db_path)
    try:
        migrate(connection)
        with connection:
            return backfill(connection, recompute)
    finally:
        connection.close()


def main() -> None:
    """Run the animal-bettor maintenance command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--backfill", action="store_true", required=True)
    parser.add_argument("--recompute", action="store_true")
    args = parser.parse_args()
    db_path = args.db or load_settings().db_path
    info(f"Backfilling animal bets in {db_path}")
    try:
        count = run_backfill(db_path, args.recompute)
    except (OSError, ValueError, sqlite3.Error) as exc:
        error(f"Backfill failed: {exc}")
        raise SystemExit(1) from exc
    success(f"Animal bets ready for {count} matches.")


if __name__ == "__main__":
    main()
