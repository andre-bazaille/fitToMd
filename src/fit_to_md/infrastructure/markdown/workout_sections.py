"""Presentation of native laps, linked repetitions, and recovery observations."""

from collections.abc import Sequence
from math import isfinite

from fit_to_md.domain.activity.workout import (
    DurationKind,
    TargetKind,
    WorkoutDuration,
    WorkoutIssue,
    WorkoutTarget,
)
from fit_to_md.domain.reporting.entities import (
    FitReport,
    MeasurementReason,
    NativeLapRow,
    RecoveryChange,
    RecoveryReason,
    RepetitionExclusion,
    RepetitionGroup,
    RepetitionMetric,
    RepetitionReason,
)
from fit_to_md.infrastructure.markdown.sections import (
    _format_duration,
    _format_integer,
    _uses_pace,
)

_MEASUREMENT_NOTES = {
    MeasurementReason.ACTIVE_TIMING_UNAVAILABLE: "active timing unavailable; record-derived measurements omitted",
    MeasurementReason.MISSING_BOUNDARIES: "lap timestamps missing; record-derived measurements omitted",
    MeasurementReason.REVERSED_BOUNDARIES: "lap timestamps reversed; record-derived measurements omitted",
    MeasurementReason.OVERLAPPING_LAPS: "overlapping lap boundaries; record-derived measurements omitted",
    MeasurementReason.NO_RECORDS: "records unavailable for a missing native measurement",
    MeasurementReason.INVALID_DISTANCE_TRACE: "invalid cumulative distance trace",
    MeasurementReason.DISTANCE_BOUNDARY_UNAVAILABLE: "distance unavailable at a lap boundary",
    MeasurementReason.NO_HR_COVERAGE: "no covered HR samples",
    MeasurementReason.NO_CADENCE_COVERAGE: "no covered cadence samples",
}
_WORKOUT_NOTES = {
    WorkoutIssue.INVALID_ASSOCIATION: "invalid workout-step reference; no step inferred",
    WorkoutIssue.MISSING_DEFINITION: "referenced workout step is missing",
    WorkoutIssue.AMBIGUOUS_DEFINITION: "workout-step reference is ambiguous",
    WorkoutIssue.CONTROL_STEP: "reference points to a control step, not a performed effort",
    WorkoutIssue.UNSUPPORTED_DURATION: "workout-step duration is unsupported",
    WorkoutIssue.UNSUPPORTED_TARGET: "workout-step target is unsupported",
    WorkoutIssue.ROLE_CONFLICT: "lap and linked-step roles disagree; lap role shown",
}
_REPETITION_NOTES = {
    RepetitionReason.NO_WORK_LAPS: "no explicitly marked work laps",
    RepetitionReason.MISSING_STEP_ASSOCIATION: "no explicit workout-step association",
    RepetitionReason.INCOMPARABLE_DEFINITION: "incomplete or unsupported step definition",
    RepetitionReason.OVERLAPPING_LAP: "overlapping lap boundaries",
    RepetitionReason.INVALID_MEASUREMENT: "positive distance and active duration unavailable",
    RepetitionReason.INSUFFICIENT_REPETITIONS: "fewer than two valid laps share a step identity and target definition",
}
_RECOVERY_NOTES = {
    RecoveryReason.NOT_PRECEDED_BY_WORK: "preceding lap is not explicitly work",
    RecoveryReason.AMBIGUOUS_LAP_TIMING: "lap boundaries overlap or have ambiguous timing",
    RecoveryReason.ACTIVE_TIMING_UNAVAILABLE: "active timing unavailable",
    RecoveryReason.INCONSISTENT_DURATION: "native and timeline active durations disagree",
    RecoveryReason.UNDER_60_ACTIVE_SECONDS: "recovery has under 60 active seconds",
    RecoveryReason.START_TARGET_UNAVAILABLE: "recovery start is outside active time",
    RecoveryReason.SIXTY_SECOND_TARGET_UNAVAILABLE: "60-active-second target unavailable",
    RecoveryReason.START_HR_UNAVAILABLE: "valid start HR observation unavailable within five wall seconds",
    RecoveryReason.SIXTY_SECOND_HR_UNAVAILABLE: "valid 60-second HR observation unavailable within five wall seconds",
}


def _escape_cell(value: str) -> str:
    """Normalize whitespace and escape characters that can change Markdown cells."""
    normalized = " ".join(value.split())
    special = frozenset("\\|*_`[]<>!#~")
    return "".join(
        f"\\{character}" if character in special else character
        for character in normalized
    )


def _format_new_duration(seconds: float | None) -> str:
    if seconds is None or not isfinite(seconds) or seconds < 0:
        return "-"
    return _format_duration(float(int(seconds + 0.5)))


def _format_lap_metric(row: NativeLapRow, sport: str | None) -> str:
    if _uses_pace(sport):
        value = _format_new_duration(row.pace_seconds_per_km)
        return f"{value}/km" if value != "-" else "-"
    return (
        f"{row.speed_kmh:.2f} km/h"
        if row.speed_kmh is not None and isfinite(row.speed_kmh)
        else "-"
    )


def _format_lap_row(row: NativeLapRow, sport: str | None) -> str:
    label = _escape_cell(row.label or "") or f"Lap {row.index}"
    distance = (
        f"{row.distance_m / 1000:.2f} km"
        if row.distance_m is not None and isfinite(row.distance_m)
        else "-"
    )
    return (
        f"| {row.index} | {label} | {row.role.value} | {distance} | "
        f"{_format_new_duration(row.active_duration_s)} | "
        f"{_format_lap_metric(row, sport)} | "
        f"{_format_integer(row.avg_heart_rate_bpm)} | "
        f"{_format_integer(row.max_heart_rate_bpm)} | "
        f"{_format_integer(row.avg_cadence_spm)} |"
    )


def _lap_note_messages(row: NativeLapRow) -> list[str]:
    reasons: list[str] = []
    for issue in row.issues:
        message = _MEASUREMENT_NOTES.get(issue.reason)
        if message is not None and message not in reasons:
            reasons.append(message)
    for workout_issue in row.workout_issues:
        message = _WORKOUT_NOTES.get(workout_issue)
        if message is not None and message not in reasons:
            reasons.append(message)
    for reason, value in (
        (row.hr_coverage.reason, row.avg_heart_rate_bpm),
        (row.cadence_coverage.reason, row.avg_cadence_spm),
    ):
        if value is None and reason in (
            MeasurementReason.NO_HR_COVERAGE,
            MeasurementReason.NO_CADENCE_COVERAGE,
        ):
            message = _MEASUREMENT_NOTES[reason]
            if message not in reasons:
                reasons.append(message)
    return reasons


class WorkoutBreakdownSectionRenderer:
    heading = "Workout Breakdown"

    def is_enabled(self, report: FitReport) -> bool:
        return report.workout is not None

    def render_lines(self, report: FitReport) -> Sequence[str]:
        workout = report.workout
        assert workout is not None
        if not workout.laps:
            lines = ["", "Workout breakdown unavailable: no native laps recorded."]
            if report.zones is None:
                lines.append(
                    "HR zones are not configured; no zone boundaries are estimated."
                )
            return lines
        metric_label = "Pace" if _uses_pace(report.summary.sport) else "Speed"
        lines = [
            "",
            f"| Lap | Label | Role | Distance | Active time | {metric_label} | Avg HR | Max HR | Avg Cad |",
            "|---|---|---|---|---|---|---|---|---|",
            *(_format_lap_row(row, report.summary.sport) for row in workout.laps),
            "",
            "Roles and step labels come from recorded metadata; unknown roles and links are not inferred.",
        ]
        if report.zones is None:
            lines.append(
                "HR zones are not configured; no zone boundaries are estimated."
            )
        note_laps: dict[str, list[int]] = {}
        for row in workout.laps:
            for message in _lap_note_messages(row):
                note_laps.setdefault(message, []).append(row.index)
        if note_laps:
            lines.extend(("", "Measurement notes:"))
            for message, indices in note_laps.items():
                subject = "Lap" if len(indices) == 1 else "Laps"
                lines.append(f"- {subject} {', '.join(map(str, indices))}: {message}.")
        return lines


def _format_target(target: WorkoutTarget) -> str:
    if target.kind == TargetKind.OPEN:
        return "open target"
    assert target.lower is not None and target.upper is not None
    unit = "m/s" if target.kind == TargetKind.SPEED_MPS else "rpm"
    return f"{target.lower:g}–{target.upper:g} {unit}"


def _format_definition(group: RepetitionGroup) -> str:
    duration: WorkoutDuration = group.duration
    if duration.kind == DurationKind.OPEN:
        duration_label = "open duration"
    else:
        assert duration.value is not None
        unit = "m" if duration.kind == DurationKind.METERS else "s"
        duration_label = f"{duration.value:g} {unit}"
    target_label = _format_target(group.target)
    if group.secondary_target is not None:
        target_label += f"; secondary {_format_target(group.secondary_target)}"
    return f"{duration_label}; {target_label}"


def _format_repetition_value(value: float, metric: RepetitionMetric) -> str:
    if metric == RepetitionMetric.PACE_SECONDS_PER_KM:
        return f"{_format_new_duration(value)}/km"
    return f"{value:.2f} km/h"


def _exclusion_lines(exclusions: tuple[RepetitionExclusion, ...]) -> list[str]:
    by_reason: dict[RepetitionReason, list[int]] = {}
    for exclusion in exclusions:
        by_reason.setdefault(exclusion.reason, []).append(exclusion.lap_index)
    return [
        f"- Laps {', '.join(map(str, indices))}: {_REPETITION_NOTES[reason]}."
        for reason, indices in by_reason.items()
    ]


class RepetitionConsistencySectionRenderer:
    heading = "Repetition Consistency"

    def is_enabled(self, report: FitReport) -> bool:
        return report.workout is not None

    def render_lines(self, report: FitReport) -> Sequence[str]:
        workout = report.workout
        assert workout is not None
        analysis = workout.repetitions
        lines = [""]
        if analysis.groups:
            lines.extend(
                (
                    "| Step | Shared definition | Count | Mean | Fastest | Slowest | CV | Excluded |",
                    "|---|---|---|---|---|---|---|---|",
                )
            )
            for group in analysis.groups:
                lines.append(
                    f"| Step {_escape_cell(group.step_identity)} | {_format_definition(group)} | "
                    f"{group.count} | {_format_repetition_value(group.mean, group.metric)} | "
                    f"{_format_repetition_value(group.fastest, group.metric)} | "
                    f"{_format_repetition_value(group.slowest, group.metric)} | "
                    f"{group.coefficient_variation_percent:.1f}% | {group.excluded_count} |"
                )
            lines.extend(
                (
                    "",
                    "Each repetition has equal weight; CV uses population standard deviation.",
                )
            )
        else:
            reason = analysis.reason or RepetitionReason.INSUFFICIENT_REPETITIONS
            lines.append(
                f"Comparable repetitions unavailable: {_REPETITION_NOTES[reason]}."
            )
        lines.append(f"Excluded work laps: {analysis.excluded_count}.")
        lines.extend(_exclusion_lines(analysis.exclusions))
        return lines


def _format_hr(value: float | None) -> str:
    return f"{value:.0f} bpm" if value is not None else "-"


def _format_offset(value: float | None) -> str:
    return f"{value:+.1f} s" if value is not None else "-"


def _format_recovery_row(change: RecoveryChange) -> str:
    delta = f"{change.delta_bpm:+.0f} bpm" if change.delta_bpm is not None else "-"
    reason = _RECOVERY_NOTES[change.reason] if change.reason is not None else "-"
    previous = (
        str(change.preceding_lap_index)
        if change.preceding_lap_index is not None
        else "-"
    )
    return (
        f"| {change.lap_index} | {previous} | {_format_hr(change.start_hr_bpm)} | "
        f"{_format_hr(change.hr_60_bpm)} | {delta} | "
        f"{_format_offset(change.start_observation_offset_s)} | "
        f"{_format_offset(change.sixty_second_observation_offset_s)} | {reason} |"
    )


class RecoveryChangesSectionRenderer:
    heading = "Recovery Heart-Rate Changes"

    def is_enabled(self, report: FitReport) -> bool:
        return report.workout is not None

    def render_lines(self, report: FitReport) -> Sequence[str]:
        workout = report.workout
        assert workout is not None
        if not workout.recoveries.changes:
            return ["", "No explicitly marked recovery laps recorded."]
        return [
            "",
            "HR change over first 60 active seconds = HR at 60 seconds − HR at recovery start; a negative value means HR fell.",
            "Endpoint observations must be within five wall-clock seconds of each target and in the same uninterrupted active interval.",
            "Offsets are signed wall-clock seconds from the start and 60-active-second targets.",
            "",
            "| Recovery lap | Previous lap | Start HR | HR at 60 s | Change | Start offset | 60 s offset | Reason |",
            "|---|---|---|---|---|---|---|---|",
            *(_format_recovery_row(change) for change in workout.recoveries.changes),
        ]
