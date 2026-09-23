"""Descriptive comparison of explicitly linked work laps."""

from dataclasses import dataclass
from math import isfinite
from statistics import fmean, pstdev

from fit_to_md.domain.activity.entities import Activity
from fit_to_md.domain.activity.workout import (
    LapRole,
    WorkoutDuration,
    WorkoutTarget,
)
from fit_to_md.domain.reporting.entities import (
    MeasurementReason,
    NativeLapRow,
    RepetitionAnalysis,
    RepetitionExclusion,
    RepetitionGroup,
    RepetitionMetric,
    RepetitionReason,
)
from fit_to_md.domain.reporting.workout import NativeLapReportBuilder


@dataclass(frozen=True)
class _Candidate:
    source_position: int
    row: NativeLapRow
    value: float


_GroupKey = tuple[str, WorkoutDuration, WorkoutTarget, WorkoutTarget | None]


def _overlaps(row: NativeLapRow) -> bool:
    return any(
        issue.metric == "lap" and issue.reason == MeasurementReason.OVERLAPPING_LAPS
        for issue in row.issues
    )


class RepetitionAnalysisBuilder:
    """Equal-weight pace or speed statistics for comparable recorded work laps."""

    def build(self, activity: Activity) -> RepetitionAnalysis:
        running = (activity.sport or "").strip().casefold() == "running"
        metric = (
            RepetitionMetric.PACE_SECONDS_PER_KM
            if running
            else RepetitionMetric.SPEED_KMH
        )
        grouped: dict[_GroupKey, list[_Candidate]] = {}
        grouped_exclusions: dict[_GroupKey, list[RepetitionExclusion]] = {}
        exclusions: list[RepetitionExclusion] = []
        work_count = 0
        for position, row in enumerate(NativeLapReportBuilder().build(activity)):
            if row.role != LapRole.WORK:
                continue
            work_count += 1
            step = row.workout_step
            if step is None or not step.identity.strip():
                exclusions.append(
                    RepetitionExclusion(
                        position, row.index, RepetitionReason.MISSING_STEP_ASSOCIATION
                    )
                )
                continue
            if not step.has_comparable_definition:
                exclusions.append(
                    RepetitionExclusion(
                        position, row.index, RepetitionReason.INCOMPARABLE_DEFINITION
                    )
                )
                continue
            assert step.duration is not None and step.target is not None
            key: _GroupKey = (
                step.identity,
                step.duration,
                step.target,
                step.secondary_target,
            )
            reason: RepetitionReason | None = None
            if _overlaps(row):
                reason = RepetitionReason.OVERLAPPING_LAP
            value = row.pace_seconds_per_km if running else row.speed_kmh
            if reason is None and (value is None or not isfinite(value) or value <= 0):
                reason = RepetitionReason.INVALID_MEASUREMENT
            if reason is not None:
                exclusion = RepetitionExclusion(position, row.index, reason)
                exclusions.append(exclusion)
                grouped_exclusions.setdefault(key, []).append(exclusion)
            else:
                assert value is not None
                grouped.setdefault(key, []).append(_Candidate(position, row, value))

        groups: list[RepetitionGroup] = []
        for key, candidates in grouped.items():
            if len(candidates) < 2:
                exclusions.extend(
                    RepetitionExclusion(
                        candidate.source_position,
                        candidate.row.index,
                        RepetitionReason.INSUFFICIENT_REPETITIONS,
                    )
                    for candidate in candidates
                )
                continue
            values = [candidate.value for candidate in candidates]
            average = fmean(values)
            groups.append(
                RepetitionGroup(
                    step_identity=key[0],
                    duration=key[1],
                    target=key[2],
                    secondary_target=key[3],
                    source_positions=tuple(c.source_position for c in candidates),
                    lap_indices=tuple(c.row.index for c in candidates),
                    metric=metric,
                    mean=average,
                    fastest=min(values) if running else max(values),
                    slowest=max(values) if running else min(values),
                    coefficient_variation_percent=100 * pstdev(values) / average,
                    excluded_laps=tuple(grouped_exclusions.get(key, ())),
                )
            )
        reason = None
        if work_count == 0:
            reason = RepetitionReason.NO_WORK_LAPS
        elif not groups:
            reason = RepetitionReason.INSUFFICIENT_REPETITIONS
        return RepetitionAnalysis(tuple(groups), tuple(exclusions), reason)
