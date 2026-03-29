from __future__ import annotations

from datetime import datetime

from fit_to_md.domain.reporting.entities import FitReport, Split, TransitionDynamics, TransitionSample


class MarkdownReportRenderer:
    def render(self, report: FitReport) -> str:
        lines: list[str] = []
        lines.append(self._render_title(report))
        lines.append("")
        lines.append("## Session Summary")
        lines.extend(self._render_summary(report))
        lines.append("")
        lines.append("## Kilometric Splits")
        lines.extend(self._render_splits(report))
        lines.append("")
        lines.append("## Heart Rate Dynamics (Recovery & Ramp)")
        lines.extend(self._render_transitions(report))
        return "\n".join(lines).rstrip() + "\n"

    def _render_title(self, report: FitReport) -> str:
        summary = report.summary
        date_label = _format_date(summary.start_time)
        activity_label = summary.activity_name or summary.activity_type or "Activity"
        return f"# FIT Report: {date_label} {activity_label}".rstrip()

    def _render_summary(self, report: FitReport) -> list[str]:
        summary = report.summary
        return [
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
            f"- **Avg Speed:** {_format_speed(summary.avg_speed_kmh)}",
        ]

    def _render_splits(self, report: FitReport) -> list[str]:
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

    def _render_transitions(self, report: FitReport) -> list[str]:
        if not report.transitions:
            return ["No transition samples available."]

        lines: list[str] = []
        for transition in report.transitions:
            lines.extend(self._render_transition(transition))
        return lines

    def _render_transition(self, transition: TransitionDynamics) -> list[str]:
        lines = [f"- **Transition: {transition.label}**"]
        for sample in transition.samples:
            lines.append(self._render_transition_sample(sample))
        return lines

    def _render_transition_sample(self, sample: TransitionSample) -> str:
        return (
            f"  - T+{sample.offset_seconds}s: {_format_integer(sample.heart_rate_bpm)} bpm "
            f"(Speed: {_format_speed(sample.speed_kmh)}, Grade: {_format_grade(sample.grade_percent)})"
        )


def _format_date(value: datetime | None) -> str:
    if value is None:
        return "unknown-date"
    return value.strftime("%Y-%m-%d")


def _format_distance(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f} km"


def _format_speed(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f} km/h"


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
