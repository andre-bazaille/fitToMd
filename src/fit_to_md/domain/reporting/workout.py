"""Native-lap measurements, independent of kilometer splits and presentation."""

from datetime import datetime
from math import isfinite

from fit_to_md.domain.activity.entities import Activity, ActivityLap, ActivityRecord
from fit_to_md.domain.activity.timeline import ActiveInterval, ActiveTimeline
from fit_to_md.domain.reporting.entities import (
    MeasurementCoverage,
    MeasurementIssue,
    MeasurementReason,
    NativeLapRow,
)
from fit_to_md.domain.reporting.measurements import (
    active_duration,
    chronological_records,
    contains_lap_time,
    hr_spans,
    sampled_spans,
    valid_cadence,
    valid_hr,
    weighted_average,
)


def _native_total(value: float | None) -> float | None:
    if value is None or isinstance(value, bool) or not isfinite(value) or value < 0:
        return None
    return value


def _native_hr(value: int | None) -> int | None:
    return value if valid_hr(value) else None


def _native_cadence(value: int | None) -> int | None:
    if value is None or isinstance(value, bool) or not isfinite(value) or value < 0:
        return None
    return value


def _lap_boundary_issue(
    lap: ActivityLap, laps: tuple[ActivityLap, ...], position: int
) -> MeasurementReason | None:
    if lap.start_time is None or lap.end_time is None:
        return MeasurementReason.MISSING_BOUNDARIES
    if lap.end_time <= lap.start_time:
        return MeasurementReason.REVERSED_BOUNDARIES
    for other_position, other in enumerate(laps):
        if (
            other_position == position
            or other.start_time is None
            or other.end_time is None
        ):
            continue
        if other.end_time <= other.start_time:
            continue
        if lap.start_time < other.end_time and other.start_time < lap.end_time:
            return MeasurementReason.OVERLAPPING_LAPS
    return None


def _distance_at(
    timestamp: datetime,
    records: tuple[ActivityRecord, ...],
    timeline: ActiveTimeline,
) -> float | None:
    for record in records:
        if record.timestamp == timestamp:
            return _native_total(record.distance_m)
    before = next((r for r in reversed(records) if r.timestamp < timestamp), None)
    after = next((r for r in records if r.timestamp > timestamp), None)
    if before is None or after is None:
        return None
    left, right = _native_total(before.distance_m), _native_total(after.distance_m)
    if left is None or right is None or right < left:
        return None
    gap = (after.timestamp - before.timestamp).total_seconds()
    if gap > 5:
        return None
    if not any(
        interval.start <= before.timestamp and after.timestamp <= interval.end
        for interval in timeline.intervals
    ):
        return None
    return left + (right - left) * (timestamp - before.timestamp).total_seconds() / gap


def _derived_distance(
    start: datetime,
    end: datetime,
    records: tuple[ActivityRecord, ...],
    timeline: ActiveTimeline,
) -> tuple[float | None, MeasurementReason | None]:
    if not records:
        return None, MeasurementReason.NO_RECORDS
    points = [r for r in records if start <= r.timestamp <= end]
    if any(_native_total(r.distance_m) is None for r in points):
        return None, MeasurementReason.INVALID_DISTANCE_TRACE
    distances = [r.distance_m for r in points]
    if any(
        right is not None and left is not None and right < left
        for left, right in zip(distances, distances[1:], strict=False)
    ):
        return None, MeasurementReason.INVALID_DISTANCE_TRACE
    first = _distance_at(start, records, timeline)
    last = _distance_at(end, records, timeline)
    if first is None or last is None:
        return None, MeasurementReason.DISTANCE_BOUNDARY_UNAVAILABLE
    if last < first or any(
        d is not None and (d < first or d > last) for d in distances
    ):
        return None, MeasurementReason.INVALID_DISTANCE_TRACE
    return last - first, None


def _coverage(
    seconds: float, total: float | None, reason: MeasurementReason | None
) -> MeasurementCoverage:
    return MeasurementCoverage(seconds, total, reason)


class NativeLapReportBuilder:
    """Build one source-ordered row per recorded native lap."""

    def build(self, activity: Activity) -> tuple[NativeLapRow, ...]:
        records = chronological_records(activity.record_samples or activity.records)
        result: list[NativeLapRow] = []
        for position, lap in enumerate(activity.laps):
            issues: list[MeasurementIssue] = []
            boundary_issue = _lap_boundary_issue(lap, activity.laps, position)
            if boundary_issue is not None:
                issues.append(MeasurementIssue("lap", boundary_issue))
            elif not activity.active_timeline.is_known:
                issues.append(
                    MeasurementIssue(
                        "timing", MeasurementReason.ACTIVE_TIMING_UNAVAILABLE
                    )
                )
            safe = boundary_issue is None and activity.active_timeline.is_known
            start, end = lap.start_time, lap.end_time
            bounds = (
                ActiveInterval(start, end)
                if safe and start is not None and end is not None
                else None
            )
            derived_duration = (
                active_duration(activity.active_timeline, bounds.start, bounds.end)
                if bounds is not None
                else None
            )
            duration = _native_total(lap.total_timer_time_s)
            if duration is None:
                duration = derived_duration
            distance = _native_total(lap.total_distance_m)
            if distance is None and bounds is not None:
                distance, distance_issue = _derived_distance(
                    bounds.start, bounds.end, records, activity.active_timeline
                )
                if distance_issue is not None:
                    issues.append(MeasurementIssue("distance", distance_issue))
            hr = (
                hr_spans(records, activity.active_timeline, bounds)
                if bounds is not None
                else ()
            )
            cadence = (
                sampled_spans(
                    records,
                    activity.active_timeline,
                    lambda r: r.cadence_spm,
                    valid_cadence,
                    bounds,
                )
                if bounds is not None
                else ()
            )
            hr_seconds = sum(span.seconds for span in hr)
            cadence_seconds = sum(span.seconds for span in cadence)
            coverage_reason = boundary_issue or (
                MeasurementReason.ACTIVE_TIMING_UNAVAILABLE
                if not activity.active_timeline.is_known
                else None
            )
            hr_reason = coverage_reason or (
                MeasurementReason.NO_HR_COVERAGE if hr_seconds == 0 else None
            )
            cadence_reason = coverage_reason or (
                MeasurementReason.NO_CADENCE_COVERAGE if cadence_seconds == 0 else None
            )
            avg_hr = _native_hr(lap.avg_heart_rate_bpm)
            if avg_hr is None and hr_seconds:
                average = weighted_average(hr)
                assert average is not None
                avg_hr = round(average)
            max_hr = _native_hr(lap.max_heart_rate_bpm)
            if max_hr is None and bounds is not None:
                observations = [
                    _native_hr(r.heart_rate_bpm)
                    for r in records
                    if contains_lap_time(r.timestamp, bounds.start, bounds.end)
                    and valid_hr(r.heart_rate_bpm)
                    and any(
                        i.start <= r.timestamp < i.end
                        for i in activity.active_timeline.intervals
                    )
                ]
                max_hr = max(
                    (value for value in observations if value is not None), default=None
                )
            avg_cadence = _native_cadence(lap.avg_cadence_spm)
            if avg_cadence is None and cadence_seconds:
                average = weighted_average(cadence)
                assert average is not None
                avg_cadence = round(average)
            pace = (
                duration * 1000 / distance
                if distance is not None
                and distance > 0
                and duration is not None
                and duration > 0
                else None
            )
            speed = (
                distance * 3.6 / duration
                if pace is not None and distance is not None and duration is not None
                else None
            )
            result.append(
                NativeLapRow(
                    index=lap.index,
                    label=lap.label,
                    role=lap.role,
                    workout_step=lap.workout_step,
                    workout_issues=lap.workout_issues,
                    start_time=start,
                    end_time=end,
                    distance_m=distance,
                    active_duration_s=duration,
                    pace_seconds_per_km=pace,
                    speed_kmh=speed,
                    avg_heart_rate_bpm=avg_hr,
                    max_heart_rate_bpm=max_hr,
                    avg_cadence_spm=avg_cadence,
                    hr_coverage=_coverage(hr_seconds, duration, hr_reason),
                    cadence_coverage=_coverage(
                        cadence_seconds, duration, cadence_reason
                    ),
                    issues=tuple(issues),
                )
            )
        return tuple(result)
