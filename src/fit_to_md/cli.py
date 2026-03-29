from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence, TextIO

from fit_to_md.application.use_cases.generate_markdown_report import GenerateMarkdownReport
from fit_to_md.infrastructure.fitdecode.extractor import FitdecodeActivityExtractor
from fit_to_md.infrastructure.fitdecode.builders import TransitionBuilder
from fit_to_md.infrastructure.markdown.renderer import MarkdownReportRenderer


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


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
    parser.add_argument(
        "--transition-sample-interval",
        type=_positive_int,
        default=10,
        help="Sampling interval in seconds for transition dynamics output.",
    )
    parser.add_argument(
        "--transition-window",
        type=_positive_int,
        default=60,
        help="Seconds captured before and after each lap transition.",
    )
    return parser


def build_default_generator(
    transition_sample_interval: int = 10,
    transition_window: int = 60,
) -> GenerateMarkdownReport:
    extractor = FitdecodeActivityExtractor(
        transition_builder=TransitionBuilder(
            sample_interval_s=transition_sample_interval,
            window_s=transition_window,
        )
    )
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

    generator = report_generator or build_default_generator(
        transition_sample_interval=args.transition_sample_interval,
        transition_window=args.transition_window,
    )

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
