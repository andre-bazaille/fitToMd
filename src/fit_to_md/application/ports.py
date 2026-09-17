from pathlib import Path
from typing import Protocol


class MarkdownReportWriter(Protocol):
    """Persistence boundary used by batch report generation."""

    def is_file(self, path: Path) -> bool: ...

    def write(self, path: Path, markdown: str) -> None: ...
