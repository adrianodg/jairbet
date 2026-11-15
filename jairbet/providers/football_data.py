"""football-data.org v4 client (free tier: 10 requests per minute)."""

import time
from collections.abc import Callable, Mapping
from datetime import date
from typing import Any

import httpx

from jairbet.config import (
    FOOTBALL_DATA_BASE_URL,
    HTTP_MAX_RETRIES,
    HTTP_TIMEOUT_SECONDS,
)
from jairbet.console import warning
from jairbet.providers.base import ApiMatch, ProviderError
from jairbet.timeutil import parse_iso_utc, to_db

STATUS_MAP = {
    "SCHEDULED": "SCHEDULED",
    "TIMED": "SCHEDULED",
    "IN_PLAY": "LIVE",
    "PAUSED": "LIVE",
    "LIVE": "LIVE",
    "EXTRA_TIME": "LIVE",
    "PENALTY_SHOOTOUT": "LIVE",
    "FINISHED": "FINISHED",
    "AWARDED": "FINISHED",
    "POSTPONED": "POSTPONED",
    "SUSPENDED": "POSTPONED",
    "CANCELLED": "CANCELLED",
}
MAX_RATE_LIMIT_WAIT = 65.0


def _goals(score: Mapping[str, Any]) -> tuple[int | None, int | None]:
    """Extract the bet-relevant score, excluding penalty shoot-outs.

    Args:
        score: The ``score`` object of a football-data match.

    Returns:
        Home and away goals, or ``None`` values when missing.
    """
    full = score.get("fullTime") or {}
    if score.get("duration", "REGULAR") == "REGULAR" or not score.get("regularTime"):
        home, away = full.get("home"), full.get("away")
    else:
        regular = score.get("regularTime") or {}
        extra = score.get("extraTime") or {}
        home = (regular.get("home") or 0) + (extra.get("home") or 0)
        away = (regular.get("away") or 0) + (extra.get("away") or 0)
    if home is None or away is None:
        return None, None
    return int(home), int(away)


def parse_match(
    payload: Mapping[str, Any], competition: Mapping[str, Any] | None = None
) -> ApiMatch:
    """Convert one football-data match object to an ``ApiMatch``.

    Args:
        payload: Match JSON object.
        competition: Competition JSON object when not embedded in the match.

    Returns:
        Normalised match. Goals are only set for finished matches.
    """
    comp = payload.get("competition") or competition or {}
    status = STATUS_MAP.get(str(payload.get("status")), "SCHEDULED")
    home_goals, away_goals = _goals(payload.get("score") or {})
    if status != "FINISHED":
        home_goals = away_goals = None
    home = payload.get("homeTeam") or {}
    away = payload.get("awayTeam") or {}
    matchday = payload.get("matchday")
    return ApiMatch(
        external_id=str(payload["id"]),
        competition_code=str(comp.get("code") or ""),
        competition_name=str(comp.get("name") or comp.get("code") or ""),
        kickoff_at=to_db(parse_iso_utc(str(payload["utcDate"]))),
        status=status,
        round_number=int(matchday) if matchday is not None else None,
        home_name=str(home.get("name") or home.get("shortName") or "?"),
        away_name=str(away.get("name") or away.get("shortName") or "?"),
        home_short=home.get("shortName"),
        away_short=away.get("shortName"),
        home_goals=home_goals,
        away_goals=away_goals,
    )


def parse_matches(payload: Mapping[str, Any]) -> list[ApiMatch]:
    """Convert a ``/matches`` response body to ``ApiMatch`` objects.

    Args:
        payload: Response JSON.

    Returns:
        Matches with a team on both sides (undecided knockout slots skipped).
    """
    competition = payload.get("competition")
    return [
        parse_match(item, competition)
        for item in payload.get("matches", [])
        if (item.get("homeTeam") or {}).get("name")
        and (item.get("awayTeam") or {}).get("name")
    ]


class FootballDataProvider:
    """Synchronous football-data.org client with retries and rate limiting."""

    def __init__(
        self,
        token: str,
        base_url: str = FOOTBALL_DATA_BASE_URL,
        client: httpx.Client | None = None,
        max_retries: int = HTTP_MAX_RETRIES,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Create the client.

        Args:
            token: API key sent as ``X-Auth-Token``.
            base_url: API root URL.
            client: Optional preconfigured HTTP client (tests use a mock).
            max_retries: Retries for rate limits, timeouts and server errors.
            sleep: Sleep function, replaceable in tests.

        Raises:
            ProviderError: If no token is given.
        """
        if not token:
            raise ProviderError("FOOTBALL_DATA_TOKEN is not set")
        self._client = client or httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)
        self._base_url = base_url.rstrip("/")
        self._headers = {"X-Auth-Token": token}
        self._max_retries = max_retries
        self._sleep = sleep
        self._throttle_until = 0.0
        self._clock = time.monotonic

    def _remember_quota(self, response: httpx.Response) -> None:
        """Read the throttling headers and plan a pause when the quota is used up.

        football-data.org sends ``X-Requests-Available-Minute`` (requests left in
        the current window) and ``X-RequestCounter-Reset`` (seconds until the
        window resets) on every response.

        Args:
            response: Any API response.
        """
        available = response.headers.get("X-Requests-Available-Minute", "")
        reset = response.headers.get("X-RequestCounter-Reset", "")
        if available.isdigit() and int(available) <= 0 and reset.isdigit():
            wait = min(float(reset) + 1, MAX_RATE_LIMIT_WAIT)
            self._throttle_until = self._clock() + wait

    def _wait_for_quota(self) -> None:
        """Sleep until the rate-limit window resets, if the quota is used up."""
        wait = self._throttle_until - self._clock()
        if wait > 0:
            warning(f"football-data quota used up; waiting {wait:.0f}s")
            self._sleep(wait)
        self._throttle_until = 0.0

    def _get(self, path: str, params: Mapping[str, str] | None = None) -> Any:
        """Send a GET request, retrying rate limits and transient failures.

        Args:
            path: Path below the API root.
            params: Query parameters.

        Returns:
            Decoded JSON body.

        Raises:
            ProviderError: On client errors or when retries are exhausted.
        """
        url = f"{self._base_url}{path}"
        last_error = "unknown error"
        for attempt in range(self._max_retries + 1):
            self._wait_for_quota()
            try:
                response = self._client.get(
                    url, params=dict(params or {}), headers=self._headers
                )
            except httpx.TransportError as exc:
                last_error = f"network error: {exc}"
                wait = 2.0**attempt
            else:
                self._remember_quota(response)
                if response.status_code == 200:
                    return response.json()
                if response.status_code == 429:
                    reset = response.headers.get("X-RequestCounter-Reset", "")
                    wait = float(reset) + 1 if reset.isdigit() else 2.0**attempt * 5
                    last_error = "rate limited (HTTP 429)"
                elif response.status_code >= 500:
                    wait = 2.0**attempt
                    last_error = f"server error HTTP {response.status_code}"
                else:
                    try:
                        message = response.json().get("message", response.text)
                    except ValueError:
                        message = response.text
                    raise ProviderError(
                        f"football-data HTTP {response.status_code}: {message}"
                    )
            if attempt < self._max_retries:
                wait = min(wait, MAX_RATE_LIMIT_WAIT)
                warning(f"football-data {last_error}; retrying in {wait:.0f}s")
                self._sleep(wait)
        raise ProviderError(f"football-data request failed: {last_error}")

    def fetch_matches(
        self,
        competition_code: str,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[ApiMatch]:
        """Return a competition's matches (current season, or a date range).

        Args:
            competition_code: Competition code such as ``BSA``.
            date_from: First UTC date (inclusive).
            date_to: Last UTC date (inclusive).

        Returns:
            Matches.
        """
        params: dict[str, str] = {}
        if date_from is not None:
            params["dateFrom"] = date_from.isoformat()
            params["dateTo"] = (date_to or date_from).isoformat()
        payload = self._get(f"/competitions/{competition_code}/matches", params)
        return parse_matches(payload)

    def fetch_match(self, external_id: str) -> ApiMatch:
        """Return one match by ID.

        Args:
            external_id: football-data match ID.

        Returns:
            The match.
        """
        return parse_match(self._get(f"/matches/{external_id}"))

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()
