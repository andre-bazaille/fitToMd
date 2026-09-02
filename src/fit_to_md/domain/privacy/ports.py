from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from fit_to_md.domain.privacy.entities import FitSanitizationPolicy


@dataclass(frozen=True)
class SanitizationSummary:
    removed_messages: int
    removed_fields: int
    shifted_timestamps: int


class FitFileSanitizer(Protocol):
    def sanitize(
        self,
        source: Path,
        destination: Path,
        target_start: datetime,
        policy: FitSanitizationPolicy,
    ) -> SanitizationSummary: ...
