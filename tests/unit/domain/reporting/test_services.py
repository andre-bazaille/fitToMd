from collections.abc import Callable
from datetime import datetime, timedelta

import pytest

from fit_to_md.domain.activity import Activity, ActivityRecord
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
