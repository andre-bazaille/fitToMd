from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class SessionSummary:
    start_time: Optional[datetime]
    activity_name: Optional[str]
    activity_type: Optional[str]
    total_distance_km: Optional[float]
    total_timer_time_s: Optional[float]
    total_elapsed_time_s: Optional[float]
    total_ascent_m: Optional[float]
    total_descent_m: Optional[float]
    avg_heart_rate_bpm: Optional[int]
    max_heart_rate_bpm: Optional[int]
    avg_cadence_spm: Optional[int]
    avg_speed_kmh: Optional[float]
    avg_temperature_c: Optional[float]
    min_temperature_c: Optional[float]
    max_temperature_c: Optional[float]


@dataclass(frozen=True)
class Split:
    kilometer: int
    time_seconds: Optional[float]
    pace_seconds_per_km: Optional[float]
    elevation_delta_m: Optional[float]
    avg_heart_rate_bpm: Optional[int]
    max_heart_rate_bpm: Optional[int]
    avg_cadence_spm: Optional[int]


@dataclass(frozen=True)
class TransitionSample:
    offset_seconds: int
    heart_rate_bpm: Optional[int]
    speed_kmh: Optional[float]
    grade_percent: Optional[float]


@dataclass(frozen=True)
class TransitionDynamics:
    label: str
    samples: tuple[TransitionSample, ...]


@dataclass(frozen=True)
class FitReport:
    summary: SessionSummary
    splits: tuple[Split, ...] = field(default_factory=tuple)
    transitions: tuple[TransitionDynamics, ...] = field(default_factory=tuple)
