from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import fitdecode

from fit_to_md.domain.reporting.entities import FitReport, SessionSummary
from fit_to_md.domain.reporting.ports import ElevationCoordinate, ElevationProvider, HistoricalWeatherProvider
from fit_to_md.infrastructure.fitdecode.builders import SessionSummaryBuilder, SplitBuilder, TransitionBuilder
from fit_to_md.infrastructure.fitdecode.models import ParsedActivityData, ParsedLap, ParsedRecord


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
class _TimerEvent:
    timestamp: datetime
    event_type: str


@dataclass(frozen=True)
class _TimerInterval:
    start_time: datetime
    end_time: datetime


_TIMER_START_EVENT_TYPES = {"start"}
_TIMER_STOP_EVENT_TYPES = {"stop", "stop_all", "stop_disable", "stop_disable_all"}


class FitdecodeActivityExtractor:
    def __init__(
        self,
        reader_factory: Callable[[str], Any] | None = None,
        summary_builder: SessionSummaryBuilder | None = None,
        split_builder: SplitBuilder | None = None,
        transition_builder: TransitionBuilder | None = None,
        weather_provider: HistoricalWeatherProvider | None = None,
        elevation_provider: ElevationProvider | None = None,
        elevation_mode: str = "fit",
        elevation_sample_distance_m: float = 30.0,
    ) -> None:
        if elevation_mode not in {"fit", "dem", "hybrid"}:
            raise ValueError("elevation_mode must be one of: fit, dem, hybrid")
        if elevation_sample_distance_m <= 0:
            raise ValueError("elevation_sample_distance_m must be positive")

        self._reader_factory = reader_factory or fitdecode.FitReader
        self._summary_builder = summary_builder or SessionSummaryBuilder()
        self._split_builder = split_builder or SplitBuilder()
        self._transition_builder = transition_builder or TransitionBuilder()
        self._weather_provider = weather_provider
        self._elevation_provider = elevation_provider
        self._elevation_mode = elevation_mode
        self._elevation_sample_distance_m = elevation_sample_distance_m

    def extract(self, source: Path) -> FitReport:
        activity = self._parse_activity(source)
        activity = self._enrich_activity_elevation(activity)
        summary = self._summary_builder.build(activity)
        summary = self._enrich_summary_weather(summary, activity)
        return FitReport(
            summary=summary,
            splits=self._split_builder.build(activity),
            transitions=self._transition_builder.build(activity),
        )

    def _enrich_activity_elevation(self, activity: ParsedActivityData) -> ParsedActivityData:
        if self._elevation_mode == "fit" or self._elevation_provider is None:
            return activity

        enriched_records = _replace_record_altitudes_from_dem(
            activity.records,
            elevation_provider=self._elevation_provider,
            elevation_mode=self._elevation_mode,
            sample_distance_m=self._elevation_sample_distance_m,
        )
        if enriched_records == activity.records:
            return activity
        return replace(activity, records=enriched_records)

    def _enrich_summary_weather(self, summary: SessionSummary, activity: ParsedActivityData) -> SessionSummary:
        if self._weather_provider is None or summary.weather is not None:
            return summary

        start_time = summary.start_time
        if start_time is None:
            return summary

        latitude_deg = _semicircles_to_degrees(activity.session_data.get("start_position_lat"))
        longitude_deg = _semicircles_to_degrees(activity.session_data.get("start_position_long"))
        if latitude_deg is None or longitude_deg is None:
            return summary

        end_time = _coerce_datetime(activity.session_data.get("timestamp"))
        if end_time is None and summary.total_elapsed_time_s is not None:
            end_time = start_time + timedelta(seconds=summary.total_elapsed_time_s)

        weather = self._weather_provider.lookup(
            start_time=start_time,
            end_time=end_time,
            latitude_deg=latitude_deg,
            longitude_deg=longitude_deg,
        )
        if weather is None:
            return summary

        return replace(summary, weather=weather)

    def _parse_activity(self, source: Path) -> ParsedActivityData:
        session_data: dict[str, object] = {}
        device_infos: list[dict[str, object]] = []
        laps: list[ParsedLap] = []
        records: list[ParsedRecord] = []
        timer_events: list[_TimerEvent] = []
        sport: str | None = None
        sub_sport: str | None = None

        with self._reader_factory(str(source)) as fit_file:
            for frame in fit_file:
                if getattr(frame, "frame_type", None) != fitdecode.FIT_FRAME_DATA:
                    continue

                if frame.name == "session":
                    session_data = _extract_message_values(frame)
                elif frame.name == "device_info":
                    device_infos.append(_extract_message_values(frame))
                elif frame.name == "sport":
                    values = _extract_message_values(frame)
                    sport = _coerce_text(values.get("sport")) or sport
                    sub_sport = _coerce_text(values.get("sub_sport")) or sub_sport
                elif frame.name == "lap":
                    laps.append(_parse_lap(frame, len(laps) + 1))
                elif frame.name == "record":
                    record = _parse_record(frame)
                    if record is not None:
                        records.append(record)
                elif frame.name == "event":
                    timer_event = _parse_timer_event(frame)
                    if timer_event is not None:
                        timer_events.append(timer_event)

        normalized_records = _normalize_running_record_cadence(
            records,
                sport=sport,
                session_data=session_data,
            )
        records_with_elapsed = _assign_elapsed_time_to_records(
            records=normalized_records,
            timer_events=tuple(timer_events),
        )
        return ParsedActivityData(
            session_data=session_data,
            device_infos=tuple(device_infos),
            sport=sport,
            sub_sport=sub_sport,
            laps=tuple(laps),
            records=records_with_elapsed,
        )


def _parse_lap(frame: Any, index: int) -> ParsedLap:
    values = _extract_message_values(frame)
    return ParsedLap(
        index=index,
        start_time=_coerce_datetime(values.get("start_time")),
        end_time=_coerce_datetime(values.get("timestamp")),
        total_distance_m=_coerce_float(values.get("total_distance")),
        total_timer_time_s=_coerce_float(values.get("total_timer_time")),
        total_ascent_m=_coerce_float(values.get("total_ascent")),
        total_descent_m=_coerce_float(values.get("total_descent")),
        avg_heart_rate_bpm=_coerce_int(values.get("avg_heart_rate")),
        max_heart_rate_bpm=_coerce_int(values.get("max_heart_rate")),
        avg_cadence_spm=_resolve_lap_cadence(values),
        avg_temperature_c=_coerce_float(values.get("avg_temperature")),
        min_temperature_c=_coerce_float(values.get("min_temperature")),
        max_temperature_c=_coerce_float(values.get("max_temperature")),
    )


def _parse_record(frame: Any) -> ParsedRecord | None:
    values = _extract_message_values(frame)
    timestamp = _coerce_datetime(values.get("timestamp"))
    if timestamp is None:
        return None

    record = ParsedRecord(
        timestamp=timestamp,
        elapsed_time_s=None,
        distance_m=_coerce_float(values.get("distance")),
        latitude_deg=_semicircles_to_degrees(values.get("position_lat")),
        longitude_deg=_semicircles_to_degrees(values.get("position_long")),
        heart_rate_bpm=_coerce_int(values.get("heart_rate")),
        cadence_spm=_coerce_int(values.get("cadence")),
        fractional_cadence=_coerce_float(values.get("fractional_cadence")),
        speed_mps=_coalesce_float(values.get("enhanced_speed"), values.get("speed")),
        altitude_m=_coalesce_float(values.get("enhanced_altitude"), values.get("altitude")),
        grade_percent=_coerce_float(values.get("grade")),
        temperature_c=_coerce_float(values.get("temperature")),
    )
    if all(
        value is None
        for value in (
            record.distance_m,
            record.heart_rate_bpm,
            record.cadence_spm,
            record.speed_mps,
            record.altitude_m,
            record.grade_percent,
            record.temperature_c,
        )
    ):
        return None
    return record


def _parse_timer_event(frame: Any) -> _TimerEvent | None:
    values = _extract_message_values(frame)
    if _coerce_text(values.get("event")) != "timer":
        return None

    timestamp = _coerce_datetime(values.get("timestamp"))
    event_type = _coerce_text(values.get("event_type"))
    if timestamp is None or event_type is None:
        return None

    return _TimerEvent(timestamp=timestamp, event_type=event_type)


def _extract_message_values(frame: Any) -> dict[str, object]:
    values: dict[str, object] = {}
    for field in frame.fields:
        key = field.name
        value = field.value
        if key in values:
            existing = values[key]
            if isinstance(existing, tuple):
                values[key] = (*existing, value)
            else:
                values[key] = (existing, value)
        else:
            values[key] = value
    return values


def _normalize_running_record_cadence(
    records: list[ParsedRecord],
    sport: str | None,
    session_data: dict[str, object],
) -> list[ParsedRecord]:
    if not _is_running_activity(sport, session_data):
        return records

    normalized_records: list[ParsedRecord] = []
    for record in records:
        normalized_records.append(
            ParsedRecord(
                timestamp=record.timestamp,
                elapsed_time_s=record.elapsed_time_s,
                distance_m=record.distance_m,
                latitude_deg=record.latitude_deg,
                longitude_deg=record.longitude_deg,
                heart_rate_bpm=record.heart_rate_bpm,
                cadence_spm=_normalize_running_cadence(record.cadence_spm, record.fractional_cadence),
                fractional_cadence=record.fractional_cadence,
                speed_mps=record.speed_mps,
                altitude_m=record.altitude_m,
                grade_percent=record.grade_percent,
                temperature_c=record.temperature_c,
            )
        )
    return normalized_records


def _assign_elapsed_time_to_records(
    records: list[ParsedRecord],
    timer_events: tuple[_TimerEvent, ...],
) -> tuple[ParsedRecord, ...]:
    if not records:
        return tuple()

    origin_time = records[0].timestamp
    intervals = _build_timer_intervals(
        timer_events=timer_events,
        fallback_start_time=origin_time,
        fallback_end_time=records[-1].timestamp,
    )
    if not intervals:
        return tuple(
            replace(record, elapsed_time_s=(record.timestamp - origin_time).total_seconds())
            for record in records
        )

    return tuple(
        replace(record, elapsed_time_s=_elapsed_time_at_timestamp(record.timestamp, intervals))
        for record in records
    )


def _build_timer_intervals(
    timer_events: tuple[_TimerEvent, ...],
    fallback_start_time: datetime,
    fallback_end_time: datetime,
) -> tuple[_TimerInterval, ...]:
    if not timer_events:
        return tuple()

    intervals: list[_TimerInterval] = []
    current_start: datetime | None = None
    saw_timer_state = False
    for timer_event in sorted(timer_events, key=lambda event: event.timestamp):
        if timer_event.event_type in _TIMER_START_EVENT_TYPES:
            saw_timer_state = True
            if current_start is None:
                current_start = max(timer_event.timestamp, fallback_start_time)
        elif timer_event.event_type in _TIMER_STOP_EVENT_TYPES:
            saw_timer_state = True
            stop_time = min(timer_event.timestamp, fallback_end_time)
            if current_start is None:
                if not intervals and stop_time > fallback_start_time:
                    intervals.append(
                        _TimerInterval(
                            start_time=fallback_start_time,
                            end_time=stop_time,
                        )
                    )
                continue

            if stop_time >= current_start:
                intervals.append(
                    _TimerInterval(
                        start_time=current_start,
                        end_time=stop_time,
                    )
                )
            current_start = None

    if not saw_timer_state:
        return tuple()

    if current_start is not None and fallback_end_time >= current_start:
        intervals.append(
            _TimerInterval(
                start_time=current_start,
                end_time=fallback_end_time,
            )
        )

    return tuple(intervals)


def _elapsed_time_at_timestamp(
    timestamp: datetime,
    intervals: tuple[_TimerInterval, ...],
) -> float:
    elapsed_time_s = 0.0
    for interval in intervals:
        interval_duration_s = (interval.end_time - interval.start_time).total_seconds()
        if timestamp >= interval.end_time:
            elapsed_time_s += interval_duration_s
            continue
        if timestamp <= interval.start_time:
            break

        elapsed_time_s += (timestamp - interval.start_time).total_seconds()
        break

    return elapsed_time_s
def _resolve_lap_cadence(values: dict[str, object]) -> int | None:
    running_cadence = _coerce_int(values.get("avg_running_cadence"))
    if running_cadence is not None:
        return _normalize_running_cadence(running_cadence, _coerce_float(values.get("avg_fractional_cadence")))
    return _coerce_int(values.get("avg_cadence"))


def _normalize_running_cadence(cadence: int | None, fractional_cadence: float | None) -> int | None:
    if cadence is None:
        return None
    return round((cadence + (fractional_cadence or 0.0)) * 2)


def _is_running_activity(sport: str | None, session_data: dict[str, object]) -> bool:
    raw_sport = _coerce_text(session_data.get("sport")) or sport
    return (raw_sport or "").lower() == "running"


def _coerce_text(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _coerce_datetime(value: object):
    return value if hasattr(value, "isoformat") else None


def _coerce_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _coalesce_float(*values: object) -> float | None:
    for value in values:
        coerced = _coerce_float(value)
        if coerced is not None:
            return coerced
    return None


def _coerce_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value)
    return None


def _semicircles_to_degrees(value: object) -> float | None:
    semicircles = _coerce_float(value)
    if semicircles is None:
        return None
    return (semicircles * 180.0) / (2**31)


def _replace_record_altitudes_from_dem(
    records: tuple[ParsedRecord, ...],
    elevation_provider: ElevationProvider,
    elevation_mode: str,
    sample_distance_m: float,
) -> tuple[ParsedRecord, ...]:
    coordinate_profile = _build_distance_coordinate_profile(records)
    if len(coordinate_profile) < 2:
        return records

    sampled_points = _build_resampled_route_points(
        coordinate_profile,
        sample_distance_m=sample_distance_m,
    )
    if len(sampled_points) < 2:
        return records

    sampled_elevations = elevation_provider.lookup(
        tuple(
            ElevationCoordinate(
                latitude_deg=point.latitude_deg,
                longitude_deg=point.longitude_deg,
            )
            for point in sampled_points
        )
    )
    elevation_profile = [
        _DistanceElevationPoint(distance_m=point.distance_m, altitude_m=elevation_m)
        for point, elevation_m in zip(sampled_points, sampled_elevations)
        if elevation_m is not None
    ]
    if len(elevation_profile) < 2:
        return records
    if elevation_mode == "hybrid" and not _should_replace_fit_altitude(records, elevation_profile):
        return records

    enriched_records: list[ParsedRecord] = []
    changed = False
    for record in records:
        altitude_m = record.altitude_m
        if record.distance_m is not None:
            dem_altitude_m = _interpolate_altitude_at_distance(elevation_profile, record.distance_m)
            if dem_altitude_m is not None:
                altitude_m = dem_altitude_m

        grade_percent = None
        if altitude_m != record.altitude_m or record.grade_percent is not None:
            changed = True

        enriched_records.append(
            ParsedRecord(
                timestamp=record.timestamp,
                elapsed_time_s=record.elapsed_time_s,
                distance_m=record.distance_m,
                latitude_deg=record.latitude_deg,
                longitude_deg=record.longitude_deg,
                heart_rate_bpm=record.heart_rate_bpm,
                cadence_spm=record.cadence_spm,
                fractional_cadence=record.fractional_cadence,
                speed_mps=record.speed_mps,
                altitude_m=altitude_m,
                grade_percent=grade_percent,
                temperature_c=record.temperature_c,
            )
        )

    if not changed:
        return records
    return tuple(enriched_records)


def _should_replace_fit_altitude(
    records: tuple[ParsedRecord, ...],
    dem_profile: list[_DistanceElevationPoint],
) -> bool:
    fit_profile = _build_distance_altitude_profile(records)
    if len(fit_profile) < 5:
        return False

    fit_aligned_altitudes = [
        _interpolate_altitude_at_distance(fit_profile, point.distance_m)
        for point in dem_profile
    ]
    aligned_pairs = [
        (fit_altitude_m, dem_point.altitude_m)
        for fit_altitude_m, dem_point in zip(fit_aligned_altitudes, dem_profile)
        if fit_altitude_m is not None
    ]
    if len(aligned_pairs) < 5:
        return False

    fit_total_variation_m = _total_variation(value for value, _ in aligned_pairs)
    dem_total_variation_m = _total_variation(value for _, value in aligned_pairs)
    mean_abs_difference_m = sum(abs(fit - dem) for fit, dem in aligned_pairs) / len(aligned_pairs)
    fit_sign_flip_ratio = _segment_sign_flip_ratio(value for value, _ in aligned_pairs)

    return (
        fit_total_variation_m >= max(dem_total_variation_m * 2.0, dem_total_variation_m + 20.0)
        and mean_abs_difference_m >= 8.0
        and fit_sign_flip_ratio >= 0.3
    )


def _build_distance_altitude_profile(records: tuple[ParsedRecord, ...]) -> list[_DistanceElevationPoint]:
    profile: list[_DistanceElevationPoint] = []
    for record in records:
        if record.distance_m is None or record.altitude_m is None:
            continue
        profile.append(
            _DistanceElevationPoint(
                distance_m=float(record.distance_m),
                altitude_m=float(record.altitude_m),
            )
        )
    return profile


def _total_variation(values: Any) -> float:
    collected = [float(value) for value in values]
    if len(collected) < 2:
        return 0.0

    total = 0.0
    previous = collected[0]
    for current in collected[1:]:
        total += abs(current - previous)
        previous = current
    return total


def _segment_sign_flip_ratio(values: Any) -> float:
    collected = [float(value) for value in values]
    if len(collected) < 3:
        return 0.0

    directions: list[int] = []
    previous = collected[0]
    for current in collected[1:]:
        delta = current - previous
        previous = current
        if abs(delta) < 0.5:
            continue
        directions.append(1 if delta > 0 else -1)

    if len(directions) < 2:
        return 0.0

    sign_flips = 0
    previous_direction = directions[0]
    for direction in directions[1:]:
        if direction != previous_direction:
            sign_flips += 1
        previous_direction = direction
    return sign_flips / (len(directions) - 1)


def _build_distance_coordinate_profile(records: tuple[ParsedRecord, ...]) -> list[_DistanceCoordinatePoint]:
    profile: list[_DistanceCoordinatePoint] = []
    for record in records:
        if record.distance_m is None or record.latitude_deg is None or record.longitude_deg is None:
            continue
        profile.append(
            _DistanceCoordinatePoint(
                distance_m=float(record.distance_m),
                latitude_deg=float(record.latitude_deg),
                longitude_deg=float(record.longitude_deg),
            )
        )
    return profile


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

    sampled_points: list[_DistanceCoordinatePoint] = []
    for target_distance_m in target_distances_m:
        point = _interpolate_coordinate_at_distance(profile, target_distance_m)
        if point is not None:
            sampled_points.append(point)
    return sampled_points


def _interpolate_coordinate_at_distance(
    profile: list[_DistanceCoordinatePoint],
    target_distance_m: float,
) -> _DistanceCoordinatePoint | None:
    if not profile:
        return None
    if target_distance_m < profile[0].distance_m or target_distance_m > profile[-1].distance_m:
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
                + ((current_point.longitude_deg - previous_point.longitude_deg) * ratio),
            )
        previous_point = current_point
    return None


def _interpolate_altitude_at_distance(
    profile: list[_DistanceElevationPoint],
    target_distance_m: float,
) -> float | None:
    if not profile:
        return None
    if target_distance_m < profile[0].distance_m or target_distance_m > profile[-1].distance_m:
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
            return previous_point.altitude_m + ((current_point.altitude_m - previous_point.altitude_m) * ratio)
        previous_point = current_point
    return None
