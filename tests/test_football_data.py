"""Tests for the football-data.org client, without network access."""

import json
from pathlib import Path

import httpx
import pytest

from jairbet.providers.base import ProviderError
from jairbet.providers.football_data import FootballDataProvider, parse_match

DATA = Path(__file__).parent / "data" / "football_data_bsa.json"


def _provider(
    handler: httpx.MockTransport, sleeps: list[float]
) -> FootballDataProvider:
    """Build a provider wired to a mock transport.

    Args:
        handler: Mock transport answering requests.
        sleeps: List collecting requested sleep durations.

    Returns:
        Provider under test.
    """
    return FootballDataProvider(
        "test-token",
        client=httpx.Client(transport=handler),
        sleep=sleeps.append,
    )


def test_parses_recorded_competition_matches() -> None:
    """Statuses, kickoff, goals and team names are normalised."""
    payload = json.loads(DATA.read_text(encoding="utf-8"))
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=payload)

    matches = _provider(httpx.MockTransport(handler), []).fetch_matches("BSA")
    assert seen[0].headers["X-Auth-Token"] == "test-token"
    assert seen[0].url.path == "/v4/competitions/BSA/matches"
    assert len(matches) == 4  # the undecided fixture is skipped
    first = matches[0]
    assert first.external_id == "600101"
    assert first.kickoff_at == "2026-11-13 00:30:00"
    assert (first.status, first.home_goals, first.away_goals) == ("FINISHED", 2, 1)
    assert (first.home_short, first.away_name) == ("Flamengo", "SE Palmeiras")
    assert first.round_number == 33
    live = matches[2]
    assert (live.status, live.home_goals, live.away_goals) == ("LIVE", None, None)
    assert matches[3].status == "SCHEDULED"


def test_date_range_is_sent_as_query_parameters() -> None:
    """Date filters use the API's dateFrom/dateTo parameters."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"matches": []})

    from datetime import date

    _provider(httpx.MockTransport(handler), []).fetch_matches(
        "BSA", date(2026, 11, 1), date(2026, 11, 2)
    )
    assert seen[0].url.params["dateFrom"] == "2026-11-01"
    assert seen[0].url.params["dateTo"] == "2026-11-02"


def test_rate_limit_is_retried_with_reset_header() -> None:
    """HTTP 429 waits for the counter reset, then succeeds."""
    responses = [
        httpx.Response(429, headers={"X-RequestCounter-Reset": "7"}),
        httpx.Response(200, json={"matches": []}),
    ]
    sleeps: list[float] = []
    provider = _provider(httpx.MockTransport(lambda _: responses.pop(0)), sleeps)
    assert provider.fetch_matches("BSA") == []
    assert sleeps == [8.0]


def test_client_error_is_not_retried() -> None:
    """A forbidden competition fails at once with the API message."""
    sleeps: list[float] = []
    provider = _provider(
        httpx.MockTransport(
            lambda _: httpx.Response(403, json={"message": "restricted resource"})
        ),
        sleeps,
    )
    with pytest.raises(ProviderError, match="restricted resource"):
        provider.fetch_matches("CDB")
    assert sleeps == []


def test_exhausted_retries_raise() -> None:
    """Persistent server errors end in a ProviderError."""
    sleeps: list[float] = []
    provider = _provider(httpx.MockTransport(lambda _: httpx.Response(503)), sleeps)
    with pytest.raises(ProviderError):
        provider.fetch_matches("BSA")
    assert len(sleeps) == 3


def test_penalty_shootout_goals_are_excluded() -> None:
    """Cup ties decided on penalties keep the score after extra time."""
    match = parse_match(
        {
            "id": 1,
            "utcDate": "2026-05-01T00:00:00Z",
            "status": "FINISHED",
            "competition": {"code": "CLI", "name": "Copa Libertadores"},
            "homeTeam": {"name": "A"},
            "awayTeam": {"name": "B"},
            "score": {
                "duration": "PENALTY_SHOOTOUT",
                "fullTime": {"home": 5, "away": 4},
                "regularTime": {"home": 1, "away": 1},
                "extraTime": {"home": 0, "away": 0},
                "penalties": {"home": 4, "away": 3},
            },
        }
    )
    assert (match.home_goals, match.away_goals) == (1, 1)


def test_missing_token_is_rejected() -> None:
    """The provider refuses to start without an API key."""
    with pytest.raises(ProviderError):
        FootballDataProvider("")


def test_used_up_quota_pauses_before_the_next_request() -> None:
    """A response with no requests left delays the next call until the reset."""
    headers = {"X-Requests-Available-Minute": "0", "X-RequestCounter-Reset": "20"}
    responses = [
        httpx.Response(200, json={"matches": []}, headers=headers),
        httpx.Response(200, json={"matches": []}),
    ]
    sleeps: list[float] = []
    provider = _provider(httpx.MockTransport(lambda _: responses.pop(0)), sleeps)
    provider.fetch_matches("BSA")
    assert sleeps == []
    provider.fetch_matches("BSA")
    assert len(sleeps) == 1 and 19 < sleeps[0] <= 21


def test_remaining_quota_does_not_pause() -> None:
    """Requests left in the window mean no waiting."""
    headers = {"X-Requests-Available-Minute": "7", "X-RequestCounter-Reset": "40"}
    sleeps: list[float] = []
    provider = _provider(
        httpx.MockTransport(
            lambda _: httpx.Response(200, json={"matches": []}, headers=headers)
        ),
        sleeps,
    )
    provider.fetch_matches("BSA")
    provider.fetch_matches("BSA")
    assert sleeps == []
