"""Export the Jairbet SQLite database to a compatible Excel workbook."""

from pathlib import Path

import openpyxl
from openpyxl.styles import Font

from jairbet.db import connect
from jairbet.repository import get_bettors, get_scoring_config, query_rows

HEADERS = [
    "Data",
    "Apostador",
    "Mandante (M)",
    "Visitante (V)",
    "Palpite (MxV)",
    "Placar Real (MxV)",
]


def export_workbook(db_path: str | Path, xlsx_path: str | Path) -> None:
    """Export all matches and bets to the standard Jairbet workbook layout.

    Args:
        db_path: Source SQLite database path.
        xlsx_path: Output workbook path.
    """
    workbook = openpyxl.Workbook()
    bets_sheet = workbook.active
    bets_sheet.title = "Apostas"
    bets_sheet.append(HEADERS)
    for cell in bets_sheet[1]:
        cell.font = Font(bold=True)
    ranking_sheet = workbook.create_sheet("Ranking")
    ranking_sheet["A2"] = "Pontos por vencedor"
    ranking_sheet["A3"] = "Bônus por placar exato"
    metadata_sheet = workbook.create_sheet("_Jairbet")
    metadata_sheet.sheet_state = "hidden"
    metadata_sheet["A1"] = "random_macaco_predictions"

    with connect(db_path) as connection:
        config = get_scoring_config(connection)
        ranking_sheet["B2"] = config["points_winner"]
        ranking_sheet["B3"] = config["bonus_exact"]
        bettors = get_bettors(connection)
        rows = query_rows(
            connection,
            """SELECT m.match_id, m.match_date, home.name AS home_team,
            away.name AS away_team, m.home_goals, m.away_goals, b.bettor_id,
            b.pred_home, b.pred_away
            FROM match AS m
            JOIN team AS home ON home.team_id = m.home_team_id
            JOIN team AS away ON away.team_id = m.away_team_id
            LEFT JOIN bet AS b ON b.match_id = m.match_id
            ORDER BY m.match_date, m.match_id, b.bettor_id""",
        )
    by_match: dict[int, list[object]] = {}
    for row in rows:
        match = by_match.setdefault(
            row["match_id"],
            [
                row["match_date"],
                row["home_team"],
                row["away_team"],
                row["home_goals"],
                row["away_goals"],
                {},
            ],
        )
        if row["bettor_id"] is not None:
            match[5][row["bettor_id"]] = (row["pred_home"], row["pred_away"])
    for match in by_match.values():
        match_date, home, away, home_goals, away_goals, predictions = match
        result = "" if home_goals is None else f"{home_goals}x{away_goals}"
        for bettor in bettors:
            prediction = predictions.get(bettor["bettor_id"])
            score = "" if prediction is None else f"{prediction[0]}x{prediction[1]}"
            bets_sheet.append([match_date, bettor["name"], home, away, score, result])
    for column in bets_sheet.columns:
        letter = column[0].column_letter
        bets_sheet.column_dimensions[letter].width = 20
    workbook.save(xlsx_path)
