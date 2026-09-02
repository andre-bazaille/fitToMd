from collections.abc import Sequence
from datetime import datetime

from fit_to_md.domain.reporting.entities import FitReport
from fit_to_md.infrastructure.markdown.sections import (
    ReportSectionRenderer,
    SessionSummarySectionRenderer,
    SplitSectionRenderer,
    TransitionSectionRenderer,
)


class MarkdownReportRenderer:
    def __init__(
        self, section_renderers: Sequence[ReportSectionRenderer] | None = None
    ) -> None:
        self._section_renderers = tuple(
            section_renderers
            or (
                SessionSummarySectionRenderer(),
                SplitSectionRenderer(),
                TransitionSectionRenderer(),
            )
        )

    def render(self, report: FitReport) -> str:
        lines = [self._render_title(report)]
        for section_renderer in self._section_renderers:
            lines.append("")
            lines.append(f"## {section_renderer.heading}")
            lines.extend(section_renderer.render_lines(report))
        return "\n".join(lines).rstrip() + "\n"

    def _render_title(self, report: FitReport) -> str:
        summary = report.summary
        date_label = _format_date(summary.start_time)
        activity_label = summary.activity_name or summary.activity_type or "Activity"
        return f"# FIT Report: {date_label} {activity_label}".rstrip()


def _format_date(value: datetime | None) -> str:
    if value is None:
        return "unknown-date"
    return value.strftime("%Y-%m-%d")
