from dataclasses import FrozenInstanceError
from datetime import timedelta

import pytest

from fit_to_md.domain.activity.entities import Activity
from fit_to_md.domain.activity.timeline import (
    ActiveInterval,
    ActiveTimeline,
    TimelineIssue,
    TimelineSource,
    TimerState,
    TimerTransition,
    build_active_timeline,
)
from fit_to_md.domain.activity.workout import (
    DurationKind,
    LapRole,
    TargetKind,
    WorkoutDuration,
    WorkoutIssue,
    WorkoutStep,
    WorkoutTarget,
)
from tests.support.workout import START


def event(seconds: int, state: TimerState) -> TimerTransition:
    return TimerTransition(START + timedelta(seconds=seconds), state)


def timeline(events=(), timer=100.0, elapsed=100.0, **kwargs):
    return build_active_timeline(
        START,
        START + timedelta(seconds=100),
        timer,
        elapsed,
        tuple(events),
        **kwargs,
    )


def test_old_activity_constructor_has_unknown_timing() -> None:
    assert Activity().active_timeline == ActiveTimeline()
    assert not Activity().active_timeline.is_known
    known_empty = ActiveTimeline((), TimelineSource.SESSION_TOTALS, None)
    assert known_empty.is_known
    assert known_empty != Activity().active_timeline


def test_immutable_duration_target_and_step_contracts() -> None:
    step = WorkoutStep(
        "effort",
        "400 m",
        LapRole.WORK,
        WorkoutDuration(DurationKind.METERS, 400),
        WorkoutTarget(TargetKind.OPEN),
    )
    assert step.has_comparable_definition
    with pytest.raises(FrozenInstanceError):
        step.label = "changed"
    unsupported = WorkoutStep(
        "x",
        None,
        LapRole.UNKNOWN,
        None,
        None,
        issues=(WorkoutIssue.UNSUPPORTED_DURATION,),
    )
    assert not unsupported.has_comparable_definition
    assert WorkoutTarget(TargetKind.SPEED_MPS, 2, 4).upper == 4
    assert WorkoutDuration(DurationKind.OPEN).value is None


@pytest.mark.parametrize("value", [None, 0, -1, True, float("nan"), float("inf")])
def test_duration_rejects_invalid_values(value) -> None:
    with pytest.raises(ValueError):
        WorkoutDuration(DurationKind.SECONDS, value)


@pytest.mark.parametrize(
    "args",
    [
        (TargetKind.OPEN, 1, None),
        (TargetKind.SPEED_MPS, None, 3),
        (TargetKind.CADENCE_RPM, -1, 5),
        (TargetKind.SPEED_MPS, 4, 3),
        (TargetKind.SPEED_MPS, 0, 0),
        (TargetKind.SPEED_MPS, True, 3),
        (TargetKind.SPEED_MPS, 1, float("inf")),
        ("unsupported", None, None),
    ],
)
def test_target_rejects_invalid_bounds(args) -> None:
    with pytest.raises(ValueError):
        WorkoutTarget(*args)


def test_duration_rejects_invalid_kind_and_open_value() -> None:
    with pytest.raises(ValueError):
        WorkoutDuration("bad")
    with pytest.raises(ValueError):
        WorkoutDuration(DurationKind.OPEN, 10)


def test_timeline_keeps_full_session_bounds_without_record_clipping() -> None:
    result = timeline([event(0, TimerState.START), event(100, TimerState.STOP)])
    assert result.source == TimelineSource.TIMER_EVENTS
    assert result.intervals == (ActiveInterval(START, START + timedelta(seconds=100)),)


def test_timeline_sorts_deduplicates_and_preserves_pauses() -> None:
    result = timeline(
        [
            event(100, TimerState.STOP),
            event(0, TimerState.START),
            event(20, TimerState.STOP),
            event(0, TimerState.START),
            event(40, TimerState.START),
        ],
        timer=80,
    )
    assert result.is_known
    assert [(s.start - START).total_seconds() for s in result.intervals] == [0, 40]
    assert [(s.end - START).total_seconds() for s in result.intervals] == [20, 100]


@pytest.mark.parametrize(
    "events",
    [
        [event(50, TimerState.STOP)],
        [event(0, TimerState.START), event(20, TimerState.START)],
        [
            event(0, TimerState.START),
            event(20, TimerState.STOP),
            event(20, TimerState.START),
        ],
        [event(-1, TimerState.START)],
        [event(101, TimerState.STOP)],
        [TimerTransition(START, "unsupported")],
    ],
)
def test_timeline_rejects_uncertain_events(events) -> None:
    result = timeline(events)
    assert not result.is_known
    assert result.issue == TimelineIssue.INVALID_EVENTS


def test_open_start_requires_matching_total() -> None:
    assert timeline([event(0, TimerState.START)]).is_known
    assert not timeline([event(0, TimerState.START)], timer=90).is_known
    assert not timeline([event(0, TimerState.START)], timer=None).is_known


@pytest.mark.parametrize(
    "timer,elapsed,known",
    [
        (100, 100, True),
        (99, 100, True),
        (98, 100, False),
        (100, 102, False),
        (None, 100, False),
        (100, None, False),
        (float("nan"), 100, False),
        (-1, 100, False),
        (True, 100, False),
    ],
)
def test_no_event_fallback_requires_consistent_totals(timer, elapsed, known) -> None:
    assert timeline(timer=timer, elapsed=elapsed).is_known == known


def test_invalid_events_do_not_fall_back_to_equal_totals() -> None:
    assert timeline(invalid_events=True).issue == TimelineIssue.INVALID_EVENTS


def test_closed_events_reject_inconsistent_present_totals() -> None:
    events = [event(0, TimerState.START), event(100, TimerState.STOP)]
    assert timeline(events, timer=None, elapsed=None).is_known
    assert not timeline(events, timer=90).is_known
    assert not timeline(events, elapsed=float("nan")).is_known


def test_missing_reversed_and_mixed_timezone_bounds_are_unknown() -> None:
    assert not build_active_timeline(None, START, 0, 0, ()).is_known
    assert not build_active_timeline(
        START, START - timedelta(seconds=1), 0, 0, ()
    ).is_known
    assert not build_active_timeline(
        START.replace(tzinfo=None), START, 0, 0, ()
    ).is_known
    assert not timeline(
        [TimerTransition(START.replace(tzinfo=None), TimerState.START)]
    ).is_known


def test_known_zero_duration_is_distinct_from_missing_evidence() -> None:
    result = build_active_timeline(START, START, 0, 0, ())
    assert result.is_known and result.intervals == ()


def test_interval_and_timeline_enforce_invariants() -> None:
    interval = ActiveInterval(START, START + timedelta(seconds=10))
    with pytest.raises(ValueError):
        ActiveInterval(START, START)
    with pytest.raises(ValueError):
        ActiveInterval(START, START.replace(tzinfo=None))
    with pytest.raises(ValueError):
        ActiveTimeline((interval,))
    with pytest.raises(ValueError):
        ActiveTimeline(source=TimelineSource.UNKNOWN, issue=None)
    with pytest.raises(ValueError):
        ActiveTimeline(source=TimelineSource.TIMER_EVENTS)
    with pytest.raises(ValueError):
        ActiveTimeline((interval, interval), TimelineSource.TIMER_EVENTS, None)
