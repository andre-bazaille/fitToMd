from datetime import datetime
from pathlib import Path

from fit_to_md.domain.privacy import (
    FitFileSanitizer,
    FitSanitizationPolicy,
    SanitizationSummary,
)


class SanitizeFitFixture:
    def __init__(self, sanitizer: FitFileSanitizer) -> None:
        self._sanitizer = sanitizer

    def execute(
        self,
        source: Path,
        destination: Path,
        target_start: datetime,
        policy: FitSanitizationPolicy | None = None,
    ) -> SanitizationSummary:
        if source.resolve() == destination.resolve():
            raise ValueError("The sanitized FIT file must use a different path.")
        return self._sanitizer.sanitize(
            source,
            destination,
            target_start,
            policy or FitSanitizationPolicy(),
        )
