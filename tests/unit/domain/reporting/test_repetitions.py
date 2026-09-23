from dataclasses import replace
from datetime import datetime, timedelta
from math import sqrt

import pytest

from fit_to_md.domain.activity.entities import Activity, ActivityLap, ActivityRecord
from fit_to_md.domain.activity.timeline import (
    ActiveInterval,
    ActiveTimeline,
    TimelineSource,
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
from fit_to_md.domain.reporting.entities import RepetitionMetric, RepetitionReason
from fit_to_md.domain.reporting.repetitions import RepetitionAnalysisBuilder

START = datetime(2026, 9, 23, 8)
DURATION = WorkoutDuration(DurationKind.METERS, 400)
OPEN_TARGET = WorkoutTarget(TargetKind.OPEN)


def step(
    identity: str = "effort",
    target: WorkoutTarget = OPEN_TARGET,
    *,
    secondary: WorkoutTarget | None = None,
    issues: tuple[WorkoutIssue, ...] = (),
) -> WorkoutStep:
    return WorkoutStep(
        identity, "400 m effort", LapRole.WORK, DURATION, target, secondary, issues
    )


def lap(
    index: int,
    distance: float | None = 400,
    duration: float | None = 100,
    *,
    role: LapRole = LapRole.WORK,
    linked_step: WorkoutStep | None = None,
    start: float | None = None,
    end: float | None = None,
) -> ActivityLap:
    return ActivityLap(
        index,
        START + timedelta(seconds=start) if start is not None else None,
        START + timedelta(seconds=end) if end is not None else None,
        distance,
        duration,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        role=role,
        workout_step=linked_step,
    )


def build(*laps: ActivityLap, sport: str = "running", sub_sport: str | None = None):
    return RepetitionAnalysisBuilder().build(
        Activity(sport=sport, sub_sport=sub_sport, laps=laps)
    )


def test_six_linked_efforts_have_equal_weight_population_pace_statistics() -> None:
    linked = step()
    laps = tuple(
        lap(i, duration=seconds, linked_step=linked)
        for i, seconds in enumerate((100, 105, 95, 100, 100, 100), start=1)
    )
    result = build(*laps)
    assert result.reason is None
    assert result.excluded_count == 0
    assert len(result.groups) == 1
    group = result.groups[0]
    assert group.step_identity == "effort"
    assert group.count == 6
    assert group.lap_indices == (1, 2, 3, 4, 5, 6)
    assert group.metric == RepetitionMetric.PACE_SECONDS_PER_KM
    assert group.mean == pytest.approx(250)
    assert group.fastest == pytest.approx(237.5)
    assert group.slowest == pytest.approx(262.5)
    assert group.coefficient_variation_percent == pytest.approx(
        100 * sqrt(2 * 12.5**2 / 6) / 250
    )


def test_equal_efforts_have_zero_variation_and_native_totals_need_no_boundaries() -> (
    None
):
    linked = step()
    result = build(
        lap(7, linked_step=linked), lap(8, linked_step=linked), sub_sport="treadmill"
    )
    assert result.groups[0].coefficient_variation_percent == 0
    assert result.groups[0].mean == 250
    assert result.groups[0].metric == RepetitionMetric.PACE_SECONDS_PER_KM


def test_nonrunning_sport_compares_speed_with_correct_fastest_direction() -> None:
    linked = step()
    result = build(
        lap(1, 100, 20, linked_step=linked),
        lap(2, 100, 25, linked_step=linked),
        sport="cycling",
    )
    group = result.groups[0]
    assert group.metric == RepetitionMetric.SPEED_KMH
    assert group.mean == pytest.approx(16.2)
    assert group.fastest == pytest.approx(18)
    assert group.slowest == pytest.approx(14.4)
    assert group.coefficient_variation_percent == pytest.approx(100 * 1.8 / 16.2)


def test_identity_primary_and_secondary_targets_define_distinct_groups() -> None:
    fast = step("one", WorkoutTarget(TargetKind.SPEED_MPS, 3, 4))
    slow = step("two", WorkoutTarget(TargetKind.SPEED_MPS, 3, 4))
    alternate = step("one", WorkoutTarget(TargetKind.SPEED_MPS, 4, 5))
    secondary = step(
        "one",
        WorkoutTarget(TargetKind.SPEED_MPS, 3, 4),
        secondary=WorkoutTarget(TargetKind.CADENCE_RPM, 80, 90),
    )
    result = build(
        lap(1, linked_step=fast),
        lap(2, linked_step=fast),
        lap(3, linked_step=slow),
        lap(4, linked_step=slow),
        lap(5, linked_step=alternate),
        lap(6, linked_step=secondary),
    )
    assert len(result.groups) == 2
    assert [group.lap_indices for group in result.groups] == [(1, 2), (3, 4)]
    assert [item.reason for item in result.exclusions] == [
        RepetitionReason.INSUFFICIENT_REPETITIONS,
        RepetitionReason.INSUFFICIENT_REPETITIONS,
    ]


def test_unlinked_and_unknown_role_laps_never_form_inferred_group() -> None:
    result = build(
        lap(1, linked_step=None),
        lap(2, linked_step=None),
        lap(3, role=LapRole.UNKNOWN, linked_step=step()),
    )
    assert result.groups == ()
    assert result.reason == RepetitionReason.INSUFFICIENT_REPETITIONS
    assert [item.reason for item in result.exclusions] == [
        RepetitionReason.MISSING_STEP_ASSOCIATION,
        RepetitionReason.MISSING_STEP_ASSOCIATION,
    ]
    no_work = build(lap(1, role=LapRole.UNKNOWN, linked_step=step()))
    assert no_work.reason == RepetitionReason.NO_WORK_LAPS
    assert no_work.exclusions == ()


def test_incomparable_step_definitions_are_excluded() -> None:
    linked = step(issues=(WorkoutIssue.UNSUPPORTED_TARGET,))
    result = build(lap(1, linked_step=linked), lap(2, linked_step=linked))
    assert result.groups == ()
    assert all(
        item.reason == RepetitionReason.INCOMPARABLE_DEFINITION
        for item in result.exclusions
    )
    blank_identity = replace(step(), identity="")
    assert (
        build(lap(1, linked_step=blank_identity)).exclusions[0].reason
        == RepetitionReason.MISSING_STEP_ASSOCIATION
    )


def test_invalid_distance_or_duration_excludes_only_affected_lap() -> None:
    linked = step()
    result = build(
        lap(1, linked_step=linked),
        lap(2, linked_step=linked),
        lap(3, distance=0, linked_step=linked),
        lap(4, duration=float("nan"), linked_step=linked),
    )
    assert result.groups[0].count == 2
    assert result.groups[0].excluded_count == 2
    assert [item.lap_index for item in result.groups[0].excluded_laps] == [3, 4]
    assert all(
        item.reason == RepetitionReason.INVALID_MEASUREMENT
        for item in result.exclusions
    )


def test_single_valid_member_is_excluded_with_insufficient_group_reason() -> None:
    linked = step()
    result = build(lap(1, linked_step=linked), lap(2, distance=0, linked_step=linked))
    assert result.groups == ()
    assert result.excluded_count == 2
    assert {item.reason for item in result.exclusions} == {
        RepetitionReason.INVALID_MEASUREMENT,
        RepetitionReason.INSUFFICIENT_REPETITIONS,
    }


def test_overlapping_work_laps_are_excluded_even_with_valid_native_totals() -> None:
    linked = step()
    result = build(
        lap(1, linked_step=linked, start=0, end=10),
        lap(2, linked_step=linked, start=8, end=20),
    )
    assert result.groups == ()
    assert [item.reason for item in result.exclusions] == [
        RepetitionReason.OVERLAPPING_LAP,
        RepetitionReason.OVERLAPPING_LAP,
    ]


def test_conflicting_lap_role_note_does_not_erase_explicit_work_link() -> None:
    linked = step()
    source = replace(
        lap(1, linked_step=linked), workout_issues=(WorkoutIssue.ROLE_CONFLICT,)
    )
    result = build(source, lap(2, linked_step=linked))
    assert result.groups[0].count == 2


def test_comparison_accepts_safe_record_derived_distance_and_duration() -> None:
    linked = step()
    records = tuple(
        ActivityRecord(
            START + timedelta(seconds=seconds),
            None,
            distance,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        )
        for seconds, distance in ((0, 0), (5, 200), (10, 400), (15, 600), (20, 800))
    )
    activity = Activity(
        sport="running",
        laps=(
            lap(1, distance=None, duration=None, linked_step=linked, start=0, end=10),
            lap(2, distance=None, duration=None, linked_step=linked, start=10, end=20),
        ),
        records=records,
        active_timeline=ActiveTimeline(
            (ActiveInterval(START, START + timedelta(seconds=20)),),
            TimelineSource.TIMER_EVENTS,
            None,
        ),
    )
    group = RepetitionAnalysisBuilder().build(activity).groups[0]
    assert group.count == 2
    assert group.mean == 25
    assert group.coefficient_variation_percent == 0
