"""Rules for the account-wide FIT budget."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

FIT_LIMIT = 50
FIT_WINDOW = timedelta(hours=24)


@dataclass(frozen=True)
class DailyCalls:
    catalogue: int = 0
    fit: int = 0


class FitQuotaExceeded(Exception):
    """The local FIT budget is exhausted."""

    def __init__(self, retry_at: datetime) -> None:
        super().__init__("The local COROS FIT quota is exhausted.")
        self.retry_at = retry_at


class ServerFitQuotaExceeded(Exception):
    """COROS has refused further FIT requests for this execution."""

    def __init__(self, retry_at: datetime | None) -> None:
        super().__init__("COROS refused the FIT request (HTTP 429).")
        self.retry_at = retry_at


def require_fit_capacity(active_reservations: Sequence[datetime]) -> None:
    """Check timestamps in chronological order, including any after a clock rollback."""
    if len(active_reservations) >= FIT_LIMIT:
        first_available = active_reservations[len(active_reservations) - FIT_LIMIT]
        raise FitQuotaExceeded(first_available + FIT_WINDOW)
