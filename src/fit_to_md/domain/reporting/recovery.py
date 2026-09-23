"""Signed HR change during the first 60 active seconds of recorded recoveries."""

from dataclasses import replace
from datetime import datetime
from math import isfinite

from fit_to_md.domain.activity.entities import Activity, ActivityRecord
from fit_to_md.domain.activity.timeline import ActiveInterval, ActiveTimeline
from fit_to_md.domain.activity.workout import LapRole
from fit_to_md.domain.reporting.entities import (
    NativeLapRow,
    RecoveryAnalysis,
    RecoveryChange,
    RecoveryReason,
)
from fit_to_md.domain.reporting.measurements import (
    active_duration,
    chronological_records,
    valid_hr,
    wall_time_at_active_offset,
)
from fit_to_md.domain.reporting.workout import NativeLapReportBuilder

_MAX_ENDPOINT_GAP_S = 5.0
_TARGET_ACTIVE_OFFSET_S = 60.0


def _valid_native_duration(value: float | None) -> float | None:
    if value is None or isinstance(value, bool) or not isfinite(value) or value < 0:
        return None
    return value


def _unsafe_boundary(row: NativeLapRow) -> bool:
    return any(issue.metric == "lap" for issue in row.issues)


def _target_interval(
    timeline: ActiveTimeline, target: datetime, *, at_start: bool
) -> ActiveInterval | None:
    if at_start:
        return next(
            (span for span in timeline.intervals if span.start <= target < span.end),
            None,
        )
    # A positive active offset exactly at a stop belongs to the preceding span.
    return next(
        (span for span in timeline.intervals if span.start < target <= span.end),
        None,
    ) or next(
        (span for span in timeline.intervals if span.start <= target < span.end),
        None,
    )


def _nearest_observation(
    records: tuple[ActivityRecord, ...],
    target: datetime,
    active_interval: ActiveInterval,
    lap_start: datetime,
    lap_end: datetime,
    *,
    allow_lap_end: bool,
) -> ActivityRecord | None:
    candidates = (
        record
        for record in records
        if valid_hr(record.heart_rate_bpm)
        and lap_start <= record.timestamp
        and (
            record.timestamp < lap_end
            or (allow_lap_end and record.timestamp == lap_end)
        )
        and active_interval.start <= record.timestamp <= active_interval.end
        and abs((record.timestamp - target).total_seconds()) <= _MAX_ENDPOINT_GAP_S
    )
    return min(
        candidates,
        key=lambda record: (
            abs((record.timestamp - target).total_seconds()),
            record.timestamp,
        ),
        default=None,
    )


class RecoveryAnalysisBuilder:
    """Measure endpoints only when lap order, timing, and observations support them."""

    def build(self, activity: Activity) -> RecoveryAnalysis:
        rows = NativeLapReportBuilder().build(activity)
        records = chronological_records(activity.record_samples or activity.records)
        timeline = activity.active_timeline
        changes: list[RecoveryChange] = []
        for position, row in enumerate(rows):
            if row.role != LapRole.RECOVERY:
                continue
            previous = rows[position - 1] if position else None
            result = RecoveryChange(
                source_position=position,
                lap_index=row.index,
                preceding_lap_index=previous.index if previous is not None else None,
            )
            if previous is None or previous.role != LapRole.WORK:
                changes.append(
                    replace(result, reason=RecoveryReason.NOT_PRECEDED_BY_WORK)
                )
                continue
            start, end = row.start_time, row.end_time
            if (
                _unsafe_boundary(previous)
                or _unsafe_boundary(row)
                or previous.end_time is None
                or start is None
                or end is None
                or previous.end_time > start
            ):
                changes.append(
                    replace(result, reason=RecoveryReason.AMBIGUOUS_LAP_TIMING)
                )
                continue
            if not timeline.is_known:
                changes.append(
                    replace(result, reason=RecoveryReason.ACTIVE_TIMING_UNAVAILABLE)
                )
                continue
            seconds = active_duration(timeline, start, end)
            assert seconds is not None
            native_seconds = _valid_native_duration(
                activity.laps[position].total_timer_time_s
            )
            if native_seconds is not None and abs(native_seconds - seconds) > 1:
                changes.append(
                    replace(result, reason=RecoveryReason.INCONSISTENT_DURATION)
                )
                continue
            if seconds < _TARGET_ACTIVE_OFFSET_S:
                changes.append(
                    replace(result, reason=RecoveryReason.UNDER_60_ACTIVE_SECONDS)
                )
                continue
            start_target = wall_time_at_active_offset(timeline, start, end, 0)
            sixty_target = wall_time_at_active_offset(
                timeline, start, end, _TARGET_ACTIVE_OFFSET_S
            )
            result = replace(
                result,
                start_target_time=start_target,
                sixty_second_target_time=sixty_target,
            )
            if start_target is None:
                changes.append(
                    replace(result, reason=RecoveryReason.START_TARGET_UNAVAILABLE)
                )
                continue
            if sixty_target is None:
                changes.append(
                    replace(
                        result, reason=RecoveryReason.SIXTY_SECOND_TARGET_UNAVAILABLE
                    )
                )
                continue
            start_interval = _target_interval(timeline, start_target, at_start=True)
            sixty_interval = _target_interval(timeline, sixty_target, at_start=False)
            if start_interval is None or sixty_interval is None:
                changes.append(
                    replace(result, reason=RecoveryReason.AMBIGUOUS_LAP_TIMING)
                )
                continue
            start_observation = _nearest_observation(
                records, start_target, start_interval, start, end, allow_lap_end=False
            )
            if start_observation is None:
                changes.append(
                    replace(result, reason=RecoveryReason.START_HR_UNAVAILABLE)
                )
                continue
            assert start_observation.heart_rate_bpm is not None
            start_hr = float(start_observation.heart_rate_bpm)
            result = replace(
                result,
                start_hr_bpm=start_hr,
                start_observation_time=start_observation.timestamp,
                start_observation_offset_s=(
                    start_observation.timestamp - start_target
                ).total_seconds(),
            )
            sixty_observation = _nearest_observation(
                records, sixty_target, sixty_interval, start, end, allow_lap_end=True
            )
            if sixty_observation is None:
                changes.append(
                    replace(result, reason=RecoveryReason.SIXTY_SECOND_HR_UNAVAILABLE)
                )
                continue
            assert sixty_observation.heart_rate_bpm is not None
            sixty_hr = float(sixty_observation.heart_rate_bpm)
            changes.append(
                replace(
                    result,
                    hr_60_bpm=sixty_hr,
                    sixty_second_observation_time=sixty_observation.timestamp,
                    sixty_second_observation_offset_s=(
                        sixty_observation.timestamp - sixty_target
                    ).total_seconds(),
                    delta_bpm=sixty_hr - start_hr,
                )
            )
        return RecoveryAnalysis(tuple(changes))
