"""Importer integration tests for Excel quirks and the bundled workbook."""

from pathlib import Path

import openpyxl
import pytest

from jairbet.db import connect
from jairbet.exporter import export_workbook
from jairbet.importer import import_workbook
from jairbet.repository import get_ranking


def _fixture_workbook(path: Path) -> None:
    """Create a compact workbook covering source data edge cases.

    Args:
        path: Destination workbook path.
    """
    workbook = openpyxl.Workbook()
    bets = workbook.active
    bets.title = "Apostas"
    bets.append(
        [
            "Data",
            "Apostador",
            "Mandante (M)",
            "Visitante (V)",
            "Palpite (MxV)",
            "Placar Real (MxV)",
        ]
    )
    bets.append(["2023-08-18", "Jair", "Vitoria", "Bolivar", "2 x 1", "2X1"])
    bets.append(["2023-08-18", "Macaco", "Vitoria", "Bolivar", "0x0", "2X1"])
    bets.append(["2026-08-19", "Jair", "Santos", "Bahia", "", "—"])
    ranking = workbook.create_sheet("Ranking")
    ranking["B2"] = 3
    ranking["B3"] = 3
    workbook.save(path)


def test_import_repairs_source_quirks(tmp_path: Path) -> None:
    """Import whitespace scores, the date typo, aliases, and an empty prediction."""
    source = tmp_path / "fixture.xlsx"
    database = tmp_path / "fixture.db"
    _fixture_workbook(source)
    summary = import_workbook(source, database)
    assert summary.rows_read == 3
    assert summary.matches == 2
    assert summary.bets == 4
    assert summary.skipped_rows == 1
    assert summary.fixes_applied == 2
    with connect(database) as connection:
        teams = connection.execute("SELECT name FROM team ORDER BY name").fetchall()
        ranking = get_ranking(connection)
        macaco_bet = connection.execute(
            """SELECT pred_home, pred_away FROM bet
            JOIN bettor USING (bettor_id) WHERE bettor.name = 'Macaco'"""
        ).fetchone()
    assert [row["name"] for row in teams] == ["Bahia", "Bolivar", "Santos", "Vitória"]
    assert ranking[0]["bettor"] == "Jair"
    assert ranking[0]["points"] == 6
    assert macaco_bet is not None
    assert 0 <= macaco_bet["pred_home"] <= 3
    assert 0 <= macaco_bet["pred_away"] <= 3


def test_export_import_round_trip_preserves_ranking(tmp_path: Path) -> None:
    """Exporting and re-importing must retain every ranking value."""
    source = tmp_path / "source.xlsx"
    first_db = tmp_path / "first.db"
    second_db = tmp_path / "second.db"
    exported = tmp_path / "exported.xlsx"
    _fixture_workbook(source)
    import_workbook(source, first_db)
    export_workbook(first_db, exported)
    import_workbook(exported, second_db)
    with connect(first_db) as first, connect(second_db) as second:
        assert [tuple(row) for row in get_ranking(first)] == [
            tuple(row) for row in get_ranking(second)
        ]


def test_real_workbook_parity_when_present(tmp_path: Path) -> None:
    """Check documented parity for the optional real 2026 source workbook."""
    source = Path(__file__).parent.parent / "Jairbet_2026_v1.xlsx"
    if not source.exists():
        pytest.skip("Real workbook is not present")
    database = tmp_path / "real.db"
    import_workbook(source, database)
    with connect(database) as connection:
        actual = {row["bettor"]: row for row in get_ranking(connection)}
    jair = actual["Jair"]
    macaco = actual["Macaco"]
    jair_summary = (
        jair["matches"],
        jair["winner_hits"],
        jair["exact_hits"],
        jair["points"],
    )
    assert jair_summary == (
        180,
        81,
        18,
        297,
    )
    assert macaco["matches"] == jair["matches"]
