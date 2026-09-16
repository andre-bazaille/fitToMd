import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
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
    "output-by-activity-time",
    "dynamics-step-size",
    "weather-mode",
    "elevation-smoothing-distance",
    "elevation-min-change",
    "elevation-source",
    "dem-sample-distance",
    "opentopodata-dataset",
    "opentopodata-base-url",
)
_BOOLEAN_CONFIG_OPTIONS = frozenset(("output-by-activity-time",))
_TRUE_CONFIG_VALUES = frozenset(("1", "true", "yes", "on"))
_FALSE_CONFIG_VALUES = frozenset(("0", "false", "no", "off"))


@dataclass(frozen=True)
class _PendingReport:
    source: Path
    output: Path
    markdown: str


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
        description="Convert a FIT activity file or directory into Markdown reports.",
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Path to a FIT file or a directory containing FIT files.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to a file containing default options as 'option = value' pairs.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Optional output path for a single FIT file; not allowed for directory inputs.",
    )
    parser.add_argument(
        "--output-by-activity-time",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Name the default output file from the activity start time as 'YYYY-MM-DD HH:MM.md'.",
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
    if not input_path.is_file() and not input_path.is_dir():
        print(f"Input path is not a file or directory: {input_path}", file=stderr)
        return 2

    is_directory = input_path.is_dir()
    if is_directory and args.output is not None:
        print(
            "The --output option cannot be used when input is a directory: "
            f"{input_path}",
            file=stderr,
        )
        return 2

    fit_files: list[Path] = []
    if is_directory:
        try:
            fit_files = _fit_files_in_directory(input_path)
        except OSError as error:
            print(f"Unable to read input directory: {input_path}: {error}", file=stderr)
            return 1
        if not fit_files:
            return 0

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

    if is_directory:
        return _run_directory(
            fit_files,
            generator,
            output_by_activity_time=args.output_by_activity_time,
            stdout=stdout,
            stderr=stderr,
        )

    try:
        if args.output_by_activity_time and args.output is None:
            report, markdown = generator.execute_with_report(input_path)
            output_path = _output_path_from_activity_time(
                input_path, report.summary.start_time
            )
        else:
            markdown = generator.execute(input_path)
            output_path = args.output or input_path.with_suffix(".md")
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

    _write_markdown_to_stdout(markdown, stdout)
    _write_elevation_usage_summary(generator, stderr)
    return 0


def _fit_files_in_directory(input_directory: Path) -> list[Path]:
    return sorted(
        path
        for path in input_directory.iterdir()
        if path.is_file() and path.suffix.casefold() == ".fit"
    )


def _run_directory(
    fit_files: Sequence[Path],
    generator: GenerateMarkdownReport,
    *,
    output_by_activity_time: bool,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    pending_reports: list[_PendingReport] = []
    failed = False

    for fit_file in fit_files:
        if not output_by_activity_time:
            output_path = fit_file.with_suffix(".md")
            if output_path.is_file():
                continue

            try:
                markdown = generator.execute(fit_file)
            except (
                fitdecode.FitError,
                OSError,
                NotImplementedError,
                RuntimeError,
            ) as error:
                _write_directory_processing_error(fit_file, error, stderr)
                failed = True
                continue

            pending_reports.append(
                _PendingReport(
                    source=fit_file,
                    output=output_path,
                    markdown=markdown,
                )
            )
            continue

        try:
            report, markdown = generator.execute_with_report(fit_file)
            output_path = _output_path_from_activity_time(
                fit_file, report.summary.start_time
            )
        except (
            fitdecode.FitError,
            OSError,
            NotImplementedError,
            RuntimeError,
        ) as error:
            _write_directory_processing_error(fit_file, error, stderr)
            failed = True
            continue

        if output_path.is_file():
            continue
        pending_reports.append(
            _PendingReport(
                source=fit_file,
                output=output_path,
                markdown=markdown,
            )
        )

    output_groups: dict[Path, list[_PendingReport]] = {}
    for pending_report in pending_reports:
        output_groups.setdefault(pending_report.output, []).append(pending_report)

    colliding_outputs = {
        output_path
        for output_path, reports in output_groups.items()
        if len(reports) > 1
    }
    for output_path in sorted(colliding_outputs):
        sources = ", ".join(str(report.source) for report in output_groups[output_path])
        print(
            f"Output path collision: {output_path} for inputs: {sources}",
            file=stderr,
        )
        failed = True

    for pending_report in pending_reports:
        if pending_report.output in colliding_outputs:
            continue
        if pending_report.output.is_file():
            continue

        try:
            pending_report.output.write_text(
                pending_report.markdown,
                encoding="utf-8",
            )
        except OSError as error:
            print(
                f"Unable to write Markdown report: {pending_report.output}: {error}",
                file=stderr,
            )
            failed = True
            continue

        _write_markdown_to_stdout(pending_report.markdown, stdout)

    _write_elevation_usage_summary(generator, stderr)
    return 1 if failed else 0


def _write_directory_processing_error(
    fit_file: Path,
    error: Exception,
    stream: TextIO,
) -> None:
    if isinstance(error, fitdecode.FitError):
        print(f"Invalid FIT file: {fit_file}: {error}", file=stream)
    elif isinstance(error, OSError):
        print(f"Unable to read input file: {fit_file}: {error}", file=stream)
    else:
        print(f"Unable to process input file: {fit_file}: {error}", file=stream)


def _write_markdown_to_stdout(markdown: str, stream: TextIO) -> None:
    stream.write(markdown)
    if not markdown.endswith("\n"):
        stream.write("\n")


def _output_path_from_activity_time(
    input_path: Path, start_time: datetime | None
) -> Path:
    if start_time is None:
        raise RuntimeError(
            "Activity start time unavailable; cannot use activity time for output name."
        )
    return input_path.with_name(f"{start_time.strftime('%Y-%m-%d %H:%M')}.md")


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

    defaults: list[str] = []
    for option, value in configured_options.items():
        if option in _BOOLEAN_CONFIG_OPTIONS:
            normalized_value = value.casefold()
            if normalized_value in _TRUE_CONFIG_VALUES:
                defaults.append(f"--{option}")
            elif normalized_value in _FALSE_CONFIG_VALUES:
                defaults.append(f"--no-{option}")
            else:
                parser.error(f"invalid boolean value {value!r} for option {option!r}")
        else:
            defaults.extend((f"--{option}", value))
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
