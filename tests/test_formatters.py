"""Tests for message formatting and score parsing."""

import pytest

from jairbet.bettors.corvo import predict
from jairbet.bot import formatters
from jairbet.scoring import parse_score_text


@pytest.mark.parametrize(
    ("text", "expected"),
    [("2x1", (2, 1)), (" 2 x 1 ", (2, 1)), ("2X1", (2, 1)), ("0-0", (0, 0))],
)
def test_parse_score_text(text: str, expected: tuple[int, int]) -> None:
    """Typed scores accept spaces, upper case and a dash."""
    assert parse_score_text(text) == expected


@pytest.mark.parametrize("text", ["", "2", "2x", "ax1", "2x1x0", "100x1"])
def test_parse_score_text_rejects_garbage(text: str) -> None:
    """Invalid scores raise ValueError."""
    with pytest.raises(ValueError):
        parse_score_text(text)


def _match(**changes: object) -> dict[str, object]:
    """Build a match row.

    Args:
        **changes: Fields to override.

    Returns:
        Match mapping.
    """
    row: dict[str, object] = {
        "match_id": 7,
        "match_date": "2026-11-15",
        "kickoff_at": "2026-11-15 19:00:00",
        "home_team": "Flamengo",
        "away_team": "São Paulo & Cia",
        "home_goals": None,
        "away_goals": None,
        "status": "SCHEDULED",
        "competition_name": "Brasileirão Série A",
        "admin_locked": 0,
        "pred_home": None,
        "pred_away": None,
    }
    row.update(changes)
    return row


def test_match_line_escapes_html_and_shows_local_time() -> None:
    """Team names are escaped and kickoff is shown in Brasília time."""
    line = formatters.match_line(_match(), "America/Sao_Paulo", show_bet=True)
    assert "São Paulo &amp; Cia" in line
    assert "dom 15/11 16:00" in line
    assert "sem palpite" in line


def test_ranking_message_table() -> None:
    """The ranking is a monospaced table with the leader's gap."""
    rows = [
        {
            "rank": 1,
            "bettor": "Jair",
            "matches": 10,
            "winner_hits": 6,
            "exact_hits": 2,
            "points": 24,
        },
        {
            "rank": 2,
            "bettor": "Corvo",
            "matches": 10,
            "winner_hits": 5,
            "exact_hits": 1,
            "points": 18,
        },
    ]
    text = formatters.ranking_message(rows)
    assert "<pre>" in text and "Jair" in text
    assert "Vantagem do líder: <b>6</b>" in text


def test_help_lists_admin_commands_only_for_admins() -> None:
    """Admin commands are hidden from Jair."""
    assert "/novojogo" in formatters.help_message(True, False)
    assert "/novojogo" not in formatters.help_message(False, True)
    assert "/palpitar" in formatters.help_message(False, True)


def test_corvo_message_shows_probabilities() -> None:
    """Corvo's explanation includes the pick and the outcome chances."""
    text = formatters.corvo_message(_match(), predict([], 1, 2), "America/Sao_Paulo")
    assert "Palpite: <b>1x0</b>" in text
    assert "%" in text


def test_truncate_keeps_messages_short() -> None:
    """Long messages are cut under Telegram's limit."""
    text = formatters.truncate("linha\n" * 2000)
    assert len(text) <= formatters.MAX_MESSAGE + 2
