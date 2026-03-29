from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import mean

from fit_to_md.domain.reporting.entities import SessionSummary, Split, TransitionDynamics, TransitionSample
from fit_to_md.infrastructure.fitdecode.models import ParsedActivityData, ParsedLap, ParsedRecord


@dataclass(frozen=True)
class _BoundaryPoint:
    timestamp: datetime
    altitude_m: float | None


class SessionSummaryBuilder:
    def build(self, activity: ParsedActivityData) -> SessionSummary:
        session = activity.session_data
        start_time = _coerce_datetime(session.get("start_time")) or _coerce_datetime(session.get("timestamp"))
        if start_time is None and activity.records:
            start_time = activity.records[0].timestamp

        activity_type = _resolve_activity_type(activity)
        total_distance_m = _first_float(session, "total_distance")
        if total_distance_m is None:
            total_distance_m = _derive_total_distance_from_records(activity.records)
        if total_distance_m is None:
            total_distance_m = _sum_optional(lap.total_distance_m for lap in activity.laps)

        total_timer_time_s = _first_float(session, "total_timer_time")
        if total_timer_time_s is None:
            total_timer_time_s = _derive_timer_time_from_records(activity.records)
        if total_timer_time_s is None:
            total_timer_time_s = _sum_optional(lap.total_timer_time_s for lap in activity.laps)

        total_elapsed_time_s = _first_float(session, "total_elapsed_time") or total_timer_time_s

        avg_heart_rate_bpm = _first_int(session, "avg_heart_rate")
        if avg_heart_rate_bpm is None:
            avg_heart_rate_bpm = _average_int(record.heart_rate_bpm for record in activity.records)

        max_heart_rate_bpm = _first_int(session, "max_heart_rate")
        if max_heart_rate_bpm is None:
            max_heart_rate_bpm = _max_int(record.heart_rate_bpm for record in activity.records)

        avg_cadence_spm = _resolve_session_avg_cadence(activity)
        if avg_cadence_spm is None:
            avg_cadence_spm = _average_int(record.cadence_spm for record in activity.records)

        avg_speed_kmh = _mps_to_kmh(_first_float(session, "enhanced_avg_speed", "avg_speed"))
        if avg_speed_kmh is None:
            avg_speed_kmh = _derive_avg_speed_kmh(total_distance_m, total_timer_time_s)

        avg_temperature_c = _first_float(session, "avg_temperature")
        if avg_temperature_c is None:
            avg_temperature_c = _average_float(record.temperature_c for record in activity.records)
        if avg_temperature_c is None:
            avg_temperature_c = _average_float(lap.avg_temperature_c for lap in activity.laps)

        min_temperature_c = _first_float(session, "min_temperature")
        if min_temperature_c is None:
            min_temperature_c = _min_float(record.temperature_c for record in activity.records)
        if min_temperature_c is None:
            min_temperature_c = _min_float(lap.min_temperature_c for lap in activity.laps)

        max_temperature_c = _first_float(session, "max_temperature")
        if max_temperature_c is None:
            max_temperature_c = _max_float(record.temperature_c for record in activity.records)
        if max_temperature_c is None:
            max_temperature_c = _max_float(lap.max_temperature_c for lap in activity.laps)

        total_ascent_m = _first_float(session, "total_ascent")
        if total_ascent_m is None:
            total_ascent_m = _sum_optional(lap.total_ascent_m for lap in activity.laps)

        total_descent_m = _first_float(session, "total_descent")
        if total_descent_m is None:
            total_descent_m = _sum_optional(lap.total_descent_m for lap in activity.laps)

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
    def build(self, activity: ParsedActivityData) -> tuple[Split, ...]:
        record_splits = self._build_from_records(activity.records)
        if record_splits:
            return tuple(record_splits)
        return tuple(self._build_from_laps(activity.laps))

    def _build_from_records(self, records: tuple[ParsedRecord, ...]) -> list[Split]:
        distance_records = [record for record in records if record.distance_m is not None]
        if len(distance_records) < 2:
            return []

        origin_distance_m = distance_records[0].distance_m or 0.0
        final_distance_m = distance_records[-1].distance_m or origin_distance_m
        completed_kilometers = int(max(0.0, final_distance_m - origin_distance_m) // 1000)
        if completed_kilometers < 1:
            return []

        boundaries: list[_BoundaryPoint] = [
            _BoundaryPoint(timestamp=distance_records[0].timestamp, altitude_m=distance_records[0].altitude_m)
        ]
        splits: list[Split] = []

        for kilometer in range(1, completed_kilometers + 1):
            target_distance_m = origin_distance_m + (kilometer * 1000)
            crossing = _interpolate_record_at_distance(distance_records, target_distance_m)
            if crossing is None:
                break

            previous_boundary = boundaries[-1]
            window_records = [
                record
                for record in records
                if previous_boundary.timestamp < record.timestamp <= crossing.timestamp
            ]

            split_time_s = (crossing.timestamp - previous_boundary.timestamp).total_seconds()
            elevation_delta_m = None
            if previous_boundary.altitude_m is not None and crossing.altitude_m is not None:
                elevation_delta_m = crossing.altitude_m - previous_boundary.altitude_m

            splits.append(
                Split(
                    kilometer=kilometer,
                    time_seconds=split_time_s,
                    pace_seconds_per_km=split_time_s,
                    elevation_delta_m=elevation_delta_m,
                    avg_heart_rate_bpm=_average_int(record.heart_rate_bpm for record in window_records),
                    max_heart_rate_bpm=_max_int(record.heart_rate_bpm for record in window_records),
                    avg_cadence_spm=_average_int(record.cadence_spm for record in window_records),
                )
            )
            boundaries.append(crossing)

        return splits

    def _build_from_laps(self, laps: tuple[ParsedLap, ...]) -> list[Split]:
        splits: list[Split] = []
        for lap in laps:
            pace_seconds_per_km = None
            if lap.total_timer_time_s is not None and lap.total_distance_m not in (None, 0):
                pace_seconds_per_km = lap.total_timer_time_s / (lap.total_distance_m / 1000)

            elevation_delta_m = None
            if lap.total_ascent_m is not None or lap.total_descent_m is not None:
                elevation_delta_m = (lap.total_ascent_m or 0.0) - (lap.total_descent_m or 0.0)

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
    def __init__(self, sample_interval_s: int = 10, window_s: int = 60) -> None:
        self._sample_interval_s = sample_interval_s
        self._window_s = window_s

    def build(self, activity: ParsedActivityData) -> tuple[TransitionDynamics, ...]:
        if len(activity.laps) < 2 or not activity.records:
            return tuple()

        transitions: list[TransitionDynamics] = []
        for previous_lap, next_lap in zip(activity.laps, activity.laps[1:]):
            boundary = next_lap.start_time or previous_lap.end_time
            if boundary is None:
                continue

            samples: list[TransitionSample] = []
            for offset_seconds in range(-self._window_s, self._window_s + 1, self._sample_interval_s):
                target_time = boundary + timedelta(seconds=offset_seconds)
                record = _interpolate_record_at_time(activity.records, target_time)
                if record is None:
                    continue

                samples.append(
                    TransitionSample(
                        offset_seconds=offset_seconds,
                        heart_rate_bpm=record.heart_rate_bpm,
                        speed_kmh=_mps_to_kmh(record.speed_mps),
                        grade_percent=record.grade_percent,
                    )
                )

            if samples:
                transitions.append(
                    TransitionDynamics(
                        label=f"End of Lap {previous_lap.index} to Start of Lap {next_lap.index}",
                        samples=tuple(samples),
                    )
                )

        return tuple(transitions)


def _resolve_activity_type(activity: ParsedActivityData) -> str | None:
    raw_sub_sport = activity.sub_sport or _coerce_text(activity.session_data.get("sub_sport"))
    raw_sport = activity.sport or _coerce_text(activity.session_data.get("sport"))

    if raw_sub_sport and raw_sub_sport.lower() not in {"generic", "none"}:
        return _prettify_label(raw_sub_sport)
    if raw_sport:
        return _prettify_label(raw_sport)
    return None


def _derive_total_distance_from_records(records: tuple[ParsedRecord, ...]) -> float | None:
    valid_records = [record.distance_m for record in records if record.distance_m is not None]
    if len(valid_records) < 2:
        return None
    return max(valid_records) - min(valid_records)


def _derive_timer_time_from_records(records: tuple[ParsedRecord, ...]) -> float | None:
    if len(records) < 2:
        return None
    return (records[-1].timestamp - records[0].timestamp).total_seconds()


def _derive_avg_speed_kmh(total_distance_m: float | None, total_timer_time_s: float | None) -> float | None:
    if total_distance_m in (None, 0) or total_timer_time_s in (None, 0):
        return None
    return (total_distance_m / total_timer_time_s) * 3.6


def _resolve_session_avg_cadence(activity: ParsedActivityData) -> int | None:
    session = activity.session_data
    running_cadence = _first_int(session, "avg_running_cadence")
    if _is_running_activity(activity) and running_cadence is not None:
        return round((running_cadence + (_first_float(session, "avg_fractional_cadence") or 0.0)) * 2)
    return _first_int(session, "avg_cadence")


def _interpolate_record_at_distance(records: list[ParsedRecord], target_distance_m: float) -> _BoundaryPoint | None:
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
            ratio = (target_distance_m - lower_distance) / (upper_distance - lower_distance)
            timestamp = previous_record.timestamp + (current_record.timestamp - previous_record.timestamp) * ratio
            altitude_m = _interpolate_float(previous_record.altitude_m, current_record.altitude_m, ratio)
            return _BoundaryPoint(timestamp=timestamp, altitude_m=altitude_m)

        previous_record = current_record

    return None


def _interpolate_record_at_time(records: tuple[ParsedRecord, ...], target_time: datetime) -> ParsedRecord | None:
    if not records or target_time < records[0].timestamp or target_time > records[-1].timestamp:
        return None

    if target_time == records[0].timestamp:
        return records[0]

    previous_record = records[0]
    for current_record in records[1:]:
        if target_time == current_record.timestamp:
            return current_record
        if previous_record.timestamp <= target_time <= current_record.timestamp:
            interval_s = (current_record.timestamp - previous_record.timestamp).total_seconds()
            if interval_s == 0:
                return current_record
            ratio = (target_time - previous_record.timestamp).total_seconds() / interval_s
            return ParsedRecord(
                timestamp=target_time,
                distance_m=_interpolate_float(previous_record.distance_m, current_record.distance_m, ratio),
                heart_rate_bpm=_interpolate_int(previous_record.heart_rate_bpm, current_record.heart_rate_bpm, ratio),
                cadence_spm=_interpolate_int(previous_record.cadence_spm, current_record.cadence_spm, ratio),
                fractional_cadence=_interpolate_float(
                    previous_record.fractional_cadence,
                    current_record.fractional_cadence,
                    ratio,
                ),
                speed_mps=_interpolate_float(previous_record.speed_mps, current_record.speed_mps, ratio),
                altitude_m=_interpolate_float(previous_record.altitude_m, current_record.altitude_m, ratio),
                grade_percent=_interpolate_float(previous_record.grade_percent, current_record.grade_percent, ratio),
                temperature_c=_interpolate_float(previous_record.temperature_c, current_record.temperature_c, ratio),
            )
        previous_record = current_record

    return None


def _meters_to_km(value: float | None) -> float | None:
    if value is None:
        return None
    return value / 1000


def _mps_to_kmh(value: float | None) -> float | None:
    if value is None:
        return None
    return value * 3.6


def _sum_optional(values: object) -> float | None:
    collected = [float(value) for value in values if value is not None]
    if not collected:
        return None
    return sum(collected)


def _average_int(values: object) -> int | None:
    collected = [int(value) for value in values if value is not None]
    if not collected:
        return None
    return round(mean(collected))


def _average_float(values: object) -> float | None:
    collected = [float(value) for value in values if value is not None]
    if not collected:
        return None
    return mean(collected)


def _max_int(values: object) -> int | None:
    collected = [int(value) for value in values if value is not None]
    if not collected:
        return None
    return max(collected)


def _min_float(values: object) -> float | None:
    collected = [float(value) for value in values if value is not None]
    if not collected:
        return None
    return min(collected)


def _max_float(values: object) -> float | None:
    collected = [float(value) for value in values if value is not None]
    if not collected:
        return None
    return max(collected)


def _first_float(values: dict[str, object], *keys: str) -> float | None:
    for key in keys:
        value = _coerce_float(values.get(key))
        if value is not None:
            return value
    return None


def _first_int(values: dict[str, object], *keys: str) -> int | None:
    for key in keys:
        value = _coerce_int(values.get(key))
        if value is not None:
            return value
    return None


def _coerce_text(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _is_running_activity(activity: ParsedActivityData) -> bool:
    raw_sport = activity.sport or _coerce_text(activity.session_data.get("sport"))
    return (raw_sport or "").lower() == "running"


def _coerce_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    return None


def _coerce_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _coerce_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value)
    return None


def _prettify_label(value: str) -> str:
    return value.replace("_", " ").strip().title()


def _interpolate_float(start: float | None, end: float | None, ratio: float) -> float | None:
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
