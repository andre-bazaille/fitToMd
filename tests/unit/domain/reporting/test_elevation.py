from datetime import datetime

import pytest

from fit_to_md.domain.activity import ActivityRecord
from fit_to_md.domain.reporting import ElevationEnricher


def _record(
    distance_m: float,
    altitude_m: float,
    *,
    latitude_deg: float = 48.0,
    longitude_deg: float = 2.0,
) -> ActivityRecord:
    return ActivityRecord(
        timestamp=datetime(2026, 9, 17),
        elapsed_time_s=distance_m,
        distance_m=distance_m,
        latitude_deg=latitude_deg + (distance_m / 100_000),
        longitude_deg=longitude_deg + (distance_m / 100_000),
        heart_rate_bpm=None,
        cadence_spm=None,
        fractional_cadence=None,
        speed_mps=None,
        altitude_m=altitude_m,
        grade_percent=5.0,
        temperature_c=None,
    )


def test_elevation_enricher_samples_route_and_applies_contiguous_dem_profile() -> None:
    records = (_record(0.0, 50.0), _record(50.0, 60.0), _record(100.0, 70.0))
    enricher = ElevationEnricher(sample_distance_m=100.0)

    coordinates = enricher.sample_coordinates(records)
    enriched = enricher.enrich_records(records, (10.0, 20.0), "dem")

    assert len(coordinates) == 2
    assert [record.altitude_m for record in enriched] == [10.0, 15.0, 20.0]
    assert [record.grade_percent for record in enriched] == [None, None, None]


def test_elevation_enricher_does_not_bridge_missing_coverage() -> None:
    records = (
        _record(0.0, 50.0),
        _record(100.0, 60.0),
        _record(200.0, 70.0),
    )
    enricher = ElevationEnricher(sample_distance_m=100.0)

    enriched = enricher.enrich_records(records, (10.0, None, 30.0), "dem")

    assert enriched is records


@pytest.mark.parametrize("invalid_value", (0.0, float("nan"), float("inf")))
def test_elevation_enricher_rejects_invalid_sample_distance(
    invalid_value: float,
) -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        ElevationEnricher(sample_distance_m=invalid_value)
