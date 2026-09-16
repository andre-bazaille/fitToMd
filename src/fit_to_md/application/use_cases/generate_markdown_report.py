from pathlib import Path

from fit_to_md.domain.reporting.entities import FitReport
from fit_to_md.domain.reporting.ports import ActivityExtractor, ReportRenderer


class GenerateMarkdownReport:
    def __init__(self, extractor: ActivityExtractor, renderer: ReportRenderer) -> None:
        self._extractor = extractor
        self._renderer = renderer

    def execute(self, source: Path) -> str:
        _, markdown = self.execute_with_report(source)
        return markdown

    def execute_with_report(self, source: Path) -> tuple[FitReport, str]:
        report = self._extractor.extract(source)
        return report, self._renderer.render(report)
