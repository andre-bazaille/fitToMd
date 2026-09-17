from pathlib import Path


class LocalMarkdownReportWriter:
    def is_file(self, path: Path) -> bool:
        return path.is_file()

    def write(self, path: Path, markdown: str) -> None:
        path.write_text(markdown, encoding="utf-8")
