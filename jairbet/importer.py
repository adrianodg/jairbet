"""Import a Jairbet Excel workbook into an idempotent SQLite database."""

import argparse
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import openpyxl
import pandas as pd

from jairbet.bettors import place_auto_bets
from jairbet.config import (
    AUTO_BETTORS,
    DATE_FIXES,
    DEFAULT_DB_PATH,
    DEFAULT_XLSX_PATH,
    MANUAL_CODE,
    TEAM_ALIASES,
)
from jairbet.console import error, info, success, warning
from jairbet.db import connect, init_db
from jairbet.repository import (
    add_bettor,
    add_team,
    get_competition,
    get_ranking,
    seed_team_aliases,
    update_scoring_config,
    upsert_bet,
    upsert_match,
)
from jairbet.scoring import parse_score_text

NO_SCORE = {"", "—", "-", "nan", "none", "nat"}


@dataclass(frozen=True)
class ImportSummary:
    """Counts recorded while importing a workbook.

    Attributes:
        rows_read: Source rows containing data.
        matches: Distinct match fixtures upserted.
        bets: Valid bets upserted.
        skipped_rows: Rows without a prediction or enough fixture data.
        fixes_applied: Known date corrections applied.
    """

    rows_read: int
    matches: int
    bets: int
    skipped_rows: int
    fixes_applied: int


def parse_score(value: object) -> tuple[int, int] | None:
    """Parse an Excel score cell, accepting flexible ``H x A`` whitespace.

    Args:
        value: Spreadsheet cell value.

    Returns:
        Pair of goals, or ``None`` for empty score cells.

    Raises:
        ValueError: If a non-empty score has an invalid format.
    """
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if text.lower() in NO_SCORE:
        return None
    try:
        return parse_score_text(text)
    except ValueError as exc:
        raise ValueError(f"Invalid score {value!r}; expected HxA") from exc


def _date_text(value: object) -> str:
    """Convert an Excel date value to the required ISO date format.

    Args:
        value: Excel cell value.

    Returns:
        ISO-8601 date string.

    Raises:
        ValueError: If the date is missing or cannot be interpreted.
    """
    parsed: date
    if isinstance(value, datetime):
        parsed = value.date()
    elif isinstance(value, date):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.strip()).date()
    else:
        raise ValueError(f"Invalid or missing match date {value!r}")
    return parsed.isoformat()


def _normalise_team(value: object) -> str:
    """Clean one required team-name cell.

    Args:
        value: Source team-name value.

    Returns:
        Stripped team name.

    Raises:
        ValueError: If the value is empty.
    """
    team = "" if value is None else str(value).strip()
    if not team or team.lower() == "nan":
        raise ValueError("Missing team name")
    return TEAM_ALIASES.get(team, team)


def _header_map(sheet: Any) -> dict[str, int]:
    """Map expected first-row headers to 0-based source column indexes.

    Args:
        sheet: OpenPyXL worksheet.

    Returns:
        Header-to-index mapping.

    Raises:
        ValueError: If required headers are absent.
    """
    headers = {
        str(cell.value).strip(): index
        for index, cell in enumerate(next(sheet.iter_rows(min_row=1, max_row=1)))
        if cell.value is not None
    }
    required = {
        "Data",
        "Apostador",
        "Mandante (M)",
        "Visitante (V)",
        "Palpite (MxV)",
        "Placar Real (MxV)",
    }
    missing = required - headers.keys()
    if missing:
        message = ", ".join(sorted(missing))
        raise ValueError(f"Apostas sheet is missing headers: {message}")
    return headers


def import_workbook(xlsx_path: str | Path, db_path: str | Path) -> ImportSummary:
    """Import an Excel workbook in one transaction.

    Existing fixtures and predictions are upserted, so rerunning the same import
    produces the same final database state.

    Args:
        xlsx_path: Path to source workbook.
        db_path: SQLite destination path.

    Returns:
        Counts describing the import.
    """
    workbook = openpyxl.load_workbook(xlsx_path, data_only=True, read_only=True)
    if "Apostas" not in workbook.sheetnames or "Ranking" not in workbook.sheetnames:
        raise ValueError("Workbook must contain Apostas and Ranking sheets")
    bets_sheet = workbook["Apostas"]
    ranking_sheet = workbook["Ranking"]
    # Workbooks written by the exporter carry the animals' stored predictions.
    preserves_auto_bets = (
        "_Jairbet" in workbook.sheetnames
        and workbook["_Jairbet"]["A1"].value == "random_macaco_predictions"
    )
    auto_names = {name.casefold() for name in AUTO_BETTORS}
    headers = _header_map(bets_sheet)
    source_rows = list(bets_sheet.iter_rows(min_row=2, values_only=True))
    rows_read = sum(1 for row in source_rows if any(value is not None for value in row))

    points_winner = ranking_sheet["B2"].value
    bonus_exact = ranking_sheet["B3"].value
    if not isinstance(points_winner, int) or not isinstance(bonus_exact, int):
        raise ValueError(
            "Ranking!B2 and Ranking!B3 must contain integer scoring values"
        )

    connection = connect(db_path)
    try:
        init_db(connection)
        skipped = 0
        fixes = 0
        fixture_ids: set[tuple[str, int, int]] = set()
        bet_count = 0
        with connection:
            update_scoring_config(connection, points_winner, bonus_exact)
            manual = get_competition(connection, MANUAL_CODE)
            manual_id = int(manual["competition_id"]) if manual else None
            tipped_matches: list[int] = []

            grouped: dict[tuple[str, str, str], list[tuple[object, ...]]] = defaultdict(
                list
            )
            for row_number, row in enumerate(source_rows, start=2):
                if not any(value is not None for value in row):
                    continue
                try:
                    raw_date = _date_text(row[headers["Data"]])
                    match_date = DATE_FIXES.get(raw_date, raw_date)
                    fixes += int(match_date != raw_date)
                    home = _normalise_team(row[headers["Mandante (M)"]])
                    away = _normalise_team(row[headers["Visitante (V)"]])
                    grouped[(match_date, home, away)].append(row)
                except ValueError as exc:
                    skipped += 1
                    warning(f"Skipping row {row_number}: {exc}")

            for (match_date, home, away), rows in grouped.items():
                home_id = add_team(connection, home)
                away_id = add_team(connection, away)
                result: tuple[int, int] | None = None
                for row in rows:
                    raw_result = row[headers["Placar Real (MxV)"]]
                    parsed_result = parse_score(raw_result)
                    if parsed_result is not None:
                        if result is not None and result != parsed_result:
                            raise ValueError(
                                f"Conflicting result for {match_date} {home} x {away}"
                            )
                        result = parsed_result
                match_id = upsert_match(
                    connection,
                    match_date,
                    home_id,
                    away_id,
                    result[0] if result else None,
                    result[1] if result else None,
                    manual_id,
                )
                fixture_ids.add((match_date, home_id, away_id))
                has_manual_prediction = False
                for row in rows:
                    bettor = str(row[headers["Apostador"]] or "").strip()
                    prediction = parse_score(row[headers["Palpite (MxV)"]])
                    if not bettor or prediction is None:
                        skipped += 1
                        warning(
                            "Skipping empty prediction for "
                            f"{match_date} {home} x {away}"
                        )
                        continue
                    is_auto = bettor.casefold() in auto_names
                    if is_auto and not preserves_auto_bets:
                        continue
                    bettor_id = add_bettor(connection, bettor)
                    upsert_bet(
                        connection,
                        match_id,
                        bettor_id,
                        *prediction,
                        source="auto" if is_auto else "user",
                    )
                    bet_count += int(not is_auto)
                    has_manual_prediction = has_manual_prediction or not is_auto
                if has_manual_prediction:
                    tipped_matches.append(match_id)

            seed_team_aliases(connection)

            # Second pass, after every result is stored, so Corvo sees the
            # full history regardless of the workbook's row order.
            for match_id in tipped_matches:
                placed = place_auto_bets(
                    connection, match_id, overwrite=not preserves_auto_bets
                )
                bet_count += len(placed)
        return ImportSummary(rows_read, len(fixture_ids), bet_count, skipped, fixes)
    except Exception:
        connection.rollback()
        raise
    finally:
        workbook.close()
        connection.close()


def _print_summary(summary: ImportSummary, db_path: str | Path) -> None:
    """Print an import summary and the resulting ranking.

    Args:
        summary: Import counts.
        db_path: Imported database path.
    """
    success(
        "Imported "
        f"{summary.rows_read} rows, {summary.matches} matches, {summary.bets} bets; "
        f"skipped {summary.skipped_rows}, applied {summary.fixes_applied} fixes."
    )
    with connect(db_path) as connection:
        rows = get_ranking(connection)
    info("Final ranking:")
    for row in rows:
        print(
            f"{row['rank']}. {row['bettor']}: {row['points']} pts "
            f"({row['matches']} matches, {row['winner_hits']} winners, "
            f"{row['exact_hits']} exact)"
        )


def main() -> None:
    """Run the Excel-to-SQLite command-line importer."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xlsx", type=Path, default=DEFAULT_XLSX_PATH)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    try:
        summary = import_workbook(args.xlsx, args.db)
        _print_summary(summary, args.db)
    except (OSError, ValueError, sqlite3.Error) as exc:
        error(f"Import failed: {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
