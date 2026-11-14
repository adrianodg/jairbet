"""Tests for provider sync: idempotency, locking, reconciliation and events."""

import sqlite3
from dataclasses import replace
from datetime import date

import pytest

from jairbet.db import connect, init_db
from jairbet.providers.base import ApiMatch
from jairbet.repository import (
    add_match,
    add_team,
    get_match,
    get_ranking,
    recent_sync_runs,
    set_match_result,
)
from jairbet.sync import normalise_team_name, run_sync, sync_matches, track_match


def _api(external_id: str = "1", **changes: object) -> ApiMatch:
    """Build a provider match for Flamengo x Palmeiras.

    Args:
        external_id: Provider match ID.
        **changes: Fields to override.

    Returns:
        Provider match.
    """
    base = ApiMatch(
        external_id=external_id,
        competition_code="BSA",
        competition_name="Campeonato Brasileiro Série A",
        kickoff_at="2026-11-13 00:30:00",
        status="SCHEDULED",
        round_number=33,
        home_name="CR Flamengo",
        away_name="SE Palmeiras",
        home_short="Flamengo",
        away_short="Palmeiras",
        home_goals=None,
        away_goals=None,
    )
    return replace(base, **changes)


class FakeProvider:
    """In-memory provider returning preset matches."""

    def __init__(self, matches: list[ApiMatch]) -> None:
        """Store the matches to return.

        Args:
            matches: Matches served by the fake.
        """
        self.matches = matches

    def fetch_matches(
        self,
        competition_code: str,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[ApiMatch]:
        """Return every preset match.

        Args:
            competition_code: Ignored.
            date_from: Ignored.
            date_to: Ignored.

        Returns:
            Preset matches.
        """
        return list(self.matches)

    def fetch_match(self, external_id: str) -> ApiMatch:
        """Return one preset match.

        Args:
            external_id: Provider ID.

        Returns:
            The matching preset.
        """
        return next(m for m in self.matches if m.external_id == external_id)


@pytest.fixture()
def database() -> sqlite3.Connection:
    """Provide an initialised in-memory database.

    Yields:
        Open database connection.
    """
    connection = connect()
    init_db(connection)
    yield connection
    connection.close()


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("São Paulo FC", "Sao Paulo"),
        ("CR Flamengo", "Flamengo"),
        ("Grêmio FBPA", "gremio"),
        ("SC Corinthians Paulista", "Corinthians Paulista"),
        ("Fortaleza EC 1918", "Fortaleza"),
    ],
)
def test_team_names_normalise(left: str, right: str) -> None:
    """Accents, case, club suffixes and years are ignored."""
    assert normalise_team_name(left) == normalise_team_name(right)


def test_sync_creates_then_is_idempotent(database: sqlite3.Connection) -> None:
    """A second run with the same data changes nothing."""
    provider = FakeProvider([_api()])
    first = run_sync(database, provider, "BSA")
    assert (first.created, first.updated) == (1, 0)
    match = get_match(database, 1)
    assert match["match_date"] == "2026-11-12"  # 21:30 in Brasília
    assert match["competition_code"] == "BSA"
    assert match["home_team"] == "Flamengo"
    second = run_sync(database, provider, "BSA")
    assert (second.created, second.updated, second.events) == (0, 0, [])
    assert [row["status"] for row in recent_sync_runs(database)] == ["ok", "ok"]


def test_live_scores_are_not_stored(database: sqlite3.Connection) -> None:
    """Only finished matches carry goals, so partial scores never count."""
    sync_matches(database, [_api(status="LIVE", home_goals=None)], "BSA")
    assert get_match(database, 1)["home_goals"] is None


def test_finished_event_and_reschedule(database: sqlite3.Connection) -> None:
    """Kickoff changes and final results emit events once."""
    sync_matches(database, [_api()], "BSA")
    moved = sync_matches(database, [_api(kickoff_at="2026-11-14 22:00:00")], "BSA")
    assert [event.kind for event in moved.events] == ["rescheduled"]
    final = _api(
        kickoff_at="2026-11-14 22:00:00", status="FINISHED", home_goals=2, away_goals=1
    )
    finished = sync_matches(database, [final], "BSA")
    assert [event.kind for event in finished.events] == ["finished"]
    assert sync_matches(database, [final], "BSA").events == []
    match = get_match(database, 1)
    assert (match["home_goals"], match["away_goals"], match["status"]) == (
        2,
        1,
        "FINISHED",
    )


def test_admin_locked_match_is_not_overwritten(database: sqlite3.Connection) -> None:
    """A result set by the admin wins over the provider."""
    sync_matches(database, [_api()], "BSA")
    set_match_result(database, 1, 3, 3, lock=True)
    final = _api(status="FINISHED", home_goals=0, away_goals=1)
    result = sync_matches(database, [final], "BSA")
    assert result.updated == 0
    assert get_match(database, 1)["home_goals"] == 3


def test_legacy_match_is_linked_not_duplicated(database: sqlite3.Connection) -> None:
    """An imported fixture one day off is reused and keeps its bets' ranking."""
    flamengo = add_team(database, "Flamengo")
    palmeiras = add_team(database, "Palmeiras")
    legacy = add_match(
        database, "2026-11-13", flamengo, palmeiras, 2, 1, source="import"
    )
    before = [tuple(row) for row in get_ranking(database)]
    final = _api(status="FINISHED", home_goals=2, away_goals=1)
    result = sync_matches(database, [final], "BSA")
    assert (result.created, result.updated) == (0, 1)
    assert database.execute("SELECT COUNT(*) FROM match").fetchone()[0] == 1
    match = get_match(database, legacy)
    assert (match["external_id"], match["source"]) == ("1", "api")
    assert match["match_date"] == "2026-11-12"
    assert [tuple(row) for row in get_ranking(database)] == before


def test_unknown_team_is_created_and_reported(database: sqlite3.Connection) -> None:
    """New clubs are created with their short name and announced."""
    result = sync_matches(
        database,
        [_api(home_name="Clube do Remo", home_short="Remo")],
        "BSA",
    )
    unknown = [event.detail for event in result.events if event.kind == "unknown_team"]
    assert unknown == ["Clube do Remo", "SE Palmeiras"]
    assert get_match(database, 1)["home_team"] == "Remo"
    again = sync_matches(database, [_api("2", home_name="Clube do Remo")], "BSA")
    assert [e for e in again.events if e.kind == "unknown_team"] == []


def test_config_alias_maps_provider_name(database: sqlite3.Connection) -> None:
    """Configured aliases map API names to the workbook's team names."""
    galo = add_team(database, "Galo")
    sync_matches(
        database,
        [_api(home_name="Clube Atlético Mineiro", home_short="Atlético Mineiro")],
        "BSA",
    )
    assert get_match(database, 1)["home_team_id"] == galo


def test_track_single_match_from_other_competition(
    database: sqlite3.Connection,
) -> None:
    """A one-off match creates a manual-sync competition and is tracked."""
    provider = FakeProvider(
        [_api("77", competition_code="CLI", competition_name="Copa Libertadores")]
    )
    match_id, _ = track_match(database, provider, "77")
    match = get_match(database, match_id)
    assert match["competition_code"] == "CLI"
    assert match["competition_auto_sync"] == 0


def test_failed_sync_is_logged(database: sqlite3.Connection) -> None:
    """Provider failures are recorded and re-raised."""

    class Broken(FakeProvider):
        def fetch_matches(self, *args: object, **kwargs: object) -> list[ApiMatch]:
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        run_sync(database, Broken([]), "BSA")
    run = recent_sync_runs(database)[0]
    assert (run["status"], run["error"]) == ("error", "boom")
