from collections.abc import Callable
from datetime import datetime, timedelta

import pytest

from fit_to_md.domain.activity import Activity, ActivityLap, ActivityRecord
from fit_to_md.domain.reporting.services import (
    SessionSummaryBuilder,
    SplitBuilder,
    TransitionBuilder,
)


def test_reporting_services_build_report_parts_from_domain_activity() -> None:
    start = datetime(2026, 8, 27, 7, 0, 0)
    activity = Activity(
        sport="running",
        records=(
            _record(
                start,
                elapsed_time_s=0.0,
                distance_m=0.0,
                altitude_m=10.0,
                heart_rate_bpm=120,
            ),
            _record(
                start + timedelta(seconds=300),
                elapsed_time_s=300.0,
                distance_m=1000.0,
                altitude_m=20.0,
                heart_rate_bpm=140,
            ),
        ),
    )

    summary = SessionSummaryBuilder().build(activity)
    splits = SplitBuilder().build(activity)
    dynamics = TransitionBuilder(sample_interval_s=300).build(activity)

    assert summary.activity_type == "Running"
    assert summary.total_distance_km == pytest.approx(1.0)
    assert summary.total_timer_time_s == pytest.approx(300.0)
    assert len(splits) == 1
    assert splits[0].elevation_delta_m == pytest.approx(10.0)
    assert splits[0].avg_heart_rate_bpm == 140
    assert len(dynamics) == 1
    assert [sample.elapsed_seconds for sample in dynamics[0].samples] == [0.0, 300.0]


def test_reporting_uses_record_boundaries_instead_of_workout_laps() -> None:
    start = datetime(2026, 9, 16, 7, 0, 0)
    activity = Activity(
        laps=(
            _lap(1, distance_m=250.0, timer_time_s=100.0),
            _lap(2, distance_m=1000.0, timer_time_s=100.0),
            _lap(3, distance_m=1000.0, timer_time_s=100.0),
        ),
        records=(
            _record(start, 0.0, 0.0, 10.0, 120),
            _record(start + timedelta(seconds=100), 100.0, 250.0, 11.0, 130),
            _record(start + timedelta(seconds=200), 200.0, 1250.0, 12.0, 140),
            _record(start + timedelta(seconds=300), 300.0, 2250.0, 13.0, 150),
        ),
    )

    splits = SplitBuilder().build(activity)
    dynamics = TransitionBuilder(sample_interval_s=100).build(activity)

    assert [split.kilometer for split in splits] == [1, 2]
    assert [split.time_seconds for split in splits] == pytest.approx([175.0, 100.0])
    assert [transition.label for transition in dynamics] == ["Km 1", "Km 2"]
    assert [transition.samples[-1].elapsed_seconds for transition in dynamics] == (
        pytest.approx([175.0, 100.0])
    )
    assert [transition.samples[-1].heart_rate_bpm for transition in dynamics] == [
        138,
        148,
    ]


def test_reporting_ignores_interleaved_recovery_laps_when_records_are_usable() -> None:
    start = datetime(2026, 9, 16, 8, 0, 0)
    activity = Activity(
        laps=(
            _lap(1, distance_m=1000.0, timer_time_s=100.0),
            _lap(2, distance_m=200.0, timer_time_s=20.0),
            _lap(3, distance_m=1000.0, timer_time_s=100.0),
        ),
        records=(
            _record(start, 0.0, 0.0, 10.0, 120),
            _record(start + timedelta(seconds=100), 100.0, 1000.0, 11.0, 130),
            _record(start + timedelta(seconds=120), 120.0, 1200.0, 12.0, 135),
            _record(start + timedelta(seconds=220), 220.0, 2200.0, 13.0, 145),
        ),
    )

    splits = SplitBuilder().build(activity)
    dynamics = TransitionBuilder(sample_interval_s=100).build(activity)

    assert [split.kilometer for split in splits] == [1, 2]
    assert [split.time_seconds for split in splits] == pytest.approx([100.0, 100.0])
    assert [transition.samples[-1].elapsed_seconds for transition in dynamics] == (
        pytest.approx([100.0, 100.0])
    )


def test_reporting_omits_sub_kilometer_lap_output() -> None:
    start = datetime(2026, 9, 16, 9, 0, 0)
    activity = Activity(
        laps=(_lap(1, distance_m=500.0, timer_time_s=50.0),),
        records=(
            _record(start, 0.0, 0.0, 10.0, 120),
            _record(start + timedelta(seconds=50), 50.0, 500.0, 11.0, 130),
        ),
    )

    assert SplitBuilder().build(activity) == ()
    assert TransitionBuilder().build(activity) == ()


def test_reporting_uses_sequential_labels_for_aligned_lap_only_fallback() -> None:
    activity = Activity(
        laps=(
            _lap(7, distance_m=990.0, timer_time_s=300.0),
            _lap(8, distance_m=1010.0, timer_time_s=310.0),
        )
    )

    splits = SplitBuilder().build(activity, prefer_records=True)

    assert [split.kilometer for split in splits] == [1, 2]
    assert [split.time_seconds for split in splits] == pytest.approx([300.0, 310.0])


@pytest.mark.parametrize(
    ("lap_distances_m", "expected_kilometers"),
    (
        ((250.0, 1000.0), []),
        ((1000.0, 200.0, 1000.0), [1]),
    ),
)
def test_reporting_stops_lap_fallback_at_first_misaligned_lap(
    lap_distances_m: tuple[float, ...],
    expected_kilometers: list[int],
) -> None:
    activity = Activity(
        laps=tuple(
            _lap(index, distance_m=distance_m, timer_time_s=100.0)
            for index, distance_m in enumerate(lap_distances_m, start=1)
        )
    )

    assert [
        split.kilometer for split in SplitBuilder().build(activity)
    ] == expected_kilometers


@pytest.mark.parametrize(
    "factory",
    (
        lambda: SessionSummaryBuilder(elevation_smoothing_distance_m=0),
        lambda: SessionSummaryBuilder(min_elevation_change_m=-1),
        lambda: TransitionBuilder(sample_interval_s=0),
        lambda: TransitionBuilder(elevation_smoothing_distance_m=0),
        lambda: TransitionBuilder(grade_distance_m=0),
    ),
)
def test_reporting_services_reject_invalid_configuration(
    factory: Callable[[], object],
) -> None:
    with pytest.raises(ValueError):
        factory()


def _record(
    timestamp: datetime,
    elapsed_time_s: float,
    distance_m: float,
    altitude_m: float,
    heart_rate_bpm: int,
) -> ActivityRecord:
    return ActivityRecord(
        timestamp=timestamp,
        elapsed_time_s=elapsed_time_s,
        distance_m=distance_m,
        latitude_deg=None,
        longitude_deg=None,
        heart_rate_bpm=heart_rate_bpm,
        cadence_spm=160,
        fractional_cadence=None,
        speed_mps=1000 / 300,
        altitude_m=altitude_m,
        grade_percent=None,
        temperature_c=None,
    )


def _lap(index: int, distance_m: float, timer_time_s: float) -> ActivityLap:
    return ActivityLap(
        index=index,
        start_time=None,
        end_time=None,
        total_distance_m=distance_m,
        total_timer_time_s=timer_time_s,
        total_ascent_m=None,
        total_descent_m=None,
        avg_heart_rate_bpm=None,
        max_heart_rate_bpm=None,
        avg_cadence_spm=None,
        avg_temperature_c=None,
        min_temperature_c=None,
        max_temperature_c=None,
    )
