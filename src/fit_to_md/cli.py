import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

import fitdecode

from fit_to_md.application.use_cases.generate_markdown_report import (
    GenerateMarkdownReport,
)
from fit_to_md.domain.reporting.services import SessionSummaryBuilder, TransitionBuilder
from fit_to_md.infrastructure.config import ConfigFileError, load_option_file
from fit_to_md.infrastructure.elevation import OpenTopoDataElevationProvider
from fit_to_md.infrastructure.fitdecode.extractor import FitdecodeActivityExtractor
from fit_to_md.infrastructure.markdown.renderer import MarkdownReportRenderer
from fit_to_md.infrastructure.weather import OpenMeteoHistoricalWeatherProvider

CONFIGURABLE_OPTIONS = (
    "output",
    "dynamics-step-size",
    "weather-mode",
    "elevation-smoothing-distance",
    "elevation-min-change",
    "elevation-source",
    "dem-sample-distance",
    "opentopodata-dataset",
    "opentopodata-base-url",
)


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
        "--config",
        type=Path,
        help="Path to a file containing default options as 'option = value' pairs.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Optional path for the generated Markdown file; defaults to the input file with a .md suffix.",
    )
    parser.add_argument(
        "--dynamics-step-size",
        "--transition-sample-interval",
        dest="dynamics_step_size",
        type=_positive_int,
        default=30,
        help="Sampling interval in seconds for per-kilometer dynamics output.",
    )
    parser.add_argument(
        "--weather-mode",
        choices=("auto", "fit"),
        default="fit",
        help="Use FIT-native weather only (default) or enrich missing weather with historical lookup.",
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
    parser.add_argument(
        "--elevation-source",
        choices=("fit", "dem", "hybrid"),
        default="fit",
        help="Use FIT altitude (default), DEM altitude, or DEM only when FIT altitude looks noisy.",
    )
    parser.add_argument(
        "--dem-sample-distance",
        type=_positive_float,
        default=25.0,
        help="Distance in meters between route samples queried from the DEM provider.",
    )
    parser.add_argument(
        "--opentopodata-dataset",
        default="eudem25m",
        help="OpenTopoData dataset to query for DEM altitude.",
    )
    parser.add_argument(
        "--opentopodata-base-url",
        default="https://api.opentopodata.org",
        help="OpenTopoData base URL, useful for self-hosted instances.",
    )
    return parser


def build_default_generator(
    dynamics_step_size: int = 30,
    weather_mode: str = "fit",
    elevation_smoothing_distance: float = 170.0,
    elevation_min_change: float = 0.4,
    elevation_source: str = "fit",
    dem_sample_distance: float = 25.0,
    opentopodata_dataset: str = "eudem25m",
    opentopodata_base_url: str = "https://api.opentopodata.org",
) -> GenerateMarkdownReport:
    weather_provider = (
        OpenMeteoHistoricalWeatherProvider() if weather_mode == "auto" else None
    )
    elevation_provider = (
        OpenTopoDataElevationProvider(
            base_url=opentopodata_base_url,
            dataset=opentopodata_dataset,
        )
        if elevation_source in {"dem", "hybrid"}
        else None
    )
    extractor = FitdecodeActivityExtractor(
        summary_builder=SessionSummaryBuilder(
            elevation_smoothing_distance_m=elevation_smoothing_distance,
            min_elevation_change_m=elevation_min_change,
        ),
        transition_builder=TransitionBuilder(
            sample_interval_s=dynamics_step_size,
        ),
        weather_provider=weather_provider,
        elevation_provider=elevation_provider,
        elevation_mode=elevation_source,
        elevation_sample_distance_m=dem_sample_distance,
    )
    renderer = MarkdownReportRenderer()
    return GenerateMarkdownReport(extractor=extractor, renderer=renderer)


def run(
    argv: Sequence[str] | None = None,
    report_generator: GenerateMarkdownReport | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    parser = build_parser()
    command_line = list(argv) if argv is not None else sys.argv[1:]
    args = parser.parse_args(_arguments_with_config_defaults(parser, command_line))

    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    input_path = args.input

    if not input_path.exists():
        print(f"Input file not found: {input_path}", file=stderr)
        return 2
    if not input_path.is_file():
        print(f"Input path is not a file: {input_path}", file=stderr)
        return 2

    output_path = args.output or input_path.with_suffix(".md")

    generator = report_generator or build_default_generator(
        dynamics_step_size=args.dynamics_step_size,
        weather_mode=args.weather_mode,
        elevation_smoothing_distance=args.elevation_smoothing_distance,
        elevation_min_change=args.elevation_min_change,
        elevation_source=args.elevation_source,
        dem_sample_distance=args.dem_sample_distance,
        opentopodata_dataset=args.opentopodata_dataset,
        opentopodata_base_url=args.opentopodata_base_url,
    )
    _configure_elevation_progress(generator, stderr)

    try:
        markdown = generator.execute(input_path)
    except fitdecode.FitError as error:
        print(f"Invalid FIT file: {input_path}: {error}", file=stderr)
        return 1
    except OSError as error:
        print(f"Unable to read input file: {input_path}: {error}", file=stderr)
        return 1
    except (NotImplementedError, RuntimeError) as error:
        print(str(error), file=stderr)
        return 1

    try:
        output_path.write_text(markdown, encoding="utf-8")
    except OSError as error:
        print(f"Unable to write Markdown report: {output_path}: {error}", file=stderr)
        return 1

    stdout.write(markdown)
    if not markdown.endswith("\n"):
        stdout.write("\n")
    _write_elevation_usage_summary(generator, stderr)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run(argv=argv)


def _arguments_with_config_defaults(
    parser: argparse.ArgumentParser,
    command_line: Sequence[str],
) -> Sequence[str]:
    config_parser = argparse.ArgumentParser(add_help=False)
    config_parser.add_argument("--config", type=Path)
    config_args, _ = config_parser.parse_known_args(command_line)
    if config_args.config is None:
        return command_line

    try:
        configured_options = load_option_file(config_args.config, CONFIGURABLE_OPTIONS)
    except ConfigFileError as error:
        parser.error(str(error))

    defaults = [
        argument
        for option, value in configured_options.items()
        for argument in (f"--{option}", value)
    ]
    return [*defaults, *command_line]


def _write_elevation_usage_summary(
    generator: GenerateMarkdownReport, stream: TextIO
) -> None:
    extractor = getattr(generator, "_extractor", None)
    if extractor is None:
        return

    elevation_provider = getattr(extractor, "_elevation_provider", None)
    if elevation_provider is None:
        return

    usage_summary_fn = getattr(elevation_provider, "usage_summary", None)
    if not callable(usage_summary_fn):
        return

    print(usage_summary_fn(), file=stream)


def _configure_elevation_progress(
    generator: GenerateMarkdownReport, stream: TextIO
) -> None:
    extractor = getattr(generator, "_extractor", None)
    if extractor is None:
        return

    elevation_provider = getattr(extractor, "_elevation_provider", None)
    if elevation_provider is None:
        return

    set_progress_callback_fn = getattr(
        elevation_provider, "set_progress_callback", None
    )
    if not callable(set_progress_callback_fn):
        return

    def _write_progress(current_request: int, total_requests: int) -> None:
        print(
            f"OpenTopoData progress: request {current_request}/{total_requests}",
            file=stream,
        )

    set_progress_callback_fn(_write_progress)
