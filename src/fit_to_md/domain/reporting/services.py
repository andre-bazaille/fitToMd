from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import mean

from fit_to_md.domain.activity.entities import Activity, ActivityLap, ActivityRecord
from fit_to_md.domain.reporting.entities import (
    SessionSummary,
    Split,
    TransitionDynamics,
    TransitionSample,
)

_ELEVATION_SMOOTHING_DISTANCE_M = 170.0
_MIN_ELEVATION_CHANGE_M = 0.4
_MIN_ELEVATION_SMOOTHING_WINDOW = 3
_MAX_ELEVATION_SMOOTHING_WINDOW = 51
_TRANSITION_GRADE_DISTANCE_M = 200.0


@dataclass(frozen=True)
class _BoundaryPoint:
    timestamp: datetime
    elapsed_time_s: float | None
    altitude_m: float | None


@dataclass(frozen=True)
class _DistanceAltitudePoint:
    distance_m: float
    altitude_m: float


class SessionSummaryBuilder:
    def __init__(
        self,
        elevation_smoothing_distance_m: float = _ELEVATION_SMOOTHING_DISTANCE_M,
        min_elevation_change_m: float = _MIN_ELEVATION_CHANGE_M,
    ) -> None:
        if elevation_smoothing_distance_m <= 0:
            raise ValueError("elevation_smoothing_distance_m must be positive")
        if min_elevation_change_m < 0:
            raise ValueError("min_elevation_change_m must be non-negative")

        self._elevation_smoothing_distance_m = elevation_smoothing_distance_m
        self._min_elevation_change_m = min_elevation_change_m

    def build(self, activity: Activity) -> SessionSummary:
        session = activity.session
        start_time = session.start_time or session.end_time
        if start_time is None and activity.records:
            start_time = activity.records[0].timestamp

        activity_type = _resolve_activity_type(activity)
        total_distance_m = session.total_distance_m
        if total_distance_m is None:
            total_distance_m = _derive_total_distance_from_records(activity.records)
        if total_distance_m is None:
            total_distance_m = _sum_optional(
                lap.total_distance_m for lap in activity.laps
            )

        total_timer_time_s = session.total_timer_time_s
        if total_timer_time_s is None:
            total_timer_time_s = _derive_timer_time_from_records(activity.records)
        if total_timer_time_s is None:
            total_timer_time_s = _sum_optional(
                lap.total_timer_time_s for lap in activity.laps
            )

        total_elapsed_time_s = session.total_elapsed_time_s or total_timer_time_s

        avg_heart_rate_bpm = session.avg_heart_rate_bpm
        if avg_heart_rate_bpm is None:
            avg_heart_rate_bpm = _average_int(
                record.heart_rate_bpm for record in activity.records
            )

        max_heart_rate_bpm = session.max_heart_rate_bpm
        if max_heart_rate_bpm is None:
            max_heart_rate_bpm = _max_int(
                record.heart_rate_bpm for record in activity.records
            )

        avg_cadence_spm = session.avg_cadence_spm
        if avg_cadence_spm is None:
            avg_cadence_spm = _average_int(
                record.cadence_spm for record in activity.records
            )

        avg_speed_kmh = _mps_to_kmh(session.avg_speed_mps)
        if avg_speed_kmh is None:
            avg_speed_kmh = _derive_avg_speed_kmh(total_distance_m, total_timer_time_s)

        avg_temperature_c = session.avg_temperature_c
        if avg_temperature_c is None:
            avg_temperature_c = _average_float(
                record.temperature_c for record in activity.records
            )
        if avg_temperature_c is None:
            avg_temperature_c = _average_float(
                lap.avg_temperature_c for lap in activity.laps
            )

        min_temperature_c = session.min_temperature_c
        if min_temperature_c is None:
            min_temperature_c = _min_float(
                record.temperature_c for record in activity.records
            )
        if min_temperature_c is None:
            min_temperature_c = _min_float(
                lap.min_temperature_c for lap in activity.laps
            )

        max_temperature_c = session.max_temperature_c
        if max_temperature_c is None:
            max_temperature_c = _max_float(
                record.temperature_c for record in activity.records
            )
        if max_temperature_c is None:
            max_temperature_c = _max_float(
                lap.max_temperature_c for lap in activity.laps
            )

        total_ascent_m, total_descent_m = _resolve_elevation_totals(
            activity,
            elevation_smoothing_distance_m=self._elevation_smoothing_distance_m,
            min_elevation_change_m=self._min_elevation_change_m,
        )

        return SessionSummary(
            start_time=start_time,
            activity_name=activity_type,
            activity_type=activity_type,
            total_distance_km=_meters_to_km(total_distance_m),
            total_timer_time_s=total_timer_time_s,
            total_elapsed_time_s=total_elapsed_time_s,
            total_ascent_m=total_ascent_m,
            total_descent_m=total_descent_m,
            avg_heart_rate_bpm=avg_heart_rate_bpm,
            max_heart_rate_bpm=max_heart_rate_bpm,
            avg_cadence_spm=avg_cadence_spm,
            avg_speed_kmh=avg_speed_kmh,
            avg_temperature_c=avg_temperature_c,
            min_temperature_c=min_temperature_c,
            max_temperature_c=max_temperature_c,
        )


class SplitBuilder:
    def build(
        self,
        activity: Activity,
        prefer_records: bool = False,
    ) -> tuple[Split, ...]:
        if prefer_records:
            record_splits = self._build_from_records(activity.records)
            if record_splits:
                return tuple(record_splits)

        kilometer_laps = _resolve_kilometer_laps(activity.laps, activity.records)
        if kilometer_laps:
            return tuple(self._build_from_laps(kilometer_laps))

        record_splits = self._build_from_records(activity.records)
        if record_splits:
            return tuple(record_splits)
        return tuple(self._build_from_laps(activity.laps))

    def _build_from_records(self, records: tuple[ActivityRecord, ...]) -> list[Split]:
        distance_records = [
            record for record in records if record.distance_m is not None
        ]
        if len(distance_records) < 2:
            return []

        origin_distance_m = distance_records[0].distance_m or 0.0
        final_distance_m = distance_records[-1].distance_m or origin_distance_m
        completed_kilometers = _count_completed_kilometers(
            origin_distance_m, final_distance_m
        )
        if completed_kilometers < 1:
            return []

        boundaries: list[_BoundaryPoint] = [
            _BoundaryPoint(
                timestamp=distance_records[0].timestamp,
                elapsed_time_s=distance_records[0].elapsed_time_s,
                altitude_m=distance_records[0].altitude_m,
            )
        ]
        splits: list[Split] = []

        for kilometer in range(1, completed_kilometers + 1):
            target_distance_m = origin_distance_m + (kilometer * 1000)
            crossing = _interpolate_record_at_distance(
                distance_records, target_distance_m
            )
            if crossing is None:
                break

            previous_boundary = boundaries[-1]
            window_records = _select_records_in_boundary_window(
                records=records,
                start_boundary=previous_boundary,
                end_boundary=crossing,
            )

            split_time_s = _resolve_boundary_duration_s(previous_boundary, crossing)
            elevation_delta_m = None
            if (
                previous_boundary.altitude_m is not None
                and crossing.altitude_m is not None
            ):
                elevation_delta_m = crossing.altitude_m - previous_boundary.altitude_m

            splits.append(
                Split(
                    kilometer=kilometer,
                    time_seconds=split_time_s,
                    pace_seconds_per_km=split_time_s,
                    elevation_delta_m=elevation_delta_m,
                    avg_heart_rate_bpm=_average_int(
                        record.heart_rate_bpm for record in window_records
                    ),
                    max_heart_rate_bpm=_max_int(
                        record.heart_rate_bpm for record in window_records
                    ),
                    avg_cadence_spm=_average_int(
                        record.cadence_spm for record in window_records
                    ),
                )
            )
            boundaries.append(crossing)

        return splits

    def _build_from_laps(self, laps: tuple[ActivityLap, ...]) -> list[Split]:
        splits: list[Split] = []
        for lap in laps:
            pace_seconds_per_km = None
            if lap.total_timer_time_s is not None and lap.total_distance_m not in (
                None,
                0,
            ):
                pace_seconds_per_km = lap.total_timer_time_s / (
                    lap.total_distance_m / 1000
                )

            elevation_delta_m = None
            if lap.total_ascent_m is not None or lap.total_descent_m is not None:
                elevation_delta_m = (lap.total_ascent_m or 0.0) - (
                    lap.total_descent_m or 0.0
                )

            splits.append(
                Split(
                    kilometer=lap.index,
                    time_seconds=lap.total_timer_time_s,
                    pace_seconds_per_km=pace_seconds_per_km,
                    elevation_delta_m=elevation_delta_m,
                    avg_heart_rate_bpm=lap.avg_heart_rate_bpm,
                    max_heart_rate_bpm=lap.max_heart_rate_bpm,
                    avg_cadence_spm=lap.avg_cadence_spm,
                )
            )
        return splits


class TransitionBuilder:
    def __init__(
        self,
        sample_interval_s: int = 30,
        elevation_smoothing_distance_m: float = _ELEVATION_SMOOTHING_DISTANCE_M,
        grade_distance_m: float = _TRANSITION_GRADE_DISTANCE_M,
    ) -> None:
        if sample_interval_s <= 0:
            raise ValueError("sample_interval_s must be positive")
        if elevation_smoothing_distance_m <= 0:
            raise ValueError("elevation_smoothing_distance_m must be positive")
        if grade_distance_m <= 0:
            raise ValueError("grade_distance_m must be positive")

        self._sample_interval_s = sample_interval_s
        self._elevation_smoothing_distance_m = elevation_smoothing_distance_m
        self._grade_distance_m = grade_distance_m

    def build(self, activity: Activity) -> tuple[TransitionDynamics, ...]:
        if not activity.records:
            return tuple()

        distance_records = [
            record for record in activity.records if record.distance_m is not None
        ]
        if len(distance_records) < 2:
            return tuple()

        origin_distance_m = distance_records[0].distance_m or 0.0
        final_distance_m = distance_records[-1].distance_m or origin_distance_m
        completed_kilometers = _count_completed_kilometers(
            origin_distance_m, final_distance_m
        )
        if completed_kilometers < 1:
            return tuple()

        kilometer_laps = _resolve_kilometer_laps(activity.laps, activity.records)
        kilometer_lap_durations = tuple(
            lap.total_timer_time_s
            for lap in kilometer_laps
            if lap.total_timer_time_s is not None
        )
        use_kilometer_lap_durations = (
            len(kilometer_lap_durations) == completed_kilometers
        )
        cumulative_elapsed_time_s = 0.0

        smoothed_altitude_profile = _build_smoothed_altitude_profile(
            activity.records,
            elevation_smoothing_distance_m=self._elevation_smoothing_distance_m,
        )
        boundaries: list[_BoundaryPoint] = [
            _BoundaryPoint(
                timestamp=distance_records[0].timestamp,
                elapsed_time_s=distance_records[0].elapsed_time_s,
                altitude_m=distance_records[0].altitude_m,
            )
        ]
        transitions: list[TransitionDynamics] = []
        for kilometer in range(1, completed_kilometers + 1):
            target_distance_m = origin_distance_m + (kilometer * 1000)
            end_boundary = _interpolate_record_at_distance(
                distance_records, target_distance_m
            )
            if end_boundary is None:
                continue

            start_boundary = boundaries[-1]
            if use_kilometer_lap_durations:
                duration_s = kilometer_lap_durations[kilometer - 1]
            else:
                duration_s = _resolve_boundary_duration_s(start_boundary, end_boundary)
            if duration_s <= 0:
                boundaries.append(end_boundary)
                continue

            samples: list[TransitionSample] = []
            for elapsed_seconds in _build_elapsed_samples(
                duration_s, self._sample_interval_s
            ):
                if use_kilometer_lap_durations:
                    record = _interpolate_record_at_elapsed_time(
                        activity.records,
                        cumulative_elapsed_time_s + elapsed_seconds,
                    )
                else:
                    record = _interpolate_record_within_boundary_window(
                        records=activity.records,
                        start_boundary=start_boundary,
                        elapsed_seconds=elapsed_seconds,
                    )
                if record is None:
                    continue

                grade_percent = record.grade_percent
                if grade_percent is None:
                    grade_percent = _estimate_grade_from_smoothed_altitude(
                        record,
                        smoothed_altitude_profile,
                        grade_distance_m=self._grade_distance_m,
                    )

                samples.append(
                    TransitionSample(
                        elapsed_seconds=elapsed_seconds,
                        heart_rate_bpm=record.heart_rate_bpm,
                        speed_kmh=_mps_to_kmh(record.speed_mps),
                        grade_percent=grade_percent,
                    )
                )

            if samples:
                transitions.append(
                    TransitionDynamics(
                        label=f"Km {kilometer}",
                        samples=tuple(samples),
                    )
                )
            boundaries.append(end_boundary)
            if use_kilometer_lap_durations:
                cumulative_elapsed_time_s += duration_s

        return tuple(transitions)


def _resolve_kilometer_laps(
    laps: tuple[ActivityLap, ...],
    records: tuple[ActivityRecord, ...],
) -> tuple[ActivityLap, ...]:
    if not laps:
        return tuple()

    kilometer_laps = tuple(lap for lap in laps if _is_kilometer_lap(lap))
    if not kilometer_laps:
        return tuple()

    distance_records = [record for record in records if record.distance_m is not None]
    if len(distance_records) < 2:
        return kilometer_laps

    origin_distance_m = distance_records[0].distance_m or 0.0
    final_distance_m = distance_records[-1].distance_m or origin_distance_m
    completed_kilometers = _count_completed_kilometers(
        origin_distance_m, final_distance_m
    )
    if completed_kilometers < 1 or len(kilometer_laps) < completed_kilometers:
        return tuple()

    return kilometer_laps[:completed_kilometers]


def _is_kilometer_lap(lap: ActivityLap) -> bool:
    if lap.total_timer_time_s is None or lap.total_distance_m is None:
        return False
    return abs(lap.total_distance_m - 1000.0) <= 25.0


def _count_completed_kilometers(
    origin_distance_m: float, final_distance_m: float
) -> int:
    return int(max(0.0, final_distance_m - origin_distance_m) // 1000)


def _resolve_activity_type(activity: Activity) -> str | None:
    raw_sub_sport = activity.sub_sport
    raw_sport = activity.sport

    if raw_sub_sport and raw_sub_sport.lower() not in {"generic", "none"}:
        return _prettify_label(raw_sub_sport)
    if raw_sport:
        return _prettify_label(raw_sport)
    return None


def _derive_total_distance_from_records(
    records: tuple[ActivityRecord, ...],
) -> float | None:
    valid_records = [
        record.distance_m for record in records if record.distance_m is not None
    ]
    if len(valid_records) < 2:
        return None
    return max(valid_records) - min(valid_records)


def _derive_timer_time_from_records(
    records: tuple[ActivityRecord, ...],
) -> float | None:
    elapsed_times = [
        record.elapsed_time_s for record in records if record.elapsed_time_s is not None
    ]
    if len(elapsed_times) >= 2:
        return max(elapsed_times) - min(elapsed_times)
    if len(records) < 2:
        return None
    return (records[-1].timestamp - records[0].timestamp).total_seconds()


def _derive_avg_speed_kmh(
    total_distance_m: float | None, total_timer_time_s: float | None
) -> float | None:
    if total_distance_m in (None, 0) or total_timer_time_s in (None, 0):
        return None
    return (total_distance_m / total_timer_time_s) * 3.6


def _resolve_elevation_totals(
    activity: Activity,
    elevation_smoothing_distance_m: float,
    min_elevation_change_m: float,
) -> tuple[float | None, float | None]:
    derived_totals = _derive_elevation_totals(
        activity.records,
        elevation_smoothing_distance_m=elevation_smoothing_distance_m,
        min_elevation_change_m=min_elevation_change_m,
    )
    if derived_totals is not None:
        return derived_totals

    total_ascent_m = activity.session.total_ascent_m
    if total_ascent_m is None:
        total_ascent_m = _sum_optional(lap.total_ascent_m for lap in activity.laps)

    total_descent_m = activity.session.total_descent_m
    if total_descent_m is None:
        total_descent_m = _sum_optional(lap.total_descent_m for lap in activity.laps)

    return total_ascent_m, total_descent_m


def _derive_elevation_totals(
    records: tuple[ActivityRecord, ...],
    elevation_smoothing_distance_m: float,
    min_elevation_change_m: float,
) -> tuple[float, float] | None:
    altitude_records = [record for record in records if record.altitude_m is not None]
    if len(altitude_records) < 2:
        return None

    smoothed_altitudes = _smooth_record_altitudes(
        altitude_records,
        elevation_smoothing_distance_m=elevation_smoothing_distance_m,
    )
    ascent_m = 0.0
    descent_m = 0.0
    previous_altitude = smoothed_altitudes[0]

    for altitude_m in smoothed_altitudes[1:]:
        delta_m = altitude_m - previous_altitude
        if delta_m >= min_elevation_change_m:
            ascent_m += delta_m
            previous_altitude = altitude_m
        elif delta_m <= -min_elevation_change_m:
            descent_m += -delta_m
            previous_altitude = altitude_m

    return ascent_m, descent_m


def _smooth_record_altitudes(
    records: list[ActivityRecord],
    elevation_smoothing_distance_m: float,
) -> list[float]:
    altitudes = [
        float(record.altitude_m) for record in records if record.altitude_m is not None
    ]
    window = _resolve_elevation_smoothing_window(
        records,
        elevation_smoothing_distance_m=elevation_smoothing_distance_m,
    )
    if window <= 1:
        return altitudes

    radius = window // 2
    smoothed_altitudes: list[float] = []
    for index in range(len(altitudes)):
        start_index = max(0, index - radius)
        end_index = min(len(altitudes), index + radius + 1)
        smoothed_altitudes.append(mean(altitudes[start_index:end_index]))
    return smoothed_altitudes


def _resolve_elevation_smoothing_window(
    records: list[ActivityRecord],
    elevation_smoothing_distance_m: float,
) -> int:
    max_window = len(records) if len(records) % 2 == 1 else len(records) - 1
    if max_window < _MIN_ELEVATION_SMOOTHING_WINDOW:
        return 1

    distances = [
        float(record.distance_m) for record in records if record.distance_m is not None
    ]
    average_spacing_m = 0.0
    if len(distances) >= 2:
        distance_span_m = max(distances) - min(distances)
        if distance_span_m > 0:
            average_spacing_m = distance_span_m / (len(distances) - 1)

    if average_spacing_m > 0:
        window = round(elevation_smoothing_distance_m / average_spacing_m)
    else:
        window = _MIN_ELEVATION_SMOOTHING_WINDOW

    window = max(_MIN_ELEVATION_SMOOTHING_WINDOW, window)
    if window % 2 == 0:
        window += 1
    return min(window, _MAX_ELEVATION_SMOOTHING_WINDOW, max_window)


def _build_smoothed_altitude_profile(
    records: tuple[ActivityRecord, ...],
    elevation_smoothing_distance_m: float,
) -> list[_DistanceAltitudePoint]:
    altitude_records = [
        record
        for record in records
        if record.distance_m is not None and record.altitude_m is not None
    ]
    if len(altitude_records) < 2:
        return []

    smoothed_altitudes = _smooth_record_altitudes(
        altitude_records,
        elevation_smoothing_distance_m=elevation_smoothing_distance_m,
    )
    profile: list[_DistanceAltitudePoint] = []
    for index, record in enumerate(altitude_records):
        distance_m = record.distance_m
        if distance_m is None:
            continue
        profile.append(
            _DistanceAltitudePoint(
                distance_m=distance_m,
                altitude_m=smoothed_altitudes[index],
            )
        )
    return profile


def _estimate_grade_from_smoothed_altitude(
    record: ActivityRecord,
    profile: list[_DistanceAltitudePoint],
    grade_distance_m: float,
) -> float | None:
    if not profile or record.distance_m is None:
        return None
    if record.speed_mps is None or record.speed_mps <= 0:
        return None

    half_distance = grade_distance_m / 2
    candidates = (
        (record.distance_m - half_distance, record.distance_m + half_distance),
        (record.distance_m - grade_distance_m, record.distance_m),
        (record.distance_m, record.distance_m + grade_distance_m),
    )
    for start_distance_m, end_distance_m in candidates:
        start_altitude_m = _interpolate_altitude_at_distance(profile, start_distance_m)
        end_altitude_m = _interpolate_altitude_at_distance(profile, end_distance_m)
        if start_altitude_m is None or end_altitude_m is None:
            continue

        delta_distance_m = end_distance_m - start_distance_m
        if delta_distance_m <= 0:
            continue
        return ((end_altitude_m - start_altitude_m) / delta_distance_m) * 100
    return None


def _interpolate_altitude_at_distance(
    profile: list[_DistanceAltitudePoint],
    target_distance_m: float,
) -> float | None:
    if not profile:
        return None
    if (
        target_distance_m < profile[0].distance_m
        or target_distance_m > profile[-1].distance_m
    ):
        return None
    if target_distance_m == profile[0].distance_m:
        return profile[0].altitude_m

    previous_point = profile[0]
    for current_point in profile[1:]:
        if target_distance_m == current_point.distance_m:
            return current_point.altitude_m
        if previous_point.distance_m <= target_distance_m <= current_point.distance_m:
            interval_distance_m = current_point.distance_m - previous_point.distance_m
            if interval_distance_m == 0:
                return current_point.altitude_m
            ratio = (
                target_distance_m - previous_point.distance_m
            ) / interval_distance_m
            return previous_point.altitude_m + (
                (current_point.altitude_m - previous_point.altitude_m) * ratio
            )
        previous_point = current_point

    return None


def _interpolate_record_at_distance(
    records: list[ActivityRecord], target_distance_m: float
) -> _BoundaryPoint | None:
    previous_record = records[0]
    for current_record in records[1:]:
        if previous_record.distance_m is None or current_record.distance_m is None:
            previous_record = current_record
            continue

        lower_distance = previous_record.distance_m
        upper_distance = current_record.distance_m
        if lower_distance == upper_distance:
            previous_record = current_record
            continue

        if lower_distance <= target_distance_m <= upper_distance:
            ratio = (target_distance_m - lower_distance) / (
                upper_distance - lower_distance
            )
            timestamp = (
                previous_record.timestamp
                + (current_record.timestamp - previous_record.timestamp) * ratio
            )
            altitude_m = _interpolate_float(
                previous_record.altitude_m, current_record.altitude_m, ratio
            )
            elapsed_time_s = _interpolate_float(
                previous_record.elapsed_time_s,
                current_record.elapsed_time_s,
                ratio,
            )
            return _BoundaryPoint(
                timestamp=timestamp,
                elapsed_time_s=elapsed_time_s,
                altitude_m=altitude_m,
            )

        previous_record = current_record

    return None


def _interpolate_record_at_time(
    records: tuple[ActivityRecord, ...], target_time: datetime
) -> ActivityRecord | None:
    if (
        not records
        or target_time < records[0].timestamp
        or target_time > records[-1].timestamp
    ):
        return None

    if target_time == records[0].timestamp:
        return records[0]

    previous_record = records[0]
    for current_record in records[1:]:
        if target_time == current_record.timestamp:
            return current_record
        if previous_record.timestamp <= target_time <= current_record.timestamp:
            interval_s = (
                current_record.timestamp - previous_record.timestamp
            ).total_seconds()
            if interval_s == 0:
                return current_record
            ratio = (
                target_time - previous_record.timestamp
            ).total_seconds() / interval_s
            return ActivityRecord(
                timestamp=target_time,
                elapsed_time_s=_interpolate_float(
                    previous_record.elapsed_time_s,
                    current_record.elapsed_time_s,
                    ratio,
                ),
                distance_m=_interpolate_float(
                    previous_record.distance_m, current_record.distance_m, ratio
                ),
                latitude_deg=_interpolate_float(
                    previous_record.latitude_deg, current_record.latitude_deg, ratio
                ),
                longitude_deg=_interpolate_float(
                    previous_record.longitude_deg, current_record.longitude_deg, ratio
                ),
                heart_rate_bpm=_interpolate_int(
                    previous_record.heart_rate_bpm, current_record.heart_rate_bpm, ratio
                ),
                cadence_spm=_interpolate_int(
                    previous_record.cadence_spm, current_record.cadence_spm, ratio
                ),
                fractional_cadence=_interpolate_float(
                    previous_record.fractional_cadence,
                    current_record.fractional_cadence,
                    ratio,
                ),
                speed_mps=_interpolate_float(
                    previous_record.speed_mps, current_record.speed_mps, ratio
                ),
                altitude_m=_interpolate_float(
                    previous_record.altitude_m, current_record.altitude_m, ratio
                ),
                grade_percent=_interpolate_float(
                    previous_record.grade_percent, current_record.grade_percent, ratio
                ),
                temperature_c=_interpolate_float(
                    previous_record.temperature_c, current_record.temperature_c, ratio
                ),
            )
        previous_record = current_record

    return None


def _interpolate_record_at_elapsed_time(
    records: tuple[ActivityRecord, ...],
    target_elapsed_time_s: float,
) -> ActivityRecord | None:
    elapsed_records = [
        record for record in records if record.elapsed_time_s is not None
    ]
    if not elapsed_records:
        return None
    first_elapsed_time_s = elapsed_records[0].elapsed_time_s
    last_elapsed_time_s = elapsed_records[-1].elapsed_time_s
    if first_elapsed_time_s is None or last_elapsed_time_s is None:
        return None
    if (
        target_elapsed_time_s < first_elapsed_time_s
        or target_elapsed_time_s > last_elapsed_time_s
    ):
        return None
    if target_elapsed_time_s == first_elapsed_time_s:
        return elapsed_records[0]

    previous_record = elapsed_records[0]
    for current_record in elapsed_records[1:]:
        current_elapsed_time_s = current_record.elapsed_time_s
        previous_elapsed_time_s = previous_record.elapsed_time_s
        if current_elapsed_time_s is None or previous_elapsed_time_s is None:
            previous_record = current_record
            continue

        if target_elapsed_time_s == current_elapsed_time_s:
            return current_record
        if previous_elapsed_time_s <= target_elapsed_time_s <= current_elapsed_time_s:
            interval_s = current_elapsed_time_s - previous_elapsed_time_s
            if interval_s == 0:
                previous_record = current_record
                continue
            ratio = (target_elapsed_time_s - previous_elapsed_time_s) / interval_s
            return ActivityRecord(
                timestamp=previous_record.timestamp
                + (current_record.timestamp - previous_record.timestamp) * ratio,
                elapsed_time_s=target_elapsed_time_s,
                distance_m=_interpolate_float(
                    previous_record.distance_m, current_record.distance_m, ratio
                ),
                latitude_deg=_interpolate_float(
                    previous_record.latitude_deg, current_record.latitude_deg, ratio
                ),
                longitude_deg=_interpolate_float(
                    previous_record.longitude_deg, current_record.longitude_deg, ratio
                ),
                heart_rate_bpm=_interpolate_int(
                    previous_record.heart_rate_bpm, current_record.heart_rate_bpm, ratio
                ),
                cadence_spm=_interpolate_int(
                    previous_record.cadence_spm, current_record.cadence_spm, ratio
                ),
                fractional_cadence=_interpolate_float(
                    previous_record.fractional_cadence,
                    current_record.fractional_cadence,
                    ratio,
                ),
                speed_mps=_interpolate_float(
                    previous_record.speed_mps, current_record.speed_mps, ratio
                ),
                altitude_m=_interpolate_float(
                    previous_record.altitude_m, current_record.altitude_m, ratio
                ),
                grade_percent=_interpolate_float(
                    previous_record.grade_percent, current_record.grade_percent, ratio
                ),
                temperature_c=_interpolate_float(
                    previous_record.temperature_c, current_record.temperature_c, ratio
                ),
            )
        previous_record = current_record

    return None


def _resolve_boundary_duration_s(
    start_boundary: _BoundaryPoint, end_boundary: _BoundaryPoint
) -> float:
    if (
        start_boundary.elapsed_time_s is not None
        and end_boundary.elapsed_time_s is not None
    ):
        return end_boundary.elapsed_time_s - start_boundary.elapsed_time_s
    return (end_boundary.timestamp - start_boundary.timestamp).total_seconds()


def _select_records_in_boundary_window(
    records: tuple[ActivityRecord, ...],
    start_boundary: _BoundaryPoint,
    end_boundary: _BoundaryPoint,
) -> list[ActivityRecord]:
    if (
        start_boundary.elapsed_time_s is not None
        and end_boundary.elapsed_time_s is not None
    ):
        return [
            record
            for record in records
            if record.elapsed_time_s is not None
            and start_boundary.elapsed_time_s
            < record.elapsed_time_s
            <= end_boundary.elapsed_time_s
        ]

    return [
        record
        for record in records
        if start_boundary.timestamp < record.timestamp <= end_boundary.timestamp
    ]


def _interpolate_record_within_boundary_window(
    records: tuple[ActivityRecord, ...],
    start_boundary: _BoundaryPoint,
    elapsed_seconds: float,
) -> ActivityRecord | None:
    if start_boundary.elapsed_time_s is not None:
        return _interpolate_record_at_elapsed_time(
            records,
            start_boundary.elapsed_time_s + elapsed_seconds,
        )

    target_time = start_boundary.timestamp + timedelta(seconds=elapsed_seconds)
    return _interpolate_record_at_time(records, target_time)


def _build_elapsed_samples(duration_s: float, sample_interval_s: int) -> list[float]:
    last_full_step = int(duration_s // sample_interval_s)
    samples = [float(step * sample_interval_s) for step in range(last_full_step + 1)]
    if not samples or samples[-1] != duration_s:
        samples.append(duration_s)
    return samples


def _meters_to_km(value: float | None) -> float | None:
    if value is None:
        return None
    return value / 1000


def _mps_to_kmh(value: float | None) -> float | None:
    if value is None:
        return None
    return value * 3.6


def _sum_optional(values: Iterable[int | float | None]) -> float | None:
    collected = [float(value) for value in values if value is not None]
    if not collected:
        return None
    return sum(collected)


def _average_int(values: Iterable[int | float | None]) -> int | None:
    collected = [int(value) for value in values if value is not None]
    if not collected:
        return None
    return round(mean(collected))


def _average_float(values: Iterable[int | float | None]) -> float | None:
    collected = [float(value) for value in values if value is not None]
    if not collected:
        return None
    return mean(collected)


def _max_int(values: Iterable[int | float | None]) -> int | None:
    collected = [int(value) for value in values if value is not None]
    if not collected:
        return None
    return max(collected)


def _min_float(values: Iterable[int | float | None]) -> float | None:
    collected = [float(value) for value in values if value is not None]
    if not collected:
        return None
    return min(collected)


def _max_float(values: Iterable[int | float | None]) -> float | None:
    collected = [float(value) for value in values if value is not None]
    if not collected:
        return None
    return max(collected)


def _prettify_label(value: str) -> str:
    return value.replace("_", " ").strip().title()


def _interpolate_float(
    start: float | None, end: float | None, ratio: float
) -> float | None:
    if start is None and end is None:
        return None
    if start is None:
        return end
    if end is None:
        return start
    return start + ((end - start) * ratio)


def _interpolate_int(start: int | None, end: int | None, ratio: float) -> int | None:
    interpolated = _interpolate_float(
        float(start) if start is not None else None,
        float(end) if end is not None else None,
        ratio,
    )
    if interpolated is None:
        return None
    return round(interpolated)
