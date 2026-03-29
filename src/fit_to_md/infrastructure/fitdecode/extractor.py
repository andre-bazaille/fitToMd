from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

import fitdecode

from fit_to_md.domain.reporting.entities import FitReport, SessionSummary
from fit_to_md.domain.reporting.ports import HistoricalWeatherProvider
from fit_to_md.infrastructure.fitdecode.builders import SessionSummaryBuilder, SplitBuilder, TransitionBuilder
from fit_to_md.infrastructure.fitdecode.models import ParsedActivityData, ParsedLap, ParsedRecord


_MIN_GRADE_DISTANCE_M = 0.5


class FitdecodeActivityExtractor:
    def __init__(
        self,
        reader_factory: Callable[[str], Any] | None = None,
        summary_builder: SessionSummaryBuilder | None = None,
        split_builder: SplitBuilder | None = None,
        transition_builder: TransitionBuilder | None = None,
        weather_provider: HistoricalWeatherProvider | None = None,
    ) -> None:
        self._reader_factory = reader_factory or fitdecode.FitReader
        self._summary_builder = summary_builder or SessionSummaryBuilder()
        self._split_builder = split_builder or SplitBuilder()
        self._transition_builder = transition_builder or TransitionBuilder()
        self._weather_provider = weather_provider

    def extract(self, source: Path) -> FitReport:
        activity = self._parse_activity(source)
        summary = self._summary_builder.build(activity)
        summary = self._enrich_summary_weather(summary, activity)
        return FitReport(
            summary=summary,
            splits=self._split_builder.build(activity),
            transitions=self._transition_builder.build(activity),
        )

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

        normalized_records = tuple(
            _normalize_running_record_cadence(
                _enrich_record_grades(records),
                sport=sport,
                session_data=session_data,
            )
        )
        return ParsedActivityData(
            session_data=session_data,
            device_infos=tuple(device_infos),
            sport=sport,
            sub_sport=sub_sport,
            laps=tuple(laps),
            records=normalized_records,
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
        distance_m=_coerce_float(values.get("distance")),
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


def _enrich_record_grades(records: list[ParsedRecord]) -> list[ParsedRecord]:
    if not records:
        return []

    ordered_records = sorted(records, key=lambda record: record.timestamp)
    enriched_records: list[ParsedRecord] = []
    for index, record in enumerate(ordered_records):
        grade_percent = record.grade_percent
        if grade_percent is None:
            previous_record = ordered_records[index - 1] if index > 0 else None
            next_record = ordered_records[index + 1] if index + 1 < len(ordered_records) else None
            grade_percent = _compute_grade(previous_record, record, next_record)
        enriched_records.append(
            ParsedRecord(
                timestamp=record.timestamp,
                distance_m=record.distance_m,
                heart_rate_bpm=record.heart_rate_bpm,
                cadence_spm=record.cadence_spm,
                fractional_cadence=record.fractional_cadence,
                speed_mps=record.speed_mps,
                altitude_m=record.altitude_m,
                grade_percent=grade_percent,
                temperature_c=record.temperature_c,
            )
        )
    return enriched_records


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
                distance_m=record.distance_m,
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


def _compute_grade(
    previous_record: ParsedRecord | None,
    current_record: ParsedRecord,
    next_record: ParsedRecord | None,
) -> float | None:
    candidates = (
        (previous_record, next_record),
        (previous_record, current_record),
        (current_record, next_record),
    )
    for start_record, end_record in candidates:
        if start_record is None or end_record is None:
            continue
        if start_record.distance_m is None or end_record.distance_m is None:
            continue
        if start_record.altitude_m is None or end_record.altitude_m is None:
            continue

        delta_distance = end_record.distance_m - start_record.distance_m
        if abs(delta_distance) < _MIN_GRADE_DISTANCE_M:
            continue
        return ((end_record.altitude_m - start_record.altitude_m) / delta_distance) * 100
    return None


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
