from datetime import datetime, timedelta

import pytest

from fit_to_md.domain.activity.entities import (
    Activity,
    ActivityLap,
    ActivityRecord,
    ActivitySession,
)
from fit_to_md.domain.activity.timeline import (
    ActiveInterval,
    ActiveTimeline,
    TimelineSource,
)
from fit_to_md.domain.reporting.entities import MeasurementReason
from fit_to_md.domain.reporting.heart_rate_zones import (
    HeartRateZoneBoundaries,
    HeartRateZoneBuilder,
    summarize_zone_spans,
)
from fit_to_md.domain.reporting.measurements import ValueSpan

START = datetime(2026, 9, 23, 8)
BOUNDARIES = HeartRateZoneBoundaries((130, 145, 160, 175))


def at(seconds: float) -> datetime:
    return START + timedelta(seconds=seconds)


def record(seconds: float, hr: int | None) -> ActivityRecord:
    return ActivityRecord(
        at(seconds), None, None, None, None, hr, None, None, None, None, None, None
    )


def lap(
    index: int, start: float | None, end: float | None, duration: float | None = None
) -> ActivityLap:
    return ActivityLap(
        index,
        at(start) if start is not None else None,
        at(end) if end is not None else None,
        None,
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
        tuple(ActiveInterval(at(start), at(end)) for start, end in pairs),
        TimelineSource.TIMER_EVENTS,
        None,
    )


def activity(
    records: tuple[ActivityRecord, ...],
    active: ActiveTimeline,
    *,
    total: float | None = None,
    laps: tuple[ActivityLap, ...] = (),
) -> Activity:
    return Activity(
        session=ActivitySession(total_timer_time_s=total),
        records=records,
        active_timeline=active,
        laps=laps,
    )


@pytest.mark.parametrize(
    "values",
    [
        (),
        (130,),
        (130, 145, 160),
        (130, 145, 160, 175, 190),
        (0, 145, 160, 175),
        (-1, 145, 160, 175),
        (130, 130, 160, 175),
        (130, 160, 145, 175),
        (True, 145, 160, 175),
        (130.0, 145, 160, 175),
        ("130", 145, 160, 175),
        [130, 145, 160, 175],
    ],
)
def test_programmatic_zone_boundaries_reject_invalid_inputs(values: object) -> None:
    with pytest.raises(ValueError, match="four positive"):
        HeartRateZoneBoundaries(values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("heart_rate", "expected_zone"),
    [(129, 0), (130, 1), (145, 2), (160, 3), (175, 4), (190, 4)],
)
def test_exact_threshold_enters_higher_zone(
    heart_rate: int, expected_zone: int
) -> None:
    assert BOUNDARIES.zone_index(heart_rate) == expected_zone


@pytest.mark.parametrize("heart_rate", [0, -1, float("nan"), float("inf"), True, "130"])
def test_zone_assignment_rejects_unusable_heart_rate(heart_rate: object) -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        BOUNDARIES.zone_index(heart_rate)  # type: ignore[arg-type]


def test_hand_calculated_six_second_fixture_includes_terminal_sample() -> None:
    report = HeartRateZoneBuilder(BOUNDARIES).build(
        activity(
            tuple(
                record(i, hr)
                for i, hr in enumerate((129, 130, 145, 160, 175, 175, 175))
            ),
            timeline((0, 6)),
            total=6,
        )
    )
    session = report.session
    assert report.boundaries.thresholds_bpm == (130, 145, 160, 175)
    assert session.zone_seconds == (1, 1, 1, 1, 2)
    assert session.coverage.covered_seconds == 6
    assert sum(session.zone_seconds) == session.coverage.covered_seconds
    assert session.zone_percentages == pytest.approx(
        (100 / 6, 100 / 6, 100 / 6, 100 / 6, 200 / 6)
    )
    assert session.coverage_percent == 100
    assert session.unknown_seconds == 0
    assert session.issues == ()


def test_session_includes_time_outside_laps_and_laps_share_no_boundary_duration() -> (
    None
):
    report = HeartRateZoneBuilder(BOUNDARIES).build(
        activity(
            tuple(record(i, 150) for i in range(7)),
            timeline((0, 6)),
            total=6,
            laps=(lap(4, 1, 3, 2), lap(5, 3, 5, 2)),
        )
    )
    assert report.session.zone_seconds == (0, 0, 6, 0, 0)
    assert [row.lap_index for row in report.laps] == [4, 5]
    assert [row.measurement.zone_seconds for row in report.laps] == [
        (0, 0, 2, 0, 0),
        (0, 0, 2, 0, 0),
    ]
    assert [row.measurement.coverage.covered_seconds for row in report.laps] == [2, 2]
    assert (
        HeartRateZoneBuilder(BOUNDARIES)
        .build(
            activity((record(0, 150), record(6, 150)), timeline((0, 6))),
            include_laps=False,
        )
        .laps
        == ()
    )


def test_pauses_are_excluded_from_session_and_lap_coverage() -> None:
    report = HeartRateZoneBuilder(BOUNDARIES).build(
        activity(
            tuple(record(i, 150) for i in range(10)),
            timeline((0, 3), (6, 9)),
            total=10,
            laps=(lap(1, 0, 9),),
        )
    )
    assert report.session.zone_seconds == (0, 0, 6, 0, 0)
    assert report.session.coverage_percent == 60
    assert report.session.unknown_seconds == 4
    assert report.laps[0].measurement.coverage.total_active_seconds == 6
    assert report.laps[0].measurement.coverage_percent == 100


def test_sparse_missing_invalid_and_duplicate_hr_leave_unknown_time() -> None:
    report = HeartRateZoneBuilder(BOUNDARIES).build(
        activity(
            (
                record(20, 150),
                record(8, 130),
                record(0, 120),
                record(8, 0),
                record(12, 140),
                record(17, None),
                record(25, 160),
            ),
            timeline((0, 25)),
            total=25,
        )
    )
    session = report.session
    assert session.zone_seconds == (5, 5, 5, 0, 0)
    assert session.coverage.covered_seconds == 15
    assert session.coverage_percent == 60
    assert session.unknown_seconds == 10
    assert sum(session.zone_seconds) == session.coverage.covered_seconds


def test_zero_hr_coverage_has_zero_zone_seconds_and_no_zone_percentages() -> None:
    session = (
        HeartRateZoneBuilder(BOUNDARIES)
        .build(
            activity((record(0, None), record(10, None)), timeline((0, 10)), total=10)
        )
        .session
    )
    assert session.zone_seconds == (0, 0, 0, 0, 0)
    assert session.zone_percentages == (None, None, None, None, None)
    assert session.coverage.covered_seconds == 0
    assert session.coverage_percent == 0
    assert session.unknown_seconds == 10
    assert session.issues == (MeasurementReason.NO_HR_COVERAGE,)


def test_unknown_active_timing_keeps_native_total_but_omits_zones() -> None:
    report = HeartRateZoneBuilder(BOUNDARIES).build(
        activity(
            (record(0, 140), record(10, 140)),
            ActiveTimeline(),
            total=10,
            laps=(lap(1, 0, 10, 10),),
        )
    )
    for measurement in (report.session, report.laps[0].measurement):
        assert measurement.zone_seconds is None
        assert measurement.zone_percentages is None
        assert measurement.coverage.covered_seconds is None
        assert measurement.coverage.total_active_seconds == 10
        assert measurement.coverage_percent is None
        assert measurement.issues == (MeasurementReason.ACTIVE_TIMING_UNAVAILABLE,)


def test_unsafe_laps_are_unavailable_while_session_zones_remain_valid() -> None:
    report = HeartRateZoneBuilder(BOUNDARIES).build(
        activity(
            tuple(record(i, 150) for i in range(21)),
            timeline((0, 20)),
            total=20,
            laps=(
                lap(1, 0, 10, 10),
                lap(2, 8, 20, 12),
                lap(3, 20, 19, 1),
                lap(4, None, None, 2),
            ),
        )
    )
    assert report.session.zone_seconds == (0, 0, 20, 0, 0)
    assert [row.measurement.issues for row in report.laps] == [
        (MeasurementReason.OVERLAPPING_LAPS,),
        (MeasurementReason.OVERLAPPING_LAPS,),
        (MeasurementReason.REVERSED_BOUNDARIES,),
        (MeasurementReason.MISSING_BOUNDARIES,),
    ]
    assert [row.measurement.coverage.total_active_seconds for row in report.laps] == [
        10,
        12,
        1,
        2,
    ]
    assert all(row.measurement.zone_seconds is None for row in report.laps)


def test_unknown_total_keeps_covered_seconds_without_coverage_percentage() -> None:
    measured = summarize_zone_spans(BOUNDARIES, (ValueSpan(at(0), at(2), 140),), None)
    assert measured.zone_seconds == (0, 2, 0, 0, 0)
    assert measured.zone_percentages == (0, 100, 0, 0, 0)
    assert measured.coverage.covered_seconds == 2
    assert measured.coverage.total_active_seconds is None
    assert measured.coverage_percent is None
    assert measured.unknown_seconds is None
    assert measured.issues == (MeasurementReason.TOTAL_ACTIVE_TIME_UNAVAILABLE,)


def test_small_native_discrepancy_caps_coverage_without_rescaling_zone_time() -> None:
    spans = (ValueSpan(at(0), at(10), 175),)
    within_tolerance = summarize_zone_spans(BOUNDARIES, spans, 9)
    assert within_tolerance.zone_seconds == (0, 0, 0, 0, 10)
    assert within_tolerance.coverage.covered_seconds == 10
    assert within_tolerance.coverage_percent == 100
    assert within_tolerance.unknown_seconds == 0
    assert within_tolerance.issues == ()
    inconsistent = summarize_zone_spans(BOUNDARIES, spans, 8.9)
    assert inconsistent.zone_seconds == (0, 0, 0, 0, 10)
    assert inconsistent.zone_percentages == (0, 0, 0, 0, 100)
    assert inconsistent.coverage_percent is None
    assert inconsistent.unknown_seconds is None
    assert inconsistent.issues == (MeasurementReason.INCONSISTENT_TIMING,)


def test_builder_flags_record_coverage_exceeding_native_session_and_lap_totals() -> (
    None
):
    report = HeartRateZoneBuilder(BOUNDARIES).build(
        activity(
            tuple(record(i, 130) for i in range(11)),
            timeline((0, 10)),
            total=8,
            laps=(lap(1, 0, 10, 8),),
        )
    )
    assert report.session.issues == (MeasurementReason.INCONSISTENT_TIMING,)
    assert report.laps[0].measurement.issues == (MeasurementReason.INCONSISTENT_TIMING,)
    assert report.session.zone_seconds == (0, 10, 0, 0, 0)


def test_missing_native_total_uses_reliable_active_timeline() -> None:
    report = HeartRateZoneBuilder(BOUNDARIES).build(
        activity(
            (record(0, 130), record(5, 130)),
            timeline((0, 5)),
            total=float("nan"),
            laps=(lap(1, 0, 5, None),),
        )
    )
    assert report.session.coverage.total_active_seconds == 5
    assert report.laps[0].measurement.coverage.total_active_seconds == 5
    assert report.session.coverage_percent == 100


@pytest.mark.parametrize("invalid_total", [-1.0, float("nan"), float("inf")])
def test_span_summary_rejects_invalid_active_total(invalid_total: float) -> None:
    with pytest.raises(ValueError, match="finite and nonnegative"):
        summarize_zone_spans(BOUNDARIES, (), invalid_total)


def test_builder_requires_explicit_zone_configuration() -> None:
    with pytest.raises(TypeError, match="HeartRateZoneBoundaries"):
        HeartRateZoneBuilder(None)  # type: ignore[arg-type]


def test_final_sample_does_not_extend_hr_coverage_to_session_end() -> None:
    session = (
        HeartRateZoneBuilder(BOUNDARIES)
        .build(activity((record(0, 130), record(5, 145)), timeline((0, 10)), total=10))
        .session
    )
    assert session.zone_seconds == (0, 5, 0, 0, 0)
    assert session.coverage.covered_seconds == 5
    assert session.unknown_seconds == 5
