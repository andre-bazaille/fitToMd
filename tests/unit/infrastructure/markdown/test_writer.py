from pathlib import Path

import pytest

from fit_to_md.infrastructure.markdown.writer import LocalMarkdownReportWriter


def test_local_markdown_report_writer_checks_and_writes_files(tmp_path: Path) -> None:
    output = tmp_path / "report.md"
    writer = LocalMarkdownReportWriter()

    assert not writer.is_file(output)
    writer.write(output, "# report")

    assert writer.is_file(output)
    assert output.read_text(encoding="utf-8") == "# report"


def test_local_markdown_report_writer_propagates_write_errors(tmp_path: Path) -> None:
    output = tmp_path / "report.md"
    output.mkdir()

    with pytest.raises(OSError):
        LocalMarkdownReportWriter().write(output, "# report")
