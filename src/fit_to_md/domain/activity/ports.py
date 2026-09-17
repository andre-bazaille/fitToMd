from pathlib import Path
from typing import Protocol

from fit_to_md.domain.activity.entities import Activity


class ActivityReader(Protocol):
    def read(self, source: Path) -> Activity: ...
