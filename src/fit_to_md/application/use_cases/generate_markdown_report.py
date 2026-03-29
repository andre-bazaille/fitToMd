from __future__ import annotations

from pathlib import Path

from fit_to_md.domain.reporting.ports import ActivityExtractor, ReportRenderer


class GenerateMarkdownReport:
    def __init__(self, extractor: ActivityExtractor, renderer: ReportRenderer) -> None:
        self._extractor = extractor
        self._renderer = renderer

    def execute(self, source: Path) -> str:
        report = self._extractor.extract(source)
        return self._renderer.render(report)
