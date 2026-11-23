from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

from fit_to_md.application.synchronization import PrepareSynchronization
from fit_to_md.domain.synchronization.model import AccountIdentity, Reference
from fit_to_md.domain.synchronization.periods import (
    DiscoveryExecution,
    SelectionError,
    SelectionOptions,
)
from fit_to_md.domain.synchronization.planning import (
    CatalogDiscovery,
    plan_synchronization,
)


class FakeClock:
    def __init__(self, instant: datetime) -> None:
        self.instant = instant
        self.calls = 0

    def now(self) -> datetime:
        self.calls += 1
        return self.instant


class FakeLocalTimezone:
    def __init__(self, zone: ZoneInfo | None) -> None:
        self.zone = zone
        self.calls = 0

    def resolve(self) -> ZoneInfo:
        self.calls += 1
        if self.zone is None:
            raise ZoneInfoNotFoundError("Local timezone is unavailable.")
        return self.zone


def test_snapshots_launch_and_zone_once_for_each_execution() -> None:
    launch = datetime(2026, 11, 16, 14, 30, tzinfo=UTC)
    paris = ZoneInfo("Europe/Paris")
    clock = FakeClock(launch)
    timezone = FakeLocalTimezone(paris)
    prepare = PrepareSynchronization(clock, timezone)

    execution = prepare.prepare(SelectionOptions(last_days=1), None)
    assert isinstance(execution, DiscoveryExecution)
    assert clock.calls == timezone.calls == 1
    clock.instant += timedelta(days=1)
    timezone.zone = ZoneInfo("America/New_York")

    first = plan_synchronization(
        execution,
        AccountIdentity("coros", "region", "account"),
        CatalogDiscovery((), True),
        (),
    )
    assert execution.period.zone is paris
    assert execution.period.end == launch
    assert first.reference_to_commit == Reference(launch)
    assert clock.calls == timezone.calls == 1

    next_execution = prepare.prepare(SelectionOptions(), first.reference_to_commit)
    assert isinstance(next_execution, DiscoveryExecution)
    assert next_execution.period.zone is timezone.zone
    assert next_execution.period.end == clock.instant
    assert clock.calls == timezone.calls == 2


def test_reports_unavailable_timezone() -> None:
    clock = FakeClock(datetime(2026, 11, 16, 14, 30, tzinfo=UTC))
    timezone = FakeLocalTimezone(None)

    with pytest.raises(ZoneInfoNotFoundError, match="Local timezone is unavailable"):
        PrepareSynchronization(clock, timezone).prepare(
            SelectionOptions(last_days=1), None
        )


def test_reports_invalid_period() -> None:
    clock = FakeClock(datetime(2026, 11, 16, 14, 30, tzinfo=UTC))
    timezone = FakeLocalTimezone(ZoneInfo("UTC"))

    with pytest.raises(SelectionError, match="positive integer"):
        PrepareSynchronization(clock, timezone).prepare(
            SelectionOptions(last_days=0), None
        )
