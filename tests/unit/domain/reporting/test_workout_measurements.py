from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from fit_to_md.domain.activity.entities import Activity, ActivityLap, ActivityRecord
from fit_to_md.domain.activity.timeline import (
    ActiveInterval,
    ActiveTimeline,
    TimelineSource,
)
from fit_to_md.domain.activity.workout import LapRole
from fit_to_md.domain.reporting.entities import MeasurementReason
from fit_to_md.domain.reporting.measurements import (
    active_duration,
    chronological_records,
    contains_lap_time,
    hr_spans,
    intersect_interval,
    wall_time_at_active_offset,
    weighted_average,
)
from fit_to_md.domain.reporting.workout import NativeLapReportBuilder

START = datetime(2026, 9, 23, 8)


def at(seconds: float) -> datetime:
    return START + timedelta(seconds=seconds)


def record(
    seconds: float,
    distance: float | None,
    hr: int | None = 120,
    cadence: int | None = 160,
) -> ActivityRecord:
    return ActivityRecord(
        at(seconds),
        None,
        distance,
        None,
        None,
        hr,
        cadence,
        None,
        None,
        None,
        None,
        None,
    )


def lap(
    index: int,
    start: float | None,
    end: float | None,
    distance: float | None = None,
    duration: float | None = None,
) -> ActivityLap:
    return ActivityLap(
        index,
        at(start) if start is not None else None,
        at(end) if end is not None else None,
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
    )


def timeline(*pairs: tuple[float, float]) -> ActiveTimeline:
    return ActiveTimeline(
        tuple(ActiveInterval(at(a), at(b)) for a, b in pairs),
        TimelineSource.TIMER_EVENTS,
        None,
    )


def activity(
    laps: tuple[ActivityLap, ...],
    records: tuple[ActivityRecord, ...] = (),
    active: ActiveTimeline | None = None,
    sport: str = "running",
) -> Activity:
    return Activity(
        sport=sport,
        laps=laps,
        records=records,
        active_timeline=active or timeline((0, 20)),
    )


def test_timeline_helpers_use_half_open_intersections_and_pause_offsets() -> None:
    active = timeline((0, 4), (10, 20))
    assert (
        intersect_interval(ActiveInterval(at(0), at(4)), ActiveInterval(at(4), at(8)))
        is None
    )
    assert active_duration(active, at(2), at(12)) == 4
    assert wall_time_at_active_offset(active, at(0), at(20), 4) == at(4)
    assert wall_time_at_active_offset(active, at(5), at(20), 0) is None
    assert wall_time_at_active_offset(active, at(0), at(20), 5) == at(11)
    assert wall_time_at_active_offset(active, at(0), at(20), 15) is None
    assert contains_lap_time(at(4), at(0), at(4)) is False
    assert contains_lap_time(at(4), at(4), at(8)) is True
    assert active_duration(ActiveTimeline(), at(0), at(10)) is None


def test_chronological_view_deduplicates_without_mutating_source() -> None:
    original = (record(5, 5, 130), record(0, 0), record(5, 6, 150))
    view = chronological_records(original)
    assert [r.timestamp for r in view] == [at(0), at(5)]
    assert view[-1].heart_rate_bpm == 150
    assert original[0].heart_rate_bpm == 130


def test_hr_spans_cap_sparse_samples_stop_at_missing_and_clip_pause() -> None:
    records = chronological_records(
        (
            record(0, 0, 100),
            record(10, 10, None),
            record(12, 12, 140),
            record(17, 17, 150),
            record(19, 19, 160),
        )
    )
    spans = hr_spans(records, timeline((0, 3), (8, 20)))
    assert [(s.start, s.end, s.value) for s in spans] == [
        (at(0), at(3), 100.0),
        (at(12), at(17), 140.0),
        (at(17), at(19), 150.0),
    ]
    assert weighted_average(spans) == pytest.approx((300 + 700 + 300) / 10)


def test_native_rows_preserve_manual_and_auto_laps_with_zero_distance() -> None:
    rows = NativeLapReportBuilder().build(
        activity(
            (
                replace(lap(7, 0, 10, 0, 10), role=LapRole.RECOVERY),
                lap(9, 10, 20, 100, 10),
            )
        )
    )
    assert [row.index for row in rows] == [7, 9]
    assert rows[0].distance_m == 0
    assert rows[0].pace_seconds_per_km is None
    assert rows[0].active_duration_s == 10
    assert rows[1].pace_seconds_per_km == 100
    assert rows[1].speed_kmh == 36
    assert rows[1].role == LapRole.UNKNOWN
    assert NativeLapReportBuilder().build(Activity()) == ()


def test_lap_only_native_aggregates_survive_unknown_timing_and_missing_boundaries() -> (
    None
):
    source = replace(
        lap(1, None, None, 400, 100),
        avg_heart_rate_bpm=150,
        max_heart_rate_bpm=170,
        avg_cadence_spm=0,
    )
    row = NativeLapReportBuilder().build(Activity(laps=(source,)))[0]
    assert (
        row.distance_m,
        row.active_duration_s,
        row.avg_heart_rate_bpm,
        row.max_heart_rate_bpm,
        row.avg_cadence_spm,
    ) == (400, 100, 150, 170, 0)
    assert row.pace_seconds_per_km == 250
    assert row.issues[0].reason == MeasurementReason.MISSING_BOUNDARIES


def test_time_weighted_hr_and_cadence_with_shared_boundary() -> None:
    rows = NativeLapReportBuilder().build(
        activity(
            (lap(1, 0, 5, 100, 5), lap(2, 5, 10, 100, 5)),
            (
                record(0, 0, 100, 100),
                record(2, 40, 200, 200),
                record(5, 100, 150, 150),
                record(10, 200, 190, 190),
            ),
            timeline((0, 10)),
        )
    )
    assert rows[0].hr_coverage.covered_seconds == 5
    assert rows[1].hr_coverage.covered_seconds == 5
    assert rows[0].avg_heart_rate_bpm == 160
    assert rows[0].avg_cadence_spm == 160
    assert rows[0].max_heart_rate_bpm == 200
    assert rows[1].avg_heart_rate_bpm == 150
    assert rows[1].max_heart_rate_bpm == 150


def test_pause_excluded_from_derived_duration_and_coverage() -> None:
    row = NativeLapReportBuilder().build(
        activity(
            (lap(1, 0, 20, 100, None),),
            (
                record(0, 0),
                record(5, 30),
                record(10, 30),
                record(15, 60),
                record(20, 100),
            ),
            timeline((0, 5), (10, 20)),
        )
    )[0]
    assert row.active_duration_s == 15
    assert row.hr_coverage.covered_seconds == 15
    assert row.pace_seconds_per_km == 150


def test_distance_interpolation_requires_short_same_active_span() -> None:
    base = (record(0, 0), record(2, 20), record(6, 60), record(8, 80))
    row = NativeLapReportBuilder().build(
        activity((lap(1, 3, 7),), base, timeline((0, 8)))
    )[0]
    assert row.distance_m == 40
    gap = NativeLapReportBuilder().build(
        activity((lap(1, 3, 7),), (record(0, 0), record(8, 80)), timeline((0, 8)))
    )[0]
    assert gap.distance_m is None
    assert gap.issues[-1].reason == MeasurementReason.DISTANCE_BOUNDARY_UNAVAILABLE
    paused = NativeLapReportBuilder().build(
        activity((lap(1, 3, 7),), base, timeline((0, 4), (5, 8)))
    )[0]
    assert paused.distance_m is None


def test_distance_rejects_invalid_or_decreasing_trace() -> None:
    for distances in ((0, 20, 10), (0, float("nan"), 30), (0, -2, 30)):
        records = tuple(record(i * 5, value) for i, value in enumerate(distances))
        row = NativeLapReportBuilder().build(
            activity((lap(1, 0, 10),), records, timeline((0, 10)))
        )[0]
        assert row.distance_m is None
        assert row.issues[-1].reason == MeasurementReason.INVALID_DISTANCE_TRACE


def test_overlaps_and_reversed_bounds_keep_native_totals_but_block_records() -> None:
    rows = NativeLapReportBuilder().build(
        activity(
            (lap(1, 0, 10, 100, 10), lap(2, 8, 20, 120, 12), lap(3, 20, 19, 10, 1)),
            (record(0, 0), record(5, 50), record(10, 100), record(20, 220)),
            timeline((0, 20)),
        )
    )
    assert [row.issues[0].reason for row in rows] == [
        MeasurementReason.OVERLAPPING_LAPS,
        MeasurementReason.OVERLAPPING_LAPS,
        MeasurementReason.REVERSED_BOUNDARIES,
    ]
    assert all(row.hr_coverage.covered_seconds == 0 for row in rows)
    assert rows[0].distance_m == 100
    assert rows[2].active_duration_s == 1


def test_unknown_timing_retains_native_values_and_omits_record_fallback() -> None:
    source = replace(lap(1, 0, 10, 100, None), avg_heart_rate_bpm=140)
    row = NativeLapReportBuilder().build(
        activity((source,), (record(0, 0), record(10, 100)), ActiveTimeline())
    )[0]
    assert row.distance_m == 100
    assert row.active_duration_s is None
    assert row.avg_heart_rate_bpm == 140
    assert row.hr_coverage.reason == MeasurementReason.ACTIVE_TIMING_UNAVAILABLE


def test_duplicate_lap_objects_are_detected_as_overlapping() -> None:
    source = lap(1, 0, 10, 100, 10)
    rows = NativeLapReportBuilder().build(activity((source, source)))
    assert all(
        row.issues[0].reason == MeasurementReason.OVERLAPPING_LAPS for row in rows
    )


def test_invalid_native_totals_fall_back_only_with_safe_evidence() -> None:
    source = replace(
        lap(1, 0, 10, float("nan"), float("inf")),
        avg_heart_rate_bpm=0,
        avg_cadence_spm=-1,
    )
    row = NativeLapReportBuilder().build(
        activity(
            (source,), (record(0, 0), record(5, 50), record(10, 100)), timeline((0, 10))
        )
    )[0]
    assert row.distance_m == 100
    assert row.active_duration_s == 10
    assert row.avg_heart_rate_bpm == 120
    assert row.avg_cadence_spm == 160
    assert row.hr_coverage.covered_seconds == 10
    unavailable = NativeLapReportBuilder().build(Activity(laps=(source,)))[0]
    assert unavailable.distance_m is None
    assert unavailable.active_duration_s is None


def test_hr_spans_sort_and_deduplicate_unordered_input() -> None:
    spans = hr_spans(
        (
            record(5, 50, 130),
            record(0, 0, 100),
            record(5, 50, 150),
            record(10, 100, 160),
        ),
        timeline((0, 10)),
    )
    assert [(span.value, span.seconds) for span in spans] == [(100, 5), (150, 5)]
