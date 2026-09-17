import argparse
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TextIO

from fit_to_md.application.use_cases.generate_markdown_report import (
    GenerateMarkdownReport,
)
from fit_to_md.application.use_cases.generate_markdown_report_batch import (
    ActivityTimeUnavailableError,
    BatchOutputNaming,
    BatchReportStatus,
    GenerateMarkdownReportBatch,
    activity_time_output_candidates,
)
from fit_to_md.domain.activity.ports import (
    InvalidActivityError,
    UnsupportedActivityError,
)
from fit_to_md.domain.reporting.ports import (
    ElevationDiagnostics,
    ProviderDiagnostic,
)
from fit_to_md.domain.reporting.services import SessionSummaryBuilder, TransitionBuilder
from fit_to_md.infrastructure.config import ConfigFileError, load_option_file
from fit_to_md.infrastructure.elevation import OpenTopoDataElevationProvider
from fit_to_md.infrastructure.fitdecode.reader import FitdecodeActivityReader
from fit_to_md.infrastructure.markdown.renderer import MarkdownReportRenderer
from fit_to_md.infrastructure.markdown.writer import LocalMarkdownReportWriter
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
class _DefaultRuntime:
    generator: GenerateMarkdownReport
    elevation_diagnostics: ElevationDiagnostics | None


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a finite positive number")
    return parsed


def _non_negative_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("value must be a finite non-negative number")
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
        help="Name the default output file from the activity start time as 'YYYY-MM-DD HH-MM.md'.",
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
    return _build_default_runtime(
        dynamics_step_size=dynamics_step_size,
        weather_mode=weather_mode,
        elevation_smoothing_distance=elevation_smoothing_distance,
        elevation_min_change=elevation_min_change,
        elevation_source=elevation_source,
        dem_sample_distance=dem_sample_distance,
        opentopodata_dataset=opentopodata_dataset,
        opentopodata_base_url=opentopodata_base_url,
    ).generator


def _build_default_runtime(
    dynamics_step_size: int = 30,
    weather_mode: str = "fit",
    elevation_smoothing_distance: float = 170.0,
    elevation_min_change: float = 0.4,
    elevation_source: str = "fit",
    dem_sample_distance: float = 25.0,
    opentopodata_dataset: str = "eudem25m",
    opentopodata_base_url: str = "https://api.opentopodata.org",
) -> _DefaultRuntime:
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
    reader = FitdecodeActivityReader()
    renderer = MarkdownReportRenderer()
    generator = GenerateMarkdownReport(
        reader=reader,
        renderer=renderer,
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
    return _DefaultRuntime(
        generator=generator,
        elevation_diagnostics=elevation_provider,
    )


def run(
    argv: Sequence[str] | None = None,
    report_generator: GenerateMarkdownReport | None = None,
    elevation_diagnostics: ElevationDiagnostics | None = None,
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

    if report_generator is None:
        runtime = _build_default_runtime(
            dynamics_step_size=args.dynamics_step_size,
            weather_mode=args.weather_mode,
            elevation_smoothing_distance=args.elevation_smoothing_distance,
            elevation_min_change=args.elevation_min_change,
            elevation_source=args.elevation_source,
            dem_sample_distance=args.dem_sample_distance,
            opentopodata_dataset=args.opentopodata_dataset,
            opentopodata_base_url=args.opentopodata_base_url,
        )
        generator = runtime.generator
        if elevation_diagnostics is None:
            elevation_diagnostics = runtime.elevation_diagnostics
    else:
        generator = report_generator
    _configure_elevation_progress(elevation_diagnostics, stderr)

    if is_directory:
        return _run_directory(
            fit_files,
            generator,
            output_by_activity_time=args.output_by_activity_time,
            elevation_diagnostics=elevation_diagnostics,
            stdout=stdout,
            stderr=stderr,
        )

    try:
        result = generator.execute_detailed(input_path)
        markdown = result.markdown
        if args.output_by_activity_time and args.output is None:
            output_path = _output_path_from_activity_time(
                input_path, result.report.summary.start_time
            )
        else:
            output_path = args.output or input_path.with_suffix(".md")
    except InvalidActivityError as error:
        print(f"Invalid FIT file: {input_path}: {error}", file=stderr)
        return 1
    except OSError as error:
        print(f"Unable to read input file: {input_path}: {error}", file=stderr)
        return 1
    except (UnsupportedActivityError, ActivityTimeUnavailableError) as error:
        print(str(error), file=stderr)
        return 1

    _write_provider_diagnostics(result.diagnostics, stderr)

    if _paths_refer_to_same_file(input_path, output_path):
        print(
            "Output path refers to the input FIT file; refusing to overwrite "
            f"the source: {output_path}",
            file=stderr,
        )
        return 2

    try:
        output_path.write_text(markdown, encoding="utf-8")
    except OSError as error:
        print(f"Unable to write Markdown report: {output_path}: {error}", file=stderr)
        return 1

    _write_markdown_to_stdout(markdown, stdout)
    _write_elevation_usage_summary(elevation_diagnostics, stderr)
    return 0


def _paths_refer_to_same_file(source: Path, output: Path) -> bool:
    try:
        if source.resolve() == output.resolve():
            return True
    except OSError:
        pass

    try:
        return output.exists() and source.samefile(output)
    except OSError:
        return False


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
    elevation_diagnostics: ElevationDiagnostics | None,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    batch = GenerateMarkdownReportBatch(generator, LocalMarkdownReportWriter())
    naming = (
        BatchOutputNaming.ACTIVITY_TIME
        if output_by_activity_time
        else BatchOutputNaming.SOURCE_NAME
    )
    failed = False
    reported_collisions: set[Path] = set()

    for outcome in batch.execute(fit_files, naming):
        if outcome.status is BatchReportStatus.SKIPPED_EXISTING:
            continue

        if outcome.status is BatchReportStatus.COLLISION:
            assert outcome.output is not None
            if outcome.output not in reported_collisions:
                sources = ", ".join(str(source) for source in outcome.collision_sources)
                print(
                    f"Output path collision: {outcome.output} for inputs: {sources}",
                    file=stderr,
                )
                reported_collisions.add(outcome.output)
            failed = True
            continue

        if outcome.generated is not None:
            _write_provider_diagnostics(
                outcome.generated.diagnostics,
                stderr,
                source=outcome.source,
            )

        if outcome.status is BatchReportStatus.WRITE_FAILED:
            assert outcome.output is not None
            assert outcome.error is not None
            print(
                f"Unable to write Markdown report: {outcome.output}: {outcome.error}",
                file=stderr,
            )
            failed = True
            continue

        if outcome.status is BatchReportStatus.PROCESSING_FAILED:
            assert outcome.error is not None
            _write_directory_processing_error(outcome.source, outcome.error, stderr)
            failed = True
            continue

        assert outcome.status is BatchReportStatus.SUCCESS
        assert outcome.generated is not None
        _write_markdown_to_stdout(outcome.generated.markdown, stdout)

    _write_elevation_usage_summary(elevation_diagnostics, stderr)
    return 1 if failed else 0


def _write_directory_processing_error(
    fit_file: Path,
    error: Exception,
    stream: TextIO,
) -> None:
    if isinstance(error, InvalidActivityError):
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
    return _activity_time_output_candidates(input_path, start_time)[0]


def _activity_time_output_candidates(
    input_path: Path, start_time: datetime | None
) -> tuple[Path, ...]:
    return activity_time_output_candidates(input_path, start_time)


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
    diagnostics: ElevationDiagnostics | None, stream: TextIO
) -> None:
    if diagnostics is None:
        return

    statistics = diagnostics.run_statistics()
    if statistics.request_limit is None:
        summary = (
            f"{statistics.provider_name} requests this run: {statistics.request_count}."
        )
    else:
        summary = (
            f"{statistics.provider_name} public API calls this run: "
            f"{statistics.request_count}/{statistics.request_limit} "
            "(daily usage is not persisted by the CLI)."
        )
    print(summary, file=stream)


def _configure_elevation_progress(
    diagnostics: ElevationDiagnostics | None, stream: TextIO
) -> None:
    if diagnostics is None:
        return

    provider_name = diagnostics.run_statistics().provider_name

    def _write_progress(current_request: int, total_requests: int) -> None:
        print(
            f"{provider_name} progress: request {current_request}/{total_requests}",
            file=stream,
        )

    diagnostics.set_progress_callback(_write_progress)


def _write_provider_diagnostics(
    diagnostics: tuple[ProviderDiagnostic, ...],
    stream: TextIO,
    *,
    source: Path | None = None,
) -> None:
    source_label = f" for {source}" if source is not None else ""
    for diagnostic in diagnostics:
        print(
            f"Warning{source_label}: {diagnostic.provider_name}: "
            f"{diagnostic.message} [{diagnostic.kind.value}]",
            file=stream,
        )
