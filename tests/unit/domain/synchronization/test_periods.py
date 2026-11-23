from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from fit_to_md.domain.synchronization.model import InvalidInstant, Reference
from fit_to_md.domain.synchronization.periods import (
    DiscoveryExecution,
    DiscoveryMode,
    Period,
    ReferenceRepositioning,
    SelectionError,
    SelectionErrorCode,
    SelectionOptions,
    resolve_selection,
)

PARIS = ZoneInfo("Europe/Paris")
LAUNCH = datetime(2026, 11, 16, 14, 30, tzinfo=UTC)


def test_since_includes_start_and_excludes_launch() -> None:
    result = resolve_selection(
        SelectionOptions(since="2026-11-01"), None, launched_at=LAUNCH, zone=PARIS
    )

    assert isinstance(result, DiscoveryExecution)
    assert result.mode is DiscoveryMode.FIRST_EXPLICIT
    assert result.period.start == datetime(2026, 10, 31, 23, tzinfo=UTC)
    assert result.period.end == LAUNCH
    assert result.period.zone is PARIS
    assert result.period.contains(result.period.start)
    assert not result.period.contains(result.period.start - timedelta(microseconds=1))
    assert result.period.contains(LAUNCH - timedelta(microseconds=1))
    assert not result.period.contains(LAUNCH)
    assert not result.period.contains(None)


@pytest.mark.parametrize(
    ("launch", "count", "start"),
    [
        (LAUNCH, 7, datetime(2026, 11, 9, 23, tzinfo=UTC)),
        (LAUNCH, 1, datetime(2026, 11, 15, 23, tzinfo=UTC)),
        (
            datetime(2026, 5, 2, 12, tzinfo=UTC),
            3,
            datetime(2026, 4, 29, 22, tzinfo=UTC),
        ),
        (
            datetime(2026, 1, 1, 12, tzinfo=UTC),
            2,
            datetime(2025, 12, 30, 23, tzinfo=UTC),
        ),
    ],
)
def test_last_days_counts_local_calendar_dates(
    launch: datetime, count: int, start: datetime
) -> None:
    result = resolve_selection(
        SelectionOptions(last_days=count), None, launched_at=launch, zone=PARIS
    )
    assert isinstance(result, DiscoveryExecution)
    assert result.period.start == start


@pytest.mark.parametrize(("month", "day", "hours"), [(3, 30, 23), (10, 26, 25)])
def test_last_days_covers_dst_days(month: int, day: int, hours: int) -> None:
    launch = datetime(2026, month, day, tzinfo=PARIS)
    result = resolve_selection(
        SelectionOptions(last_days=2), None, launched_at=launch, zone=PARIS
    )
    assert isinstance(result, DiscoveryExecution)
    assert result.period.end - result.period.start == timedelta(hours=hours)


def test_today_at_midnight_can_produce_empty_window() -> None:
    start = datetime(2026, 11, 16, tzinfo=PARIS)
    result = resolve_selection(
        SelectionOptions(last_days=1), None, launched_at=start, zone=PARIS
    )
    assert isinstance(result, DiscoveryExecution)
    assert result.period.start == result.period.end
    assert not result.period.contains(start)


def test_incremental_resolves_reference_day_in_the_current_zone() -> None:
    reference = Reference(datetime(2026, 11, 16, 0, 30, tzinfo=PARIS))
    paris = resolve_selection(
        SelectionOptions(), reference, launched_at=LAUNCH, zone=PARIS
    )
    new_york = resolve_selection(
        SelectionOptions(),
        reference,
        launched_at=LAUNCH,
        zone=ZoneInfo("America/New_York"),
    )
    assert isinstance(paris, DiscoveryExecution)
    assert isinstance(new_york, DiscoveryExecution)
    assert paris.mode is new_york.mode is DiscoveryMode.INCREMENTAL
    assert paris.period.start == datetime(2026, 11, 15, 23, tzinfo=UTC)
    assert new_york.period.start == datetime(2026, 11, 15, 5, tzinfo=UTC)
    assert reference.instant == datetime(2026, 11, 15, 23, 30, tzinfo=UTC)
    assert reference.instant.tzinfo is UTC


def test_ambiguous_midnight_takes_first_occurrence() -> None:
    result = resolve_selection(
        SelectionOptions(since="2026-11-01"),
        None,
        launched_at=LAUNCH,
        zone=ZoneInfo("America/Havana"),
    )
    assert isinstance(result, DiscoveryExecution)
    assert result.period.start == datetime(2026, 11, 1, 4, tzinfo=UTC)


@pytest.mark.parametrize(
    ("options", "error"),
    [
        (
            SelectionOptions(since="2026-11-01", last_days=1),
            SelectionErrorCode.CONFLICTING_OPTIONS,
        ),
        (
            SelectionOptions(init_coros_reference="2026-11-01", since="2026-11-01"),
            SelectionErrorCode.CONFLICTING_OPTIONS,
        ),
        (
            SelectionOptions(init_coros_reference="2026-11-01", last_days=1),
            SelectionErrorCode.CONFLICTING_OPTIONS,
        ),
        (
            SelectionOptions(init_coros_reference="2026-11-01", download_only=True),
            SelectionErrorCode.CONFLICTING_OPTIONS,
        ),
        (
            SelectionOptions(init_coros_reference="2026-11-01", dry_run=True),
            SelectionErrorCode.CONFLICTING_OPTIONS,
        ),
        (SelectionOptions(last_days=0), SelectionErrorCode.INVALID_DAY_COUNT),
        (SelectionOptions(last_days=-1), SelectionErrorCode.INVALID_DAY_COUNT),
        (SelectionOptions(last_days=True), SelectionErrorCode.INVALID_DAY_COUNT),
        (SelectionOptions(last_days=1.5), SelectionErrorCode.INVALID_DAY_COUNT),
        (SelectionOptions(last_days=10**12), SelectionErrorCode.UNREPRESENTABLE_BOUND),
        (SelectionOptions(since=""), SelectionErrorCode.INVALID_DATE),
        (SelectionOptions(since="20261101"), SelectionErrorCode.INVALID_DATE),
        (SelectionOptions(since="2026-2-01"), SelectionErrorCode.INVALID_DATE),
        (SelectionOptions(since="2026-02-29"), SelectionErrorCode.INVALID_DATE),
        (SelectionOptions(since="2026-11-17"), SelectionErrorCode.FUTURE_DATE),
        (
            SelectionOptions(init_coros_reference="2026-11-17"),
            SelectionErrorCode.FUTURE_DATE,
        ),
        (
            SelectionOptions(init_coros_reference="invalid"),
            SelectionErrorCode.INVALID_DATE,
        ),
    ],
)
def test_invalid_options_have_typed_errors(
    options: SelectionOptions, error: SelectionErrorCode
) -> None:
    with pytest.raises(SelectionError) as raised:
        resolve_selection(options, None, launched_at=LAUNCH, zone=PARIS)
    assert raised.value.code is error


def test_missing_reference_suggests_seven_days() -> None:
    with pytest.raises(SelectionError, match="--last-days 7 --output DIR") as raised:
        resolve_selection(SelectionOptions(), None, launched_at=LAUNCH, zone=PARIS)
    assert raised.value.code is SelectionErrorCode.MISSING_REFERENCE


def test_future_reference_within_same_day_is_refused() -> None:
    with pytest.raises(SelectionError) as raised:
        resolve_selection(
            SelectionOptions(),
            Reference(LAUNCH + timedelta(seconds=1)),
            launched_at=LAUNCH,
            zone=PARIS,
        )
    assert raised.value.code is SelectionErrorCode.FUTURE_REFERENCE


@pytest.mark.parametrize(
    ("day", "zone"), [("2026-03-08", "America/Havana"), ("0001-01-01", "Europe/Paris")]
)
def test_refuses_unrepresentable_midnight(day: str, zone: str) -> None:
    with pytest.raises(SelectionError) as raised:
        resolve_selection(
            SelectionOptions(since=day), None, launched_at=LAUNCH, zone=ZoneInfo(zone)
        )
    assert raised.value.code is SelectionErrorCode.UNREPRESENTABLE_BOUND


def test_refuses_launch_outside_local_calendar() -> None:
    with pytest.raises(SelectionError) as raised:
        resolve_selection(
            SelectionOptions(last_days=1),
            None,
            launched_at=datetime.min.replace(tzinfo=UTC),
            zone=ZoneInfo("America/New_York"),
        )
    assert raised.value.code is SelectionErrorCode.UNREPRESENTABLE_BOUND


def test_refuses_naive_launch_and_reversed_period() -> None:
    with pytest.raises(InvalidInstant):
        resolve_selection(
            SelectionOptions(last_days=1),
            None,
            launched_at=datetime(2026, 11, 16),
            zone=PARIS,
        )
    with pytest.raises(SelectionError) as raised:
        Period(LAUNCH, LAUNCH - timedelta(seconds=1), PARIS)
    assert raised.value.code is SelectionErrorCode.UNREPRESENTABLE_BOUND


@pytest.mark.parametrize("existing", [None, Reference(LAUNCH)])
def test_reposition_returns_old_and_new_reference_only(
    existing: Reference | None,
) -> None:
    result = resolve_selection(
        SelectionOptions(init_coros_reference="2026-11-01"),
        existing,
        launched_at=LAUNCH,
        zone=PARIS,
    )
    assert result == ReferenceRepositioning(
        existing, Reference(datetime(2026, 10, 31, 23, tzinfo=UTC)), PARIS
    )
