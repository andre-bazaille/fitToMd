from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class ActivitySession:
    start_time: datetime | None = None
    end_time: datetime | None = None
    start_latitude_deg: float | None = None
    start_longitude_deg: float | None = None
    total_distance_m: float | None = None
    total_timer_time_s: float | None = None
    total_elapsed_time_s: float | None = None
    total_ascent_m: float | None = None
    total_descent_m: float | None = None
    avg_heart_rate_bpm: int | None = None
    max_heart_rate_bpm: int | None = None
    avg_cadence_spm: int | None = None
    avg_speed_mps: float | None = None
    avg_temperature_c: float | None = None
    min_temperature_c: float | None = None
    max_temperature_c: float | None = None


@dataclass(frozen=True)
class ActivityRecord:
    timestamp: datetime
    elapsed_time_s: float | None
    distance_m: float | None
    latitude_deg: float | None
    longitude_deg: float | None
    heart_rate_bpm: int | None
    cadence_spm: int | None
    fractional_cadence: float | None
    speed_mps: float | None
    altitude_m: float | None
    grade_percent: float | None
    temperature_c: float | None


@dataclass(frozen=True)
class ActivityLap:
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
class Activity:
    session: ActivitySession = field(default_factory=ActivitySession)
    sport: str | None = None
    sub_sport: str | None = None
    laps: tuple[ActivityLap, ...] = field(default_factory=tuple)
    records: tuple[ActivityRecord, ...] = field(default_factory=tuple)
