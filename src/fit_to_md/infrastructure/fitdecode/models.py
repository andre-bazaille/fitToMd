from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class ParsedRecord:
    timestamp: datetime
    distance_m: float | None
    heart_rate_bpm: int | None
    cadence_spm: int | None
    fractional_cadence: float | None
    speed_mps: float | None
    altitude_m: float | None
    grade_percent: float | None
    temperature_c: float | None


@dataclass(frozen=True)
class ParsedLap:
    index: int
    start_time: datetime | None
    end_time: datetime | None
    total_distance_m: float | None
    total_timer_time_s: float | None
    total_ascent_m: float | None
    total_descent_m: float | None
    avg_heart_rate_bpm: int | None
    max_heart_rate_bpm: int | None
    avg_cadence_spm: int | None
    avg_temperature_c: float | None
    min_temperature_c: float | None
    max_temperature_c: float | None


@dataclass(frozen=True)
class ParsedActivityData:
    session_data: dict[str, object] = field(default_factory=dict)
    device_infos: tuple[dict[str, object], ...] = field(default_factory=tuple)
    sport: str | None = None
    sub_sport: str | None = None
    laps: tuple[ParsedLap, ...] = field(default_factory=tuple)
    records: tuple[ParsedRecord, ...] = field(default_factory=tuple)
