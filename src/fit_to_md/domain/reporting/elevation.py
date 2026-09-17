from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from math import isfinite

from fit_to_md.domain.activity.entities import ActivityRecord
from fit_to_md.domain.reporting.ports import ElevationCoordinate


@dataclass(frozen=True)
class _DistanceCoordinatePoint:
    distance_m: float
    latitude_deg: float
    longitude_deg: float


@dataclass(frozen=True)
class _DistanceElevationPoint:
    distance_m: float
    altitude_m: float


@dataclass(frozen=True)
class _DistanceElevationSample:
    distance_m: float
    altitude_m: float | None


class ElevationEnricher:
    """Build and apply a decoder-independent DEM elevation profile."""

    def __init__(self, sample_distance_m: float = 30.0) -> None:
        if not isfinite(sample_distance_m) or sample_distance_m <= 0:
            raise ValueError("sample_distance_m must be finite and positive")
        self._sample_distance_m = sample_distance_m

    def sample_coordinates(
        self, records: tuple[ActivityRecord, ...]
    ) -> tuple[ElevationCoordinate, ...]:
        sampled_points = self._sampled_route_points(records)
        if len(sampled_points) < 2:
            return tuple()
        return tuple(
            ElevationCoordinate(
                latitude_deg=point.latitude_deg,
                longitude_deg=point.longitude_deg,
            )
            for point in sampled_points
        )

    def enrich_records(
        self,
        records: tuple[ActivityRecord, ...],
        sampled_elevations: Sequence[float | None],
        elevation_mode: str,
    ) -> tuple[ActivityRecord, ...]:
        if elevation_mode not in {"dem", "hybrid"}:
            raise ValueError("elevation_mode must be one of: dem, hybrid")

        sampled_points = self._sampled_route_points(records)
        if len(sampled_points) < 2:
            return records

        elevation_samples = [
            _DistanceElevationSample(
                distance_m=point.distance_m,
                altitude_m=(
                    sampled_elevations[index]
                    if index < len(sampled_elevations)
                    else None
                ),
            )
            for index, point in enumerate(sampled_points)
        ]
        # Missing points remain boundaries so isolated successes never join
        # across a provider coverage gap.
        elevation_samples = [
            sample
            if (
                (index > 0 and elevation_samples[index - 1].altitude_m is not None)
                or (
                    index + 1 < len(elevation_samples)
                    and elevation_samples[index + 1].altitude_m is not None
                )
            )
            else replace(sample, altitude_m=None)
            for index, sample in enumerate(elevation_samples)
        ]
        if not any(sample.altitude_m is not None for sample in elevation_samples):
            return records
        if elevation_mode == "hybrid" and not _should_replace_fit_altitude(
            records, elevation_samples
        ):
            return records

        enriched_records: list[ActivityRecord] = []
        changed = False
        for record in records:
            enriched_record = record
            if record.distance_m is not None:
                dem_altitude_m = _interpolate_dem_altitude_at_distance(
                    elevation_samples, record.distance_m
                )
                if dem_altitude_m is not None:
                    enriched_record = replace(
                        record,
                        altitude_m=dem_altitude_m,
                        grade_percent=None,
                    )

            changed = changed or enriched_record != record
            enriched_records.append(enriched_record)

        return tuple(enriched_records) if changed else records

    def _sampled_route_points(
        self, records: tuple[ActivityRecord, ...]
    ) -> list[_DistanceCoordinatePoint]:
        coordinate_profile = _build_distance_coordinate_profile(records)
        if len(coordinate_profile) < 2:
            return []
        return _build_resampled_route_points(
            coordinate_profile,
            sample_distance_m=self._sample_distance_m,
        )


def _should_replace_fit_altitude(
    records: tuple[ActivityRecord, ...],
    dem_samples: list[_DistanceElevationSample],
) -> bool:
    fit_profile = _build_distance_altitude_profile(records)
    if len(fit_profile) < 5:
        return False

    aligned_segments: list[list[tuple[float, float]]] = []
    current_segment: list[tuple[float, float]] = []
    for sample in dem_samples:
        if sample.altitude_m is None:
            if current_segment:
                aligned_segments.append(current_segment)
                current_segment = []
            continue

        fit_altitude_m = _interpolate_altitude_at_distance(
            fit_profile, sample.distance_m
        )
        if fit_altitude_m is None:
            if current_segment:
                aligned_segments.append(current_segment)
                current_segment = []
            continue
        current_segment.append((fit_altitude_m, sample.altitude_m))

    if current_segment:
        aligned_segments.append(current_segment)

    aligned_pairs = [pair for segment in aligned_segments for pair in segment]
    if len(aligned_pairs) < 5:
        return False

    fit_total_variation_m = sum(
        _total_variation(value for value, _ in segment) for segment in aligned_segments
    )
    dem_total_variation_m = sum(
        _total_variation(value for _, value in segment) for segment in aligned_segments
    )
    mean_abs_difference_m = sum(abs(fit - dem) for fit, dem in aligned_pairs) / len(
        aligned_pairs
    )
    sign_flips = 0
    direction_changes = 0
    for segment in aligned_segments:
        segment_flips, segment_changes = _segment_sign_flip_counts(
            value for value, _ in segment
        )
        sign_flips += segment_flips
        direction_changes += segment_changes
    fit_sign_flip_ratio = sign_flips / direction_changes if direction_changes else 0.0

    return (
        fit_total_variation_m
        >= max(dem_total_variation_m * 2.0, dem_total_variation_m + 20.0)
        and mean_abs_difference_m >= 8.0
        and fit_sign_flip_ratio >= 0.3
    )


def _build_distance_altitude_profile(
    records: tuple[ActivityRecord, ...],
) -> list[_DistanceElevationPoint]:
    return [
        _DistanceElevationPoint(
            distance_m=float(record.distance_m),
            altitude_m=float(record.altitude_m),
        )
        for record in records
        if record.distance_m is not None and record.altitude_m is not None
    ]


def _total_variation(values: Iterable[float]) -> float:
    collected = [float(value) for value in values]
    if len(collected) < 2:
        return 0.0
    return sum(
        abs(current - previous)
        for previous, current in zip(collected, collected[1:], strict=False)
    )


def _segment_sign_flip_counts(values: Iterable[float]) -> tuple[int, int]:
    collected = [float(value) for value in values]
    if len(collected) < 3:
        return 0, 0

    directions: list[int] = []
    for previous, current in zip(collected, collected[1:], strict=False):
        delta = current - previous
        if abs(delta) >= 0.5:
            directions.append(1 if delta > 0 else -1)

    if len(directions) < 2:
        return 0, 0
    sign_flips = sum(
        direction != previous
        for previous, direction in zip(directions, directions[1:], strict=False)
    )
    return sign_flips, len(directions) - 1


def _build_distance_coordinate_profile(
    records: tuple[ActivityRecord, ...],
) -> list[_DistanceCoordinatePoint]:
    return [
        _DistanceCoordinatePoint(
            distance_m=float(record.distance_m),
            latitude_deg=float(record.latitude_deg),
            longitude_deg=float(record.longitude_deg),
        )
        for record in records
        if (
            record.distance_m is not None
            and record.latitude_deg is not None
            and record.longitude_deg is not None
        )
    ]


def _build_resampled_route_points(
    profile: list[_DistanceCoordinatePoint],
    sample_distance_m: float,
) -> list[_DistanceCoordinatePoint]:
    origin_distance_m = profile[0].distance_m
    final_distance_m = profile[-1].distance_m
    if final_distance_m <= origin_distance_m:
        return profile[:1]

    target_distances_m: list[float] = []
    current_distance_m = origin_distance_m
    while current_distance_m < final_distance_m:
        target_distances_m.append(current_distance_m)
        current_distance_m += sample_distance_m
    if not target_distances_m or target_distances_m[-1] != final_distance_m:
        target_distances_m.append(final_distance_m)

    return [
        point
        for target_distance_m in target_distances_m
        if (point := _interpolate_coordinate_at_distance(profile, target_distance_m))
        is not None
    ]


def _interpolate_coordinate_at_distance(
    profile: list[_DistanceCoordinatePoint],
    target_distance_m: float,
) -> _DistanceCoordinatePoint | None:
    if not profile or not (
        profile[0].distance_m <= target_distance_m <= profile[-1].distance_m
    ):
        return None
    if target_distance_m == profile[0].distance_m:
        return profile[0]

    previous_point = profile[0]
    for current_point in profile[1:]:
        if target_distance_m == current_point.distance_m:
            return current_point
        if previous_point.distance_m <= target_distance_m <= current_point.distance_m:
            delta_distance_m = current_point.distance_m - previous_point.distance_m
            if delta_distance_m == 0:
                return current_point
            ratio = (target_distance_m - previous_point.distance_m) / delta_distance_m
            return _DistanceCoordinatePoint(
                distance_m=target_distance_m,
                latitude_deg=previous_point.latitude_deg
                + ((current_point.latitude_deg - previous_point.latitude_deg) * ratio),
                longitude_deg=previous_point.longitude_deg
                + (
                    (current_point.longitude_deg - previous_point.longitude_deg) * ratio
                ),
            )
        previous_point = current_point
    return None


def _interpolate_altitude_at_distance(
    profile: list[_DistanceElevationPoint],
    target_distance_m: float,
) -> float | None:
    if not profile or not (
        profile[0].distance_m <= target_distance_m <= profile[-1].distance_m
    ):
        return None
    if target_distance_m == profile[0].distance_m:
        return profile[0].altitude_m

    previous_point = profile[0]
    for current_point in profile[1:]:
        if target_distance_m == current_point.distance_m:
            return current_point.altitude_m
        if previous_point.distance_m <= target_distance_m <= current_point.distance_m:
            delta_distance_m = current_point.distance_m - previous_point.distance_m
            if delta_distance_m == 0:
                return current_point.altitude_m
            ratio = (target_distance_m - previous_point.distance_m) / delta_distance_m
            return previous_point.altitude_m + (
                (current_point.altitude_m - previous_point.altitude_m) * ratio
            )
        previous_point = current_point
    return None


def _interpolate_dem_altitude_at_distance(
    samples: list[_DistanceElevationSample],
    target_distance_m: float,
) -> float | None:
    if not samples or not (
        samples[0].distance_m <= target_distance_m <= samples[-1].distance_m
    ):
        return None
    if target_distance_m == samples[0].distance_m:
        return samples[0].altitude_m

    previous_sample = samples[0]
    for current_sample in samples[1:]:
        if target_distance_m == current_sample.distance_m:
            return current_sample.altitude_m
        if previous_sample.distance_m <= target_distance_m <= current_sample.distance_m:
            if previous_sample.altitude_m is None or current_sample.altitude_m is None:
                return None
            interval_distance_m = current_sample.distance_m - previous_sample.distance_m
            if interval_distance_m == 0:
                return current_sample.altitude_m
            ratio = (
                target_distance_m - previous_sample.distance_m
            ) / interval_distance_m
            return previous_sample.altitude_m + (
                (current_sample.altitude_m - previous_sample.altitude_m) * ratio
            )
        previous_sample = current_sample
    return None
