from pathlib import Path
from typing import Protocol


class MarkdownReportWriter(Protocol):
    """Persistence boundary used by batch report generation."""

    def is_file(self, path: Path) -> bool: ...

    def publish_if_absent(self, path: Path, markdown: str) -> bool:
        """Atomically publish a complete report, returning False for an existing file."""
        ...
