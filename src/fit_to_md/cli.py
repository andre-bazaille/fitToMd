from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence, TextIO

from fit_to_md.application.use_cases.generate_markdown_report import GenerateMarkdownReport
from fit_to_md.infrastructure.fitdecode.extractor import FitdecodeActivityExtractor
from fit_to_md.infrastructure.fitdecode.builders import SessionSummaryBuilder, TransitionBuilder
from fit_to_md.infrastructure.markdown.renderer import MarkdownReportRenderer
from fit_to_md.infrastructure.weather import OpenMeteoHistoricalWeatherProvider


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive number")
    return parsed


def _non_negative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be a non-negative number")
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
    parser.add_argument(
        "--weather-mode",
        choices=("auto", "fit"),
        default="auto",
        help="Use FIT-native weather only or enrich missing weather with historical lookup.",
    )
    parser.add_argument(
        "--elevation-smoothing-distance",
        type=_positive_float,
        default=170.0,
        help="Distance in meters used to smooth record altitude before computing session gain/loss.",
    )
    parser.add_argument(
        "--elevation-min-change",
        type=_non_negative_float,
        default=0.4,
        help="Minimum altitude change in meters counted toward session gain/loss after smoothing.",
    )
    return parser


def build_default_generator(
    transition_sample_interval: int = 10,
    transition_window: int = 60,
    weather_mode: str = "auto",
    elevation_smoothing_distance: float = 170.0,
    elevation_min_change: float = 0.4,
) -> GenerateMarkdownReport:
    weather_provider = OpenMeteoHistoricalWeatherProvider() if weather_mode == "auto" else None
    extractor = FitdecodeActivityExtractor(
        summary_builder=SessionSummaryBuilder(
            elevation_smoothing_distance_m=elevation_smoothing_distance,
            min_elevation_change_m=elevation_min_change,
        ),
        transition_builder=TransitionBuilder(
            sample_interval_s=transition_sample_interval,
            window_s=transition_window,
        ),
        weather_provider=weather_provider,
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
        weather_mode=args.weather_mode,
        elevation_smoothing_distance=args.elevation_smoothing_distance,
        elevation_min_change=args.elevation_min_change,
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
