from __future__ import annotations

from pathlib import Path

from fit_to_md.domain.reporting.entities import FitReport


class FitdecodeActivityExtractor:
    def extract(self, source: Path) -> FitReport:
        raise NotImplementedError(
            "FIT decoding has not been implemented yet. "
            f"Project scaffold is ready for parsing logic: {source}"
        )
