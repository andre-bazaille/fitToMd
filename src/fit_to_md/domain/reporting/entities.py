from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class SessionSummary:
    start_time: datetime | None
    activity_name: str | None
    activity_type: str | None
    total_distance_km: float | None
    total_timer_time_s: float | None
    total_elapsed_time_s: float | None
    total_ascent_m: float | None
    total_descent_m: float | None
    avg_heart_rate_bpm: int | None
    max_heart_rate_bpm: int | None
    avg_cadence_spm: int | None
    avg_speed_kmh: float | None
    avg_temperature_c: float | None
    min_temperature_c: float | None
    max_temperature_c: float | None
    weather: "WeatherSummary | None" = None


@dataclass(frozen=True)
class WeatherSummary:
    source: str
    temperature_c: float | None
    apparent_temperature_c: float | None
    condition_summary: str | None
    wind_speed_kmh: float | None
    wind_direction_label: str | None
    temperature_min_c: float | None = None
    temperature_max_c: float | None = None


@dataclass(frozen=True)
class Split:
    kilometer: int
    time_seconds: float | None
    pace_seconds_per_km: float | None
    elevation_delta_m: float | None
    avg_heart_rate_bpm: int | None
    max_heart_rate_bpm: int | None
    avg_cadence_spm: int | None


@dataclass(frozen=True)
class TransitionSample:
    elapsed_seconds: float
    heart_rate_bpm: int | None
    speed_kmh: float | None
    grade_percent: float | None


@dataclass(frozen=True)
class TransitionDynamics:
    label: str
    samples: tuple[TransitionSample, ...]


@dataclass(frozen=True)
class FitReport:
    summary: SessionSummary
    splits: tuple[Split, ...] = field(default_factory=tuple)
    transitions: tuple[TransitionDynamics, ...] = field(default_factory=tuple)
