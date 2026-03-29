from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence, TextIO

from fit_to_md.application.use_cases.generate_markdown_report import GenerateMarkdownReport
from fit_to_md.infrastructure.fitdecode.extractor import FitdecodeActivityExtractor
from fit_to_md.infrastructure.markdown.renderer import MarkdownReportRenderer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fit-to-md",
        description="Convert a FIT activity file into a Markdown report.",
    )
    parser.add_argument("input", type=Path, help="Path to the FIT file to parse.")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Optional path for the generated Markdown file.",
    )
    return parser


def build_default_generator() -> GenerateMarkdownReport:
    extractor = FitdecodeActivityExtractor()
    renderer = MarkdownReportRenderer()
    return GenerateMarkdownReport(extractor=extractor, renderer=renderer)


def run(
    argv: Optional[Sequence[str]] = None,
    report_generator: Optional[GenerateMarkdownReport] = None,
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    input_path = args.input

    if not input_path.exists():
        print(f"Input file not found: {input_path}", file=stderr)
        return 2

    generator = report_generator or build_default_generator()

    try:
        markdown = generator.execute(input_path)
    except NotImplementedError as error:
        print(str(error), file=stderr)
        return 1

    if args.output is None:
        stdout.write(markdown)
        if not markdown.endswith("\n"):
            stdout.write("\n")
        return 0

    args.output.write_text(markdown, encoding="utf-8")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    return run(argv=argv)
