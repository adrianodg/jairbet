"""Provider-neutral match data and the provider protocol."""

from dataclasses import dataclass
from datetime import date
from typing import Protocol

STATUSES = ("SCHEDULED", "LIVE", "FINISHED", "POSTPONED", "CANCELLED")


class ProviderError(RuntimeError):
    """Raised when a provider request fails after retries."""


@dataclass(frozen=True)
class ApiMatch:
    """One match as reported by a provider.

    Attributes:
        external_id: Provider match ID.
        competition_code: Provider competition code, e.g. ``BSA``.
        competition_name: Competition display name.
        kickoff_at: UTC kickoff as ``YYYY-MM-DD HH:MM:SS``.
        status: One of ``STATUSES``.
        round_number: Matchday, if known.
        home_name: Home team full name.
        away_name: Away team full name.
        home_short: Home team short name, if any.
        away_short: Away team short name, if any.
        home_goals: Final home goals (only when finished).
        away_goals: Final away goals (only when finished).
    """

    external_id: str
    competition_code: str
    competition_name: str
    kickoff_at: str
    status: str
    round_number: int | None
    home_name: str
    away_name: str
    home_short: str | None
    away_short: str | None
    home_goals: int | None
    away_goals: int | None


class FixtureProvider(Protocol):
    """Source of fixtures and results."""

    def fetch_matches(
        self,
        competition_code: str,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[ApiMatch]:
        """Return a competition's matches, optionally limited to a date range.

        Args:
            competition_code: Provider competition code.
            date_from: First UTC date (inclusive), or the whole current season.
            date_to: Last UTC date (inclusive).

        Returns:
            Matches reported by the provider.
        """
        ...

    def fetch_match(self, external_id: str) -> ApiMatch:
        """Return one match by provider ID.

        Args:
            external_id: Provider match ID.

        Returns:
            The match.
        """
        ...
