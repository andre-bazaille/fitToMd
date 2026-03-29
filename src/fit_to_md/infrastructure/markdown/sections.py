from __future__ import annotations

from datetime import datetime
from typing import Protocol, Sequence

from fit_to_md.domain.reporting.entities import FitReport, Split, TransitionDynamics, TransitionSample


class ReportSectionRenderer(Protocol):
    heading: str

    def render_lines(self, report: FitReport) -> Sequence[str]:
        ...


class SessionSummarySectionRenderer:
    heading = "Session Summary"

    def render_lines(self, report: FitReport) -> Sequence[str]:
        summary = report.summary
        speed_label = "Avg Pace" if _uses_pace(summary.activity_type) else "Avg Speed"
        return [
            f"- **Start Time:** {_format_datetime(summary.start_time)}",
            f"- **Activity Type:** {summary.activity_type or '-'}",
            f"- **Total Distance:** {_format_distance(summary.total_distance_km)}",
            f"- **Total Time:** {_format_duration(summary.total_timer_time_s)}",
            f"- **Elapsed Time:** {_format_duration(summary.total_elapsed_time_s)}",
            (
                "- **Elevation Gain/Loss:** "
                f"{_format_elevation(summary.total_ascent_m, positive_hint=True)} / "
                f"{_format_elevation(summary.total_descent_m, positive_hint=False)}"
            ),
            (
                "- **Avg/Max HR:** "
                f"{_format_integer(summary.avg_heart_rate_bpm)} / "
                f"{_format_integer(summary.max_heart_rate_bpm)} bpm"
            ),
            f"- **Avg Cadence:** {_format_integer(summary.avg_cadence_spm)} spm",
            f"- **{speed_label}:** {_format_speed_metric(summary.avg_speed_kmh, summary.activity_type)}",
            f"- **Weather:** {_format_weather(summary)}",
        ]


class SplitSectionRenderer:
    heading = "Kilometric Splits"

    def render_lines(self, report: FitReport) -> Sequence[str]:
        if not report.splits:
            return ["No split data available."]

        lines = [
            "| Km | Time | Pace | Elev +/- | Avg HR | Max HR | Avg Cad |",
            "|---|---|---|---|---|---|---|",
        ]
        for split in report.splits:
            lines.append(self._render_split_row(split))
        return lines

    def _render_split_row(self, split: Split) -> str:
        return (
            f"| {split.kilometer} | {_format_duration(split.time_seconds)} | "
            f"{_format_duration(split.pace_seconds_per_km)} | "
            f"{_format_signed_metric(split.elevation_delta_m, 'm')} | "
            f"{_format_integer(split.avg_heart_rate_bpm)} | "
            f"{_format_integer(split.max_heart_rate_bpm)} | "
            f"{_format_integer(split.avg_cadence_spm)} |"
        )


class TransitionSectionRenderer:
    heading = "Heart Rate Dynamics (Recovery & Ramp)"

    def render_lines(self, report: FitReport) -> Sequence[str]:
        if not report.transitions:
            return ["No transition samples available."]

        lines: list[str] = []
        for transition in report.transitions:
            lines.extend(self._render_transition(transition, report.summary.activity_type))
        return lines

    def _render_transition(self, transition: TransitionDynamics, report_activity_type: str | None) -> list[str]:
        lines = [f"- **Transition: {transition.label}**"]
        for sample in transition.samples:
            lines.append(self._render_transition_sample(sample, report_activity_type))
        return lines

    def _render_transition_sample(self, sample: TransitionSample, report_activity_type: str | None) -> str:
        speed_label = "Pace" if _uses_pace(report_activity_type) else "Speed"
        return (
            f"  - {_format_offset(sample.offset_seconds)}: {_format_integer(sample.heart_rate_bpm)} bpm "
            f"({speed_label}: {_format_speed_metric(sample.speed_kmh, report_activity_type)}, "
            f"Grade: {_format_grade(sample.grade_percent)})"
        )


def _format_datetime(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.isoformat(sep=" ", timespec="seconds")


def _format_distance(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f} km"


def _format_speed_metric(value: float | None, activity_type: str | None) -> str:
    if _uses_pace(activity_type):
        return _format_pace(value)
    return _format_speed(value)


def _format_speed(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f} km/h"


def _format_pace(speed_kmh: float | None) -> str:
    if speed_kmh is None or speed_kmh <= 0:
        return "-"
    seconds_per_km = 3600 / speed_kmh
    return f"{_format_duration(seconds_per_km)}/km"


def _uses_pace(activity_type: str | None) -> bool:
    if activity_type is None:
        return False
    normalized_activity_type = activity_type.strip().casefold()
    return "run" in normalized_activity_type


def _format_weather(summary) -> str:
    if summary.weather is not None:
        parts = [
            _format_temperature(summary.weather.temperature_c),
        ]
        if summary.weather.apparent_temperature_c is not None:
            parts.append(f"feels like {_format_temperature(summary.weather.apparent_temperature_c)}")
        if summary.weather.condition_summary:
            parts.append(summary.weather.condition_summary)
        wind_label = _format_wind(summary.weather.wind_speed_kmh, summary.weather.wind_direction_label)
        if wind_label is not None:
            parts.append(wind_label)
        return ", ".join(parts) + f" [{summary.weather.source}]"

    if summary.avg_temperature_c is None and summary.min_temperature_c is None and summary.max_temperature_c is None:
        return "FIT and historical weather data unavailable"
    return (
        f"Avg {_format_temperature(summary.avg_temperature_c)} / "
        f"Min {_format_temperature(summary.min_temperature_c)} / "
        f"Max {_format_temperature(summary.max_temperature_c)} [fit]"
    )


def _format_temperature(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.1f}C"


def _format_wind(speed_kmh: float | None, direction_label: str | None) -> str | None:
    if speed_kmh is None and direction_label is None:
        return None
    if speed_kmh is None:
        return f"Wind {direction_label}"
    if direction_label is None:
        return f"Wind {speed_kmh:.1f} km/h"
    return f"Wind {speed_kmh:.1f} km/h {direction_label}"


def _format_grade(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}%"


def _format_elevation(value: float | None, positive_hint: bool) -> str:
    if value is None:
        return "-"
    sign = "+" if positive_hint else "-"
    return f"{sign}{abs(value):.0f}m"


def _format_signed_metric(value: float | None, unit: str) -> str:
    if value is None:
        return "-"
    prefix = "+" if value >= 0 else "-"
    return f"{prefix}{abs(value):.0f}{unit}"


def _format_integer(value: int | None) -> str:
    if value is None:
        return "-"
    return str(value)


def _format_duration(value: float | None) -> str:
    if value is None:
        return "-"

    total_seconds = int(round(value))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _format_offset(value: int) -> str:
    if value == 0:
        return "T+0s"
    prefix = "+" if value > 0 else "-"
    return f"T{prefix}{abs(value)}s"
