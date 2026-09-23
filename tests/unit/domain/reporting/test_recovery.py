from datetime import datetime, timedelta

import pytest

from fit_to_md.domain.activity.entities import Activity, ActivityLap, ActivityRecord
from fit_to_md.domain.activity.timeline import (
    ActiveInterval,
    ActiveTimeline,
    TimelineSource,
)
from fit_to_md.domain.activity.workout import LapRole
from fit_to_md.domain.reporting.entities import RecoveryReason
from fit_to_md.domain.reporting.recovery import RecoveryAnalysisBuilder

START = datetime(2026, 9, 23, 8)


def at(seconds: float) -> datetime:
    return START + timedelta(seconds=seconds)


def record(seconds: float, hr: int | None) -> ActivityRecord:
    return ActivityRecord(
        at(seconds), None, None, None, None, hr, None, None, None, None, None, None
    )


def lap(
    index: int,
    start: float | None,
    end: float | None,
    role: LapRole,
    duration: float | None = None,
) -> ActivityLap:
    return ActivityLap(
        index,
        at(start) if start is not None else None,
        at(end) if end is not None else None,
        None,
        duration
        if duration is not None
        else (end - start if start is not None and end is not None else None),
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        role=role,
    )


def timeline(*pairs: tuple[float, float]) -> ActiveTimeline:
    return ActiveTimeline(
        tuple(ActiveInterval(at(a), at(b)) for a, b in pairs),
        TimelineSource.TIMER_EVENTS,
        None,
    )


def build(
    laps: tuple[ActivityLap, ...],
    records: tuple[ActivityRecord, ...] = (),
    active: ActiveTimeline | None = None,
):
    return RecoveryAnalysisBuilder().build(
        Activity(
            laps=laps,
            records=records,
            active_timeline=active if active is not None else timeline((0, 100)),
        )
    )


def work_and_recovery(
    end: float = 70, duration: float | None = None
) -> tuple[ActivityLap, ActivityLap]:
    return (
        lap(1, 0, 10, LapRole.WORK),
        lap(2, 10, end, LapRole.RECOVERY, duration),
    )


def test_exactly_sixty_seconds_uses_lap_end_observation_for_negative_change() -> None:
    result = build(
        work_and_recovery(),
        (record(10, 160), record(70, 130)),
        timeline((0, 70)),
    )
    assert len(result.changes) == 1
    change = result.changes[0]
    assert change.reason is None
    assert change.preceding_lap_index == 1
    assert change.start_target_time == at(10)
    assert change.sixty_second_target_time == at(70)
    assert change.start_observation_time == at(10)
    assert change.sixty_second_observation_time == at(70)
    assert change.start_observation_offset_s == 0
    assert change.sixty_second_observation_offset_s == 0
    assert change.delta_bpm == -30


def test_positive_change_is_signed_and_not_relabelled() -> None:
    change = build(
        work_and_recovery(), (record(10, 100), record(70, 130)), timeline((0, 70))
    ).changes[0]
    assert change.delta_bpm == 30
    assert change.start_hr_bpm == 100
    assert change.hr_60_bpm == 130


def test_nearest_endpoint_ties_choose_earlier_observation() -> None:
    change = build(
        work_and_recovery(end=80),
        (record(10, 160), record(68, 130), record(72, 120)),
        timeline((0, 80)),
    ).changes[0]
    assert change.sixty_second_target_time == at(70)
    assert change.sixty_second_observation_time == at(68)
    assert change.sixty_second_observation_offset_s == -2
    assert change.delta_bpm == -30


def test_recovery_start_observation_must_be_at_or_after_start() -> None:
    change = build(
        work_and_recovery(), (record(9, 170), record(70, 130)), timeline((0, 70))
    ).changes[0]
    assert change.reason == RecoveryReason.START_HR_UNAVAILABLE
    assert change.delta_bpm is None


def test_sparse_or_invalid_endpoints_are_unavailable() -> None:
    late_start = build(
        work_and_recovery(), (record(16, 160), record(70, 130)), timeline((0, 70))
    ).changes[0]
    assert late_start.reason == RecoveryReason.START_HR_UNAVAILABLE
    missing_end = build(
        work_and_recovery(), (record(10, 160), record(64, 130)), timeline((0, 70))
    ).changes[0]
    assert missing_end.reason == RecoveryReason.SIXTY_SECOND_HR_UNAVAILABLE
    assert missing_end.start_hr_bpm == 160
    invalid_end = build(
        work_and_recovery(), (record(10, 160), record(70, 0)), timeline((0, 70))
    ).changes[0]
    assert invalid_end.reason == RecoveryReason.SIXTY_SECOND_HR_UNAVAILABLE


def test_duplicate_timestamp_uses_last_source_hr() -> None:
    change = build(
        work_and_recovery(),
        (record(10, 160), record(70, 130), record(10, 155)),
        timeline((0, 70)),
    ).changes[0]
    assert change.start_hr_bpm == 155
    assert change.delta_bpm == -25


def test_recovery_under_sixty_active_seconds_is_unavailable() -> None:
    change = build(
        work_and_recovery(end=69),
        (record(10, 160), record(69, 130)),
        timeline((0, 69)),
    ).changes[0]
    assert change.reason == RecoveryReason.UNDER_60_ACTIVE_SECONDS
    assert change.delta_bpm is None


def test_pause_after_recovery_start_is_excluded_from_sixty_second_target() -> None:
    change = build(
        work_and_recovery(end=100, duration=80),
        (record(12, 160), record(78, 130), record(35, 100)),
        timeline((0, 30), (40, 100)),
    ).changes[0]
    assert change.reason is None
    assert change.start_target_time == at(10)
    assert change.start_observation_offset_s == 2
    assert change.sixty_second_target_time == at(80)
    assert change.sixty_second_observation_offset_s == -2
    assert change.delta_bpm == -30


def test_sixty_second_target_at_pause_onset_uses_stop_endpoint() -> None:
    change = build(
        work_and_recovery(end=100, duration=80),
        (record(10, 160), record(70, 125), record(80, 100)),
        timeline((0, 70), (80, 100)),
    ).changes[0]
    assert change.reason is None
    assert change.sixty_second_target_time == at(70)
    assert change.sixty_second_observation_time == at(70)
    assert change.delta_bpm == -35


def test_sixty_second_target_does_not_select_resume_sample_across_pause() -> None:
    change = build(
        work_and_recovery(end=100, duration=86),
        (record(10, 160), record(74, 100)),
        timeline((0, 70), (74, 100)),
    ).changes[0]
    assert change.sixty_second_target_time == at(70)
    assert change.reason == RecoveryReason.SIXTY_SECOND_HR_UNAVAILABLE


def test_recovery_beginning_inside_pause_has_no_start_endpoint() -> None:
    change = build(
        (lap(1, 0, 10, LapRole.WORK), lap(2, 20, 100, LapRole.RECOVERY, 75)),
        (record(25, 160), record(85, 130)),
        timeline((0, 15), (25, 100)),
    ).changes[0]
    assert change.reason == RecoveryReason.START_TARGET_UNAVAILABLE
    assert change.start_target_time is None
    assert change.delta_bpm is None


def test_nonwork_predecessor_and_unmarked_laps_do_not_produce_change() -> None:
    result = build(
        (lap(1, 0, 10, LapRole.UNKNOWN), lap(2, 10, 70, LapRole.RECOVERY)),
        (record(10, 160), record(70, 130)),
        timeline((0, 70)),
    )
    assert result.changes[0].reason == RecoveryReason.NOT_PRECEDED_BY_WORK
    assert build((lap(1, 0, 10, LapRole.UNKNOWN),)).changes == ()
    assert (
        build((lap(1, 0, 70, LapRole.RECOVERY),)).changes[0].reason
        == RecoveryReason.NOT_PRECEDED_BY_WORK
    )


def test_overlapping_or_missing_lap_boundaries_are_ambiguous() -> None:
    overlap = build(
        (lap(1, 0, 20, LapRole.WORK), lap(2, 15, 75, LapRole.RECOVERY)),
        active=timeline((0, 75)),
    ).changes[0]
    assert overlap.reason == RecoveryReason.AMBIGUOUS_LAP_TIMING
    missing = build(
        (lap(1, None, None, LapRole.WORK, 10), lap(2, 10, 70, LapRole.RECOVERY)),
        active=timeline((0, 70)),
    ).changes[0]
    assert missing.reason == RecoveryReason.AMBIGUOUS_LAP_TIMING


def test_unknown_timeline_and_inconsistent_native_duration_are_unavailable() -> None:
    unknown = build(work_and_recovery(), active=ActiveTimeline()).changes[0]
    assert unknown.reason == RecoveryReason.ACTIVE_TIMING_UNAVAILABLE
    inconsistent = build(
        work_and_recovery(duration=55), active=timeline((0, 70))
    ).changes[0]
    assert inconsistent.reason == RecoveryReason.INCONSISTENT_DURATION


def test_multiple_recoveries_preserve_source_positions_and_indices() -> None:
    result = build(
        (
            lap(7, 0, 10, LapRole.WORK),
            lap(8, 10, 70, LapRole.RECOVERY),
            lap(9, 70, 80, LapRole.WORK),
            lap(10, 80, 140, LapRole.RECOVERY),
        ),
        (record(10, 160), record(70, 130), record(80, 150), record(140, 120)),
        timeline((0, 140)),
    )
    assert [
        (change.source_position, change.lap_index, change.delta_bpm)
        for change in result.changes
    ] == [(1, 8, -30), (3, 10, -30)]


@pytest.mark.parametrize("offset", [-5, 5])
def test_observation_exactly_five_seconds_from_target_is_eligible(offset: int) -> None:
    change = build(
        work_and_recovery(end=80),
        (record(10, 160), record(70 + offset, 130)),
        timeline((0, 80)),
    ).changes[0]
    assert change.reason is None
    assert change.sixty_second_observation_offset_s == offset
