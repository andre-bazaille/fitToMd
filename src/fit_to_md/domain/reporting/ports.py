from __future__ import annotations

from pathlib import Path
from typing import Protocol

from fit_to_md.domain.reporting.entities import FitReport


class ActivityExtractor(Protocol):
    def extract(self, source: Path) -> FitReport:
        ...


class ReportRenderer(Protocol):
    def render(self, report: FitReport) -> str:
        ...
