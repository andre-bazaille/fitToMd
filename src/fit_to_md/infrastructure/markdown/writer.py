import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


class LocalMarkdownReportWriter:
    def is_file(self, path: Path) -> bool:
        return path.is_file()

    def publish_if_absent(self, path: Path, markdown: str) -> bool:
        with TemporaryDirectory(dir=path.parent, prefix=".fit-to-md-") as directory:
            staged = Path(directory) / "report.tmp"
            staged.write_text(markdown, encoding="utf-8")
            try:
                if sys.platform == "win32":
                    staged.rename(path)
                else:
                    os.link(staged, path)
            except FileExistsError:
                if path.is_dir():
                    raise
                return False
        return True
