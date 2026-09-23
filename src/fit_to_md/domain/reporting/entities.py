from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from fit_to_md.domain.activity.workout import LapRole, WorkoutIssue, WorkoutStep

if TYPE_CHECKING:
    from fit_to_md.domain.activity.workout import WorkoutDuration, WorkoutTarget
    from fit_to_md.domain.reporting.heart_rate_zones import HeartRateZoneBoundaries


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
    sport: str | None = None

    @property
    def has_fit_temperature(self) -> bool:
        """Return whether the activity contains any FIT-native temperature data."""
        return any(
            temperature is not None
            for temperature in (
                self.avg_temperature_c,
                self.min_temperature_c,
                self.max_temperature_c,
            )
        )


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
    distance_m: float = 1000.0
    is_partial: bool = False


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
    sampling_note: str | None = None


@dataclass(frozen=True)
class FitReport:
    summary: SessionSummary
    splits: tuple[Split, ...] = field(default_factory=tuple)
    transitions: tuple[TransitionDynamics, ...] = field(default_factory=tuple)
    workout: "WorkoutReport | None" = None
    zones: "HeartRateZoneReport | None" = None


class MeasurementReason(StrEnum):
    ACTIVE_TIMING_UNAVAILABLE = "active_timing_unavailable"
    MISSING_BOUNDARIES = "missing_boundaries"
    REVERSED_BOUNDARIES = "reversed_boundaries"
    OVERLAPPING_LAPS = "overlapping_laps"
    NO_RECORDS = "no_records"
    INVALID_DISTANCE_TRACE = "invalid_distance_trace"
    DISTANCE_BOUNDARY_UNAVAILABLE = "distance_boundary_unavailable"
    NO_HR_COVERAGE = "no_hr_coverage"
    NO_CADENCE_COVERAGE = "no_cadence_coverage"
    INCONSISTENT_TIMING = "inconsistent_timing"
    TOTAL_ACTIVE_TIME_UNAVAILABLE = "total_active_time_unavailable"


@dataclass(frozen=True)
class MeasurementIssue:
    metric: str
    reason: MeasurementReason


@dataclass(frozen=True)
class MeasurementCoverage:
    covered_seconds: float | None
    total_active_seconds: float | None
    reason: MeasurementReason | None = None


@dataclass(frozen=True)
class NativeLapRow:
    index: int
    label: str | None
    role: LapRole
    workout_step: WorkoutStep | None
    workout_issues: tuple[WorkoutIssue, ...]
    start_time: datetime | None
    end_time: datetime | None
    distance_m: float | None
    active_duration_s: float | None
    pace_seconds_per_km: float | None
    speed_kmh: float | None
    avg_heart_rate_bpm: int | None
    max_heart_rate_bpm: int | None
    avg_cadence_spm: int | None
    hr_coverage: MeasurementCoverage
    cadence_coverage: MeasurementCoverage
    issues: tuple[MeasurementIssue, ...] = ()


ZoneSeconds = tuple[float, float, float, float, float]
ZonePercentages = tuple[
    float | None, float | None, float | None, float | None, float | None
]


@dataclass(frozen=True)
class ZoneMeasurement:
    """Unrounded durations and percentages for one session or lap scope."""

    zone_seconds: ZoneSeconds | None
    zone_percentages: ZonePercentages | None
    coverage: MeasurementCoverage
    coverage_percent: float | None
    unknown_seconds: float | None
    issues: tuple[MeasurementReason, ...] = ()


@dataclass(frozen=True)
class LapZoneMeasurement:
    source_position: int
    lap_index: int
    measurement: ZoneMeasurement


@dataclass(frozen=True)
class HeartRateZoneReport:
    boundaries: "HeartRateZoneBoundaries"
    session: ZoneMeasurement
    laps: tuple[LapZoneMeasurement, ...] = ()


class RepetitionMetric(StrEnum):
    PACE_SECONDS_PER_KM = "pace_seconds_per_km"
    SPEED_KMH = "speed_kmh"


class RepetitionReason(StrEnum):
    NO_WORK_LAPS = "no_work_laps"
    MISSING_STEP_ASSOCIATION = "missing_step_association"
    INCOMPARABLE_DEFINITION = "incomparable_definition"
    OVERLAPPING_LAP = "overlapping_lap"
    INVALID_MEASUREMENT = "invalid_measurement"
    INSUFFICIENT_REPETITIONS = "insufficient_repetitions"


@dataclass(frozen=True)
class RepetitionExclusion:
    source_position: int
    lap_index: int
    reason: RepetitionReason


@dataclass(frozen=True)
class RepetitionGroup:
    step_identity: str
    duration: "WorkoutDuration"
    target: "WorkoutTarget"
    secondary_target: "WorkoutTarget | None"
    source_positions: tuple[int, ...]
    lap_indices: tuple[int, ...]
    metric: RepetitionMetric
    mean: float
    fastest: float
    slowest: float
    coefficient_variation_percent: float
    excluded_laps: tuple[RepetitionExclusion, ...] = ()

    @property
    def count(self) -> int:
        return len(self.source_positions)

    @property
    def excluded_count(self) -> int:
        return len(self.excluded_laps)


@dataclass(frozen=True)
class RepetitionAnalysis:
    groups: tuple[RepetitionGroup, ...]
    exclusions: tuple[RepetitionExclusion, ...]
    reason: RepetitionReason | None = None

    @property
    def excluded_count(self) -> int:
        return len(self.exclusions)


class RecoveryReason(StrEnum):
    NOT_PRECEDED_BY_WORK = "not_preceded_by_work"
    AMBIGUOUS_LAP_TIMING = "ambiguous_lap_timing"
    ACTIVE_TIMING_UNAVAILABLE = "active_timing_unavailable"
    INCONSISTENT_DURATION = "inconsistent_duration"
    UNDER_60_ACTIVE_SECONDS = "under_60_active_seconds"
    START_TARGET_UNAVAILABLE = "start_target_unavailable"
    SIXTY_SECOND_TARGET_UNAVAILABLE = "sixty_second_target_unavailable"
    START_HR_UNAVAILABLE = "start_hr_unavailable"
    SIXTY_SECOND_HR_UNAVAILABLE = "sixty_second_hr_unavailable"


@dataclass(frozen=True)
class RecoveryChange:
    """Observation offsets are signed wall seconds from their target times."""

    source_position: int
    lap_index: int
    preceding_lap_index: int | None
    delta_bpm: float | None = None
    start_hr_bpm: float | None = None
    hr_60_bpm: float | None = None
    start_target_time: datetime | None = None
    sixty_second_target_time: datetime | None = None
    start_observation_time: datetime | None = None
    sixty_second_observation_time: datetime | None = None
    start_observation_offset_s: float | None = None
    sixty_second_observation_offset_s: float | None = None
    reason: RecoveryReason | None = None


@dataclass(frozen=True)
class RecoveryAnalysis:
    changes: tuple[RecoveryChange, ...]


@dataclass(frozen=True)
class WorkoutReport:
    laps: tuple[NativeLapRow, ...]
    repetitions: RepetitionAnalysis
    recoveries: RecoveryAnalysis
