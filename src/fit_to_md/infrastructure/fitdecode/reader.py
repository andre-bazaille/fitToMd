from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import fitdecode

from fit_to_md.domain.activity.entities import (
    Activity,
    ActivityLap,
    ActivityRecord,
    ActivitySession,
)
from fit_to_md.domain.activity.ports import (
    InvalidActivityError,
    UnsupportedActivityError,
)
from fit_to_md.domain.activity.timeline import (
    TimerState,
    TimerTransition,
    build_active_timeline,
)
from fit_to_md.infrastructure.fitdecode.workout import attach_workout_metadata


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


class FitdecodeActivityReader:
    def __init__(
        self,
        reader_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self._reader_factory = reader_factory or fitdecode.FitReader

    def read(self, source: Path) -> Activity:
        try:
            return self._read(source)
        except fitdecode.FitError as error:
            raise InvalidActivityError(str(error)) from error

    def _read(self, source: Path) -> Activity:
        session_values: dict[str, object] = {}
        session_count = 0
        laps: list[ActivityLap] = []
        records: list[ActivityRecord] = []
        record_samples: list[ActivityRecord] = []
        timer_events: list[_TimerEvent] = []
        lap_values: list[dict[str, object]] = []
        step_values: list[dict[str, object]] = []
        transitions: list[TimerTransition] = []
        invalid_workout_events = False
        sport: str | None = None
        sub_sport: str | None = None

        with self._reader_factory(str(source)) as fit_file:
            for frame in fit_file:
                if getattr(frame, "frame_type", None) != fitdecode.FIT_FRAME_DATA:
                    continue

                if frame.name == "session":
                    session_count += 1
                    if session_count > 1:
                        raise UnsupportedActivityError(
                            "FIT files with multiple sessions are not supported."
                        )
                    session_values = _extract_message_values(frame)
                elif frame.name == "sport":
                    values = _extract_message_values(frame)
                    sport = _coerce_text(values.get("sport")) or sport
                    sub_sport = _coerce_text(values.get("sub_sport")) or sub_sport
                elif frame.name == "lap":
                    laps.append(_parse_lap(frame, len(laps) + 1))
                    lap_values.append(_extract_message_values(frame))
                elif frame.name == "workout_step":
                    step_values.append(_extract_message_values(frame))
                elif frame.name == "record":
                    record = _parse_record(frame)
                    if record is not None:
                        record_samples.append(record)
                        if _has_reportable_measurement(record):
                            records.append(record)
                elif frame.name == "event":
                    values = _extract_message_values(frame)
                    if values.get("event") == "timer":
                        timestamp = values.get("timestamp")
                        state = values.get("event_type")
                        if isinstance(timestamp, datetime) and isinstance(state, str):
                            if (
                                state
                                in _TIMER_START_EVENT_TYPES | _TIMER_STOP_EVENT_TYPES
                            ):
                                transitions.append(
                                    TimerTransition(
                                        timestamp,
                                        TimerState.START
                                        if state in _TIMER_START_EVENT_TYPES
                                        else TimerState.STOP,
                                    )
                                )
                            else:
                                invalid_workout_events = True
                        else:
                            invalid_workout_events = True
                    timer_event = _parse_timer_event(frame)
                    if timer_event is not None:
                        timer_events.append(timer_event)

        sport = sport or _coerce_text(session_values.get("sport"))
        sub_sport = sub_sport or _coerce_text(session_values.get("sub_sport"))
        session = _parse_session(session_values, is_running=_is_running_activity(sport))
        normalized_records = _normalize_running_record_cadence(records, sport=sport)
        records_with_elapsed, has_active_timing = _assign_elapsed_time_to_records(
            records=normalized_records,
            timer_events=tuple(timer_events),
        )
        normalized_samples = _normalize_running_record_cadence(
            record_samples, sport=sport
        )
        samples_with_elapsed, _ = _assign_elapsed_time_to_records(
            records=normalized_samples,
            timer_events=tuple(timer_events),
        )
        return Activity(
            session=session,
            sport=sport,
            sub_sport=sub_sport,
            laps=attach_workout_metadata(
                tuple(laps), tuple(lap_values), tuple(step_values)
            ),
            records=records_with_elapsed,
            record_samples=samples_with_elapsed,
            has_active_record_timing=has_active_timing,
            active_timeline=build_active_timeline(
                session.start_time,
                session.end_time,
                session.total_timer_time_s,
                session.total_elapsed_time_s,
                tuple(transitions),
                invalid_events=invalid_workout_events,
            ),
        )


def _parse_session(values: dict[str, object], is_running: bool) -> ActivitySession:
    return ActivitySession(
        start_time=_coerce_datetime(values.get("start_time")),
        end_time=_coerce_datetime(values.get("timestamp")),
        start_latitude_deg=_semicircles_to_degrees(values.get("start_position_lat")),
        start_longitude_deg=_semicircles_to_degrees(values.get("start_position_long")),
        total_distance_m=_coerce_float(values.get("total_distance")),
        total_timer_time_s=_coerce_float(values.get("total_timer_time")),
        total_elapsed_time_s=_coerce_float(values.get("total_elapsed_time")),
        total_ascent_m=_coerce_float(values.get("total_ascent")),
        total_descent_m=_coerce_float(values.get("total_descent")),
        avg_heart_rate_bpm=_coerce_int(values.get("avg_heart_rate")),
        max_heart_rate_bpm=_coerce_int(values.get("max_heart_rate")),
        avg_cadence_spm=_resolve_session_cadence(values, is_running=is_running),
        avg_speed_mps=_coalesce_float(
            values.get("enhanced_avg_speed"), values.get("avg_speed")
        ),
        avg_temperature_c=_coerce_float(values.get("avg_temperature")),
        min_temperature_c=_coerce_float(values.get("min_temperature")),
        max_temperature_c=_coerce_float(values.get("max_temperature")),
    )


def _parse_lap(frame: Any, index: int) -> ActivityLap:
    values = _extract_message_values(frame)
    return ActivityLap(
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


def _parse_record(frame: Any) -> ActivityRecord | None:
    values = _extract_message_values(frame)
    timestamp = _coerce_datetime(values.get("timestamp"))
    if timestamp is None:
        return None

    record = ActivityRecord(
        timestamp=timestamp,
        elapsed_time_s=None,
        distance_m=_coerce_float(values.get("distance")),
        latitude_deg=_semicircles_to_degrees(values.get("position_lat")),
        longitude_deg=_semicircles_to_degrees(values.get("position_long")),
        heart_rate_bpm=_coerce_int(values.get("heart_rate")),
        cadence_spm=_coerce_int(values.get("cadence")),
        fractional_cadence=_coerce_float(values.get("fractional_cadence")),
        speed_mps=_coalesce_float(values.get("enhanced_speed"), values.get("speed")),
        altitude_m=_coalesce_float(
            values.get("enhanced_altitude"), values.get("altitude")
        ),
        grade_percent=_coerce_float(values.get("grade")),
        temperature_c=_coerce_float(values.get("temperature")),
    )
    return record


def _has_reportable_measurement(record: ActivityRecord) -> bool:
    return any(
        value is not None
        for value in (
            record.distance_m,
            record.heart_rate_bpm,
            record.cadence_spm,
            record.speed_mps,
            record.altitude_m,
            record.grade_percent,
            record.temperature_c,
        )
    )


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
    records: list[ActivityRecord],
    sport: str | None,
) -> list[ActivityRecord]:
    if not _is_running_activity(sport):
        return records

    normalized_records: list[ActivityRecord] = []
    for record in records:
        normalized_records.append(
            ActivityRecord(
                timestamp=record.timestamp,
                elapsed_time_s=record.elapsed_time_s,
                distance_m=record.distance_m,
                latitude_deg=record.latitude_deg,
                longitude_deg=record.longitude_deg,
                heart_rate_bpm=record.heart_rate_bpm,
                cadence_spm=_normalize_running_cadence(
                    record.cadence_spm, record.fractional_cadence
                ),
                fractional_cadence=record.fractional_cadence,
                speed_mps=record.speed_mps,
                altitude_m=record.altitude_m,
                grade_percent=record.grade_percent,
                temperature_c=record.temperature_c,
            )
        )
    return normalized_records


def _assign_elapsed_time_to_records(
    records: list[ActivityRecord],
    timer_events: tuple[_TimerEvent, ...],
) -> tuple[tuple[ActivityRecord, ...], bool]:
    if not records:
        return tuple(), False

    origin_time = records[0].timestamp
    intervals = _build_timer_intervals(
        timer_events=timer_events,
        fallback_start_time=origin_time,
        fallback_end_time=records[-1].timestamp,
    )
    if not intervals:
        return tuple(
            replace(
                record, elapsed_time_s=(record.timestamp - origin_time).total_seconds()
            )
            for record in records
        ), False

    return tuple(
        replace(
            record,
            elapsed_time_s=_elapsed_time_at_timestamp(record.timestamp, intervals),
        )
        for record in records
    ), True


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
        return _normalize_running_cadence(
            running_cadence, _coerce_float(values.get("avg_fractional_cadence"))
        )
    return _coerce_int(values.get("avg_cadence"))


def _resolve_session_cadence(values: dict[str, object], is_running: bool) -> int | None:
    running_cadence = _coerce_int(values.get("avg_running_cadence"))
    if is_running and running_cadence is not None:
        return _normalize_running_cadence(
            running_cadence,
            _coerce_float(values.get("avg_fractional_cadence")),
        )
    return _coerce_int(values.get("avg_cadence"))


def _normalize_running_cadence(
    cadence: int | None, fractional_cadence: float | None
) -> int | None:
    if cadence is None:
        return None
    return round((cadence + (fractional_cadence or 0.0)) * 2)


def _is_running_activity(sport: str | None) -> bool:
    return (sport or "").lower() == "running"


def _coerce_text(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _coerce_datetime(value: object) -> datetime | None:
    return cast(datetime, value) if hasattr(value, "isoformat") else None


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
