from pathlib import Path
from typing import Protocol

from fit_to_md.domain.activity.entities import Activity


class ActivityReadError(Exception):
    """Base error exposed by activity-reader adapters."""


class InvalidActivityError(ActivityReadError):
    """The source cannot be decoded as a valid activity."""


class UnsupportedActivityError(ActivityReadError):
    """The source is valid but uses an unsupported activity shape."""


class ActivityReader(Protocol):
    def read(self, source: Path) -> Activity: ...
