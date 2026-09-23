"""Pure heart-rate zone durations and coverage for activity and native-lap scopes."""

from bisect import bisect_right
from dataclasses import dataclass
from math import isfinite

from fit_to_md.domain.activity.entities import Activity
from fit_to_md.domain.activity.timeline import ActiveInterval
from fit_to_md.domain.reporting.entities import (
    HeartRateZoneReport,
    LapZoneMeasurement,
    MeasurementCoverage,
    MeasurementReason,
    ZoneMeasurement,
    ZonePercentages,
    ZoneSeconds,
)
from fit_to_md.domain.reporting.measurements import ValueSpan, hr_spans
from fit_to_md.domain.reporting.workout import NativeLapReportBuilder


@dataclass(frozen=True)
class HeartRateZoneBoundaries:
    """Four BPM thresholds defining five zones; equality enters the higher zone."""

    thresholds_bpm: tuple[int, int, int, int]

    def __post_init__(self) -> None:
        values = self.thresholds_bpm
        if (
            not isinstance(values, tuple)
            or len(values) != 4
            or any(type(value) is not int or value <= 0 for value in values)
            or any(
                right <= left for left, right in zip(values, values[1:], strict=False)
            )
        ):
            raise ValueError(
                "Zone boundaries must be four positive, strictly increasing integers"
            )

    def zone_index(self, heart_rate_bpm: float) -> int:
        """Return the zero-based zone index for a positive finite HR value."""
        if (
            isinstance(heart_rate_bpm, bool)
            or not isinstance(heart_rate_bpm, (int, float))
            or not isfinite(heart_rate_bpm)
            or heart_rate_bpm <= 0
        ):
            raise ValueError("Heart rate must be finite and positive")
        return bisect_right(self.thresholds_bpm, heart_rate_bpm)


def _native_total(value: float | None) -> float | None:
    if value is None or isinstance(value, bool) or not isfinite(value) or value < 0:
        return None
    return value


def summarize_zone_spans(
    boundaries: HeartRateZoneBoundaries,
    spans: tuple[ValueSpan, ...],
    total_active_seconds: float | None,
    unavailable: MeasurementReason | None = None,
) -> ZoneMeasurement:
    """Summarize covered spans without rounding or filling unknown HR time."""
    if total_active_seconds is not None and (
        isinstance(total_active_seconds, bool)
        or not isinstance(total_active_seconds, (int, float))
        or not isfinite(total_active_seconds)
        or total_active_seconds < 0
    ):
        raise ValueError("Total active time must be finite and nonnegative")
    if unavailable is not None:
        return ZoneMeasurement(
            zone_seconds=None,
            zone_percentages=None,
            coverage=MeasurementCoverage(None, total_active_seconds, unavailable),
            coverage_percent=None,
            unknown_seconds=None,
            issues=(unavailable,),
        )

    durations = [0.0] * 5
    for span in spans:
        durations[boundaries.zone_index(span.value)] += span.seconds
    zone_seconds: ZoneSeconds = (
        durations[0],
        durations[1],
        durations[2],
        durations[3],
        durations[4],
    )
    covered = sum(zone_seconds)
    zone_percentages: ZonePercentages = (
        (
            zone_seconds[0] * 100 / covered,
            zone_seconds[1] * 100 / covered,
            zone_seconds[2] * 100 / covered,
            zone_seconds[3] * 100 / covered,
            zone_seconds[4] * 100 / covered,
        )
        if covered > 0
        else (None, None, None, None, None)
    )
    issues: list[MeasurementReason] = []
    if covered == 0:
        issues.append(MeasurementReason.NO_HR_COVERAGE)
    if total_active_seconds is None:
        issues.append(MeasurementReason.TOTAL_ACTIVE_TIME_UNAVAILABLE)
        coverage_percent = None
        unknown_seconds = None
    elif covered > total_active_seconds + 1:
        issues.append(MeasurementReason.INCONSISTENT_TIMING)
        coverage_percent = None
        unknown_seconds = None
    else:
        coverage_percent = (
            min(covered * 100 / total_active_seconds, 100.0)
            if total_active_seconds > 0
            else (100.0 if covered > 0 else None)
        )
        unknown_seconds = max(total_active_seconds - covered, 0.0)
    return ZoneMeasurement(
        zone_seconds=zone_seconds,
        zone_percentages=zone_percentages,
        coverage=MeasurementCoverage(
            covered,
            total_active_seconds,
            issues[0] if issues else None,
        ),
        coverage_percent=coverage_percent,
        unknown_seconds=unknown_seconds,
        issues=tuple(issues),
    )


class HeartRateZoneBuilder:
    """Build session zones independently and optional per-native-lap zones."""

    def __init__(self, boundaries: HeartRateZoneBoundaries) -> None:
        if not isinstance(boundaries, HeartRateZoneBoundaries):
            raise TypeError("boundaries must be HeartRateZoneBoundaries")
        self._boundaries = boundaries

    def build(
        self, activity: Activity, *, include_laps: bool = True
    ) -> HeartRateZoneReport:
        timeline = activity.active_timeline
        native_session_total = _native_total(activity.session.total_timer_time_s)
        session_total = native_session_total
        if session_total is None and timeline.is_known:
            session_total = sum(
                (interval.end - interval.start).total_seconds()
                for interval in timeline.intervals
            )
        session = summarize_zone_spans(
            self._boundaries,
            hr_spans(activity.record_samples or activity.records, timeline),
            session_total,
            None if timeline.is_known else MeasurementReason.ACTIVE_TIMING_UNAVAILABLE,
        )
        laps: list[LapZoneMeasurement] = []
        if include_laps:
            for position, row in enumerate(NativeLapReportBuilder().build(activity)):
                boundary_issue = next(
                    (issue.reason for issue in row.issues if issue.metric == "lap"),
                    None,
                )
                unavailable = boundary_issue or (
                    MeasurementReason.ACTIVE_TIMING_UNAVAILABLE
                    if not timeline.is_known
                    else None
                )
                bounds = (
                    ActiveInterval(row.start_time, row.end_time)
                    if unavailable is None
                    and row.start_time is not None
                    and row.end_time is not None
                    else None
                )
                laps.append(
                    LapZoneMeasurement(
                        source_position=position,
                        lap_index=row.index,
                        measurement=summarize_zone_spans(
                            self._boundaries,
                            hr_spans(
                                activity.record_samples or activity.records,
                                timeline,
                                bounds,
                            )
                            if bounds is not None
                            else (),
                            row.active_duration_s,
                            unavailable,
                        ),
                    )
                )
        return HeartRateZoneReport(self._boundaries, session, tuple(laps))
