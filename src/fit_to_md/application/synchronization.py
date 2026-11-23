from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from zoneinfo import ZoneInfo

from fit_to_md.domain.coros_connection import CorosAccount
from fit_to_md.domain.synchronization.model import AccountIdentity, Reference
from fit_to_md.domain.synchronization.periods import (
    DiscoveryExecution,
    ReferenceRepositioning,
    SelectionOptions,
    resolve_selection,
)


class Clock(Protocol):
    def now(self) -> datetime:
        """Return a timezone-aware instant."""
        ...


class LocalTimezone(Protocol):
    def resolve(self) -> ZoneInfo: ...


def to_account_identity(account: CorosAccount) -> AccountIdentity:
    """Translate a confirmed COROS account to the synchronization context."""
    return AccountIdentity("coros", account.issuer, account.subject)


class PrepareSynchronization:
    """Capture the temporal context for a synchronization request."""

    def __init__(self, clock: Clock, timezone: LocalTimezone) -> None:
        self._clock = clock
        self._timezone = timezone

    def prepare(
        self, options: SelectionOptions, reference: Reference | None
    ) -> DiscoveryExecution | ReferenceRepositioning:
        return self.capture().resolve(options, reference)

    def capture(self) -> "SynchronizationTime":
        return SynchronizationTime(self._clock.now(), self._timezone.resolve())


@dataclass(frozen=True)
class SynchronizationTime:
    launched_at: datetime
    zone: ZoneInfo

    def resolve(
        self, options: SelectionOptions, reference: Reference | None
    ) -> DiscoveryExecution | ReferenceRepositioning:
        return resolve_selection(
            options,
            reference,
            launched_at=self.launched_at,
            zone=self.zone,
        )
