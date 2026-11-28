import os
import sys
from pathlib import Path

import pytest

from fit_to_md.infrastructure.markdown.writer import LocalMarkdownReportWriter


def test_local_markdown_report_writer_publishes_complete_utf8_report(
    tmp_path: Path,
) -> None:
    output = tmp_path / "report.md"
    writer = LocalMarkdownReportWriter()
    markdown = "# Séance\n\nDistance parcourue\n"

    assert not writer.is_file(output)
    assert writer.publish_if_absent(output, markdown)

    assert writer.is_file(output)
    assert output.read_text(encoding="utf-8") == markdown
    assert set(tmp_path.iterdir()) == {output}


def test_local_markdown_report_writer_cleans_up_after_publication_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "report.md"
    markdown = "# report\n\nFinished report\n"

    def fail_publication(source: Path, destination: Path) -> None:
        assert source.parent.parent == output.parent
        assert source.read_text(encoding="utf-8") == markdown
        assert destination == output
        assert not destination.exists()
        raise PermissionError("Publication denied")

    operation = "rename" if sys.platform == "win32" else "link"
    monkeypatch.setattr(os, operation, fail_publication)

    with pytest.raises(PermissionError, match="Publication denied"):
        LocalMarkdownReportWriter().publish_if_absent(output, markdown)

    assert list(tmp_path.iterdir()) == []
