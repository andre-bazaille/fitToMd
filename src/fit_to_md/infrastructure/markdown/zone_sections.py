"""Presentation of configured heart-rate zones and measurement coverage."""

from collections.abc import Sequence
from math import isfinite

from fit_to_md.domain.reporting.entities import (
    FitReport,
    MeasurementReason,
    ZoneMeasurement,
)
from fit_to_md.infrastructure.markdown.sections import _format_duration

_ZONE_NOTES = {
    MeasurementReason.ACTIVE_TIMING_UNAVAILABLE: "Active timing unavailable; zone durations cannot be measured",
    MeasurementReason.MISSING_BOUNDARIES: "Lap timestamps missing; per-lap zones unavailable",
    MeasurementReason.REVERSED_BOUNDARIES: "Lap timestamps reversed; per-lap zones unavailable",
    MeasurementReason.OVERLAPPING_LAPS: "Overlapping lap boundaries; per-lap zones unavailable",
    MeasurementReason.NO_HR_COVERAGE: "No covered HR samples; zone percentages unavailable",
    MeasurementReason.TOTAL_ACTIVE_TIME_UNAVAILABLE: "Total active time unavailable; coverage percentage and unknown time unavailable",
    MeasurementReason.INCONSISTENT_TIMING: "Covered HR time exceeds native active time by more than one second; coverage percentage and unknown time unavailable",
}


def _format_zone_seconds(value: float | None) -> str:
    if value is None or not isfinite(value) or value < 0:
        return "-"
    if value == 0:
        return "0:00"
    rounded = round(value, 1)
    if rounded == 0:
        return "<0.1 s"
    if rounded.is_integer():
        return _format_duration(rounded)
    hours, remainder = divmod(rounded, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{int(hours)}:{int(minutes):02d}:{seconds:04.1f}"
    return f"{int(minutes)}:{seconds:04.1f}"


def _format_percentage(value: float | None) -> str:
    return f"{value:.1f}%" if value is not None else "-"


def _zone_ranges(thresholds: tuple[int, int, int, int]) -> tuple[str, ...]:
    first, second, third, fourth = thresholds
    return (
        f"<{first}",
        f"{first}–<{second}",
        f"{second}–<{third}",
        f"{third}–<{fourth}",
        f"≥{fourth}",
    )


def _measurement_notes(measurement: ZoneMeasurement) -> list[str]:
    reasons = list(measurement.issues)
    if (
        measurement.coverage.reason is not None
        and measurement.coverage.reason not in reasons
    ):
        reasons.append(measurement.coverage.reason)
    return [_ZONE_NOTES[reason] for reason in reasons if reason in _ZONE_NOTES]


def _zone_cell(measurement: ZoneMeasurement, zone_index: int) -> str:
    if measurement.zone_seconds is None:
        return "-"
    seconds = _format_zone_seconds(measurement.zone_seconds[zone_index])
    percentage = (
        measurement.zone_percentages[zone_index]
        if measurement.zone_percentages is not None
        else None
    )
    return f"{seconds} ({_format_percentage(percentage)})"


class HeartRateZonesSectionRenderer:
    heading = "Heart-Rate Zones"

    def is_enabled(self, report: FitReport) -> bool:
        return report.zones is not None

    def render_lines(self, report: FitReport) -> Sequence[str]:
        zones = report.zones
        assert zones is not None
        ranges = _zone_ranges(zones.boundaries.thresholds_bpm)
        boundary_summary = "; ".join(
            f"Z{index + 1} {hr_range} bpm" for index, hr_range in enumerate(ranges)
        )
        lines = [
            "",
            f"**Configured boundaries:** {boundary_summary}.",
            "HR coverage uses the earlier valid sample until the next record, capped at five wall-clock seconds and clipped to active time. Missing HR and pauses are uncovered.",
            "Zone percentages use covered HR time as their denominator; coverage percentage uses total active time.",
            "",
            "**Session zones**",
            "| Zone | HR range | Time | % of covered HR time |",
            "|---|---|---|---|",
        ]
        for index, hr_range in enumerate(ranges):
            seconds = (
                zones.session.zone_seconds[index]
                if zones.session.zone_seconds is not None
                else None
            )
            percentage = (
                zones.session.zone_percentages[index]
                if zones.session.zone_percentages is not None
                else None
            )
            lines.append(
                f"| Z{index + 1} | {hr_range} bpm | "
                f"{_format_zone_seconds(seconds)} | {_format_percentage(percentage)} |"
            )
        coverage = zones.session.coverage
        lines.extend(
            (
                "",
                "**Session HR coverage:** "
                f"{_format_zone_seconds(coverage.covered_seconds)} covered / "
                f"{_format_zone_seconds(coverage.total_active_seconds)} active "
                f"({_format_percentage(zones.session.coverage_percent)}); "
                f"unknown active time {_format_zone_seconds(zones.session.unknown_seconds)}.",
            )
        )
        for note in _measurement_notes(zones.session):
            lines.append(f"- {note}.")
        if zones.laps:
            lines.extend(
                (
                    "",
                    "**Per-lap zones**",
                    "| Lap | Z1 | Z2 | Z3 | Z4 | Z5 | HR coverage | Note |",
                    "|---|---|---|---|---|---|---|---|",
                )
            )
            for lap in zones.laps:
                measurement = lap.measurement
                zone_cells = " | ".join(_zone_cell(measurement, i) for i in range(5))
                notes = "; ".join(_measurement_notes(measurement)) or "-"
                lines.append(
                    f"| {lap.lap_index} | {zone_cells} | "
                    f"{_format_zone_seconds(measurement.coverage.covered_seconds)} / "
                    f"{_format_zone_seconds(measurement.coverage.total_active_seconds)} "
                    f"({_format_percentage(measurement.coverage_percent)}); "
                    f"unknown {_format_zone_seconds(measurement.unknown_seconds)} | {notes} |"
                )
        return lines
