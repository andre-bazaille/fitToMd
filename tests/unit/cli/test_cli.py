import io
from datetime import datetime
from pathlib import Path

import pytest

import fit_to_md.cli as cli
from fit_to_md.cli import build_default_generator, build_parser, run
from fit_to_md.domain.reporting.entities import FitReport, SessionSummary
from fit_to_md.infrastructure.weather import OpenMeteoHistoricalWeatherProvider


class StubGenerator:
    def __init__(self, markdown: str) -> None:
        self.markdown = markdown
        self.calls: list[Path] = []

    def execute(self, source: Path) -> str:
        self.calls.append(source)
        return self.markdown


class StubGeneratorWithReport(StubGenerator):
    def __init__(self, markdown: str, report: FitReport) -> None:
        super().__init__(markdown)
        self.report = report

    def execute_with_report(self, source: Path) -> tuple[FitReport, str]:
        self.calls.append(source)
        return self.report, self.markdown


class StubGeneratorBySource:
    def __init__(self, markdown_by_source: dict[Path, str]) -> None:
        self.markdown_by_source = markdown_by_source
        self.calls: list[Path] = []

    def execute(self, source: Path) -> str:
        self.calls.append(source)
        return self.markdown_by_source[source]


class StubGeneratorWithReports:
    def __init__(
        self,
        markdown_by_source: dict[Path, str],
        reports_by_source: dict[Path, FitReport],
    ) -> None:
        self.markdown_by_source = markdown_by_source
        self.reports_by_source = reports_by_source
        self.calls: list[Path] = []

    def execute_with_report(self, source: Path) -> tuple[FitReport, str]:
        self.calls.append(source)
        return self.reports_by_source[source], self.markdown_by_source[source]


class StubElevationProvider:
    def __init__(self, summary: str) -> None:
        self._summary = summary
        self.progress_updates: list[tuple[int, int]] = []

    def usage_summary(self) -> str:
        return self._summary

    def set_progress_callback(self, callback) -> None:
        callback(1, 3)
        callback(2, 3)
        self.progress_updates.extend([(1, 3), (2, 3)])


class StubGeneratorWithElevationUsage(StubGenerator):
    def __init__(self, markdown: str, summary: str) -> None:
        super().__init__(markdown)
        self._extractor = type(
            "Extractor", (), {"_elevation_provider": StubElevationProvider(summary)}
        )()


def test_run_writes_markdown_to_default_output_file(tmp_path: Path) -> None:
    fit_file = tmp_path / "activity.fit"
    fit_file.write_bytes(b"FIT")
    stdout = io.StringIO()
    stderr = io.StringIO()
    generator = StubGenerator("# FIT Report\n")
    expected_output = tmp_path / "activity.md"

    exit_code = run(
        argv=[str(fit_file)],
        report_generator=generator,
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert stdout.getvalue() == "# FIT Report\n"
    assert stderr.getvalue() == ""
    assert generator.calls == [fit_file]
    assert expected_output.read_text(encoding="utf-8") == "# FIT Report\n"


def test_run_rejects_output_that_is_the_input_file(tmp_path: Path) -> None:
    fit_file = tmp_path / "activity.fit"
    source_bytes = b"FIT source bytes"
    fit_file.write_bytes(source_bytes)
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = run(
        argv=[str(fit_file), "--output", str(fit_file)],
        report_generator=StubGenerator("# FIT Report\n"),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert "refers to the input FIT file" in stderr.getvalue()
    assert fit_file.read_bytes() == source_bytes


def test_run_rejects_relative_output_alias_of_input_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fit_file = tmp_path / "activity.fit"
    source_bytes = b"FIT source bytes"
    fit_file.write_bytes(source_bytes)
    monkeypatch.chdir(tmp_path)
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = run(
        argv=[str(fit_file.resolve()), "--output", "activity.fit"],
        report_generator=StubGenerator("# FIT Report\n"),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert "refers to the input FIT file" in stderr.getvalue()
    assert fit_file.read_bytes() == source_bytes


def test_run_rejects_symlink_output_alias_of_input_file(tmp_path: Path) -> None:
    fit_file = tmp_path / "activity.fit"
    output_link = tmp_path / "report.md"
    source_bytes = b"FIT source bytes"
    fit_file.write_bytes(source_bytes)
    output_link.symlink_to(fit_file)
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = run(
        argv=[str(fit_file), "--output", str(output_link)],
        report_generator=StubGenerator("# FIT Report\n"),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert "refers to the input FIT file" in stderr.getvalue()
    assert fit_file.read_bytes() == source_bytes


def test_run_rejects_hard_link_output_alias_of_input_file(tmp_path: Path) -> None:
    fit_file = tmp_path / "activity.fit"
    output_link = tmp_path / "report.md"
    source_bytes = b"FIT source bytes"
    fit_file.write_bytes(source_bytes)
    output_link.hardlink_to(fit_file)
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = run(
        argv=[str(fit_file), "--output", str(output_link)],
        report_generator=StubGenerator("# FIT Report\n"),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert "refers to the input FIT file" in stderr.getvalue()
    assert fit_file.read_bytes() == source_bytes


def test_run_can_name_output_from_activity_start_time(tmp_path: Path) -> None:
    fit_file = tmp_path / "activity.fit"
    fit_file.write_bytes(b"FIT")
    stdout = io.StringIO()
    stderr = io.StringIO()
    generator = StubGeneratorWithReport(
        "# FIT Report\n", _report_with_start_time(datetime(2026, 9, 16, 7, 5, 59))
    )
    expected_output = tmp_path / "2026-09-16 07:05.md"

    exit_code = run(
        argv=[str(fit_file), "--output-by-activity-time"],
        report_generator=generator,
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert stdout.getvalue() == "# FIT Report\n"
    assert stderr.getvalue() == ""
    assert generator.calls == [fit_file]
    assert expected_output.read_text(encoding="utf-8") == "# FIT Report\n"
    assert not fit_file.with_suffix(".md").exists()


def test_run_rejects_activity_time_output_without_activity_start_time(
    tmp_path: Path,
) -> None:
    fit_file = tmp_path / "activity.fit"
    fit_file.write_bytes(b"FIT")
    stdout = io.StringIO()
    stderr = io.StringIO()
    generator = StubGeneratorWithReport("# FIT Report\n", _report_with_start_time(None))

    exit_code = run(
        argv=[str(fit_file), "--output-by-activity-time"],
        report_generator=generator,
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 1
    assert stdout.getvalue() == ""
    assert "Activity start time unavailable" in stderr.getvalue()
    assert not fit_file.with_suffix(".md").exists()


def test_run_reports_elevation_api_usage_to_stderr(tmp_path: Path) -> None:
    fit_file = tmp_path / "activity.fit"
    fit_file.write_bytes(b"FIT")
    stdout = io.StringIO()
    stderr = io.StringIO()
    expected_output = tmp_path / "activity.md"
    generator = StubGeneratorWithElevationUsage(
        "# FIT Report\n",
        "OpenTopoData public API calls this run: 3/1000 (daily usage is not persisted by the CLI).",
    )

    exit_code = run(
        argv=[str(fit_file)],
        report_generator=generator,
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert stdout.getvalue() == "# FIT Report\n"
    assert expected_output.read_text(encoding="utf-8") == "# FIT Report\n"
    assert stderr.getvalue() == (
        "OpenTopoData progress: request 1/3\n"
        "OpenTopoData progress: request 2/3\n"
        "OpenTopoData public API calls this run: 3/1000 (daily usage is not persisted by the CLI).\n"
    )


def test_run_returns_error_for_missing_input(tmp_path: Path) -> None:
    missing_file = tmp_path / "missing.fit"
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = run(argv=[str(missing_file)], stdout=stdout, stderr=stderr)

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert "Input file not found" in stderr.getvalue()


def test_run_treats_an_empty_directory_as_a_successful_no_op(tmp_path: Path) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = run(argv=[str(tmp_path)], stdout=stdout, stderr=stderr)

    assert exit_code == 0
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == ""


def test_run_processes_sorted_direct_fit_children_and_skips_existing_reports(
    tmp_path: Path,
) -> None:
    input_directory = tmp_path / "activities"
    input_directory.mkdir()
    first_fit = input_directory / "b.fit"
    second_fit = input_directory / "a.FIT"
    existing_fit = input_directory / "c.fit"
    nested_directory = input_directory / "nested"
    nested_directory.mkdir()
    nested_fit = nested_directory / "nested.fit"
    for fit_file in (first_fit, second_fit, existing_fit, nested_fit):
        fit_file.write_bytes(b"FIT")
    (input_directory / "notes.txt").write_text("not FIT", encoding="utf-8")
    existing_report = existing_fit.with_suffix(".md")
    existing_report.write_text("keep this report", encoding="utf-8")

    generator = StubGeneratorBySource(
        {
            first_fit: "# B report",
            second_fit: "# A report",
        }
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = run(
        argv=[str(input_directory)],
        report_generator=generator,
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert generator.calls == [second_fit, first_fit]
    assert second_fit.with_suffix(".md").read_text(encoding="utf-8") == "# A report"
    assert first_fit.with_suffix(".md").read_text(encoding="utf-8") == "# B report"
    assert existing_report.read_text(encoding="utf-8") == "keep this report"
    assert not nested_fit.with_suffix(".md").exists()
    assert stdout.getvalue() == "# A report\n# B report\n"
    assert stderr.getvalue() == ""


def test_run_rejects_manual_output_for_directory_input(tmp_path: Path) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    generator = StubGenerator("# report")

    exit_code = run(
        argv=[str(tmp_path), "--output", str(tmp_path / "reports.md")],
        report_generator=generator,
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 2
    assert generator.calls == []
    assert stdout.getvalue() == ""
    assert "--output option cannot be used" in stderr.getvalue()


def test_run_rejects_configured_manual_output_for_directory_input(
    tmp_path: Path,
) -> None:
    config_file = tmp_path / ".config"
    config_file.write_text("output = reports.md\n", encoding="utf-8")
    stdout = io.StringIO()
    stderr = io.StringIO()
    generator = StubGenerator("# report")

    exit_code = run(
        argv=[str(tmp_path), "--config", str(config_file)],
        report_generator=generator,
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 2
    assert generator.calls == []
    assert stdout.getvalue() == ""
    assert "--output option cannot be used" in stderr.getvalue()


def test_run_uses_activity_time_names_and_skips_existing_reports(
    tmp_path: Path,
) -> None:
    first_fit = tmp_path / "first.fit"
    second_fit = tmp_path / "second.fit"
    first_fit.write_bytes(b"FIT")
    second_fit.write_bytes(b"FIT")
    first_report_path = tmp_path / "2026-09-16 07:05.md"
    first_report_path.write_text("keep this report", encoding="utf-8")
    reports = {
        first_fit: _report_with_start_time(datetime(2026, 9, 16, 7, 5)),
        second_fit: _report_with_start_time(datetime(2026, 9, 16, 8, 10)),
    }
    generator = StubGeneratorWithReports(
        {first_fit: "# old", second_fit: "# new"}, reports
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = run(
        argv=[str(tmp_path), "--output-by-activity-time"],
        report_generator=generator,
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert generator.calls == [first_fit, second_fit]
    assert first_report_path.read_text(encoding="utf-8") == "keep this report"
    assert (tmp_path / "2026-09-16 08:10.md").read_text(encoding="utf-8") == "# new"
    assert stdout.getvalue() == "# new\n"
    assert stderr.getvalue() == ""


def test_run_continues_after_directory_processing_failure(tmp_path: Path) -> None:
    input_directory = tmp_path / "activities"
    input_directory.mkdir()
    failing_fit = input_directory / "a.fit"
    successful_fit = input_directory / "b.fit"
    failing_fit.write_bytes(b"FIT")
    successful_fit.write_bytes(b"FIT")

    class PartiallyFailingGenerator(StubGenerator):
        def execute(self, source: Path) -> str:
            self.calls.append(source)
            if source == failing_fit:
                raise OSError("permission denied")
            return self.markdown

    generator = PartiallyFailingGenerator("# successful")
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = run(
        argv=[str(input_directory)],
        report_generator=generator,
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 1
    assert generator.calls == [failing_fit, successful_fit]
    assert not failing_fit.with_suffix(".md").exists()
    assert successful_fit.with_suffix(".md").read_text(encoding="utf-8") == (
        "# successful"
    )
    assert stdout.getvalue() == "# successful\n"
    assert f"Unable to read input file: {failing_fit}" in stderr.getvalue()


def test_run_continues_after_directory_write_failure(tmp_path: Path) -> None:
    input_directory = tmp_path / "activities"
    input_directory.mkdir()
    failing_fit = input_directory / "a.fit"
    successful_fit = input_directory / "b.fit"
    failing_fit.write_bytes(b"FIT")
    successful_fit.write_bytes(b"FIT")
    failing_fit.with_suffix(".md").mkdir()

    generator = StubGenerator("# successful")
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = run(
        argv=[str(input_directory)],
        report_generator=generator,
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 1
    assert successful_fit.with_suffix(".md").read_text(encoding="utf-8") == (
        "# successful"
    )
    assert stdout.getvalue() == "# successful\n"
    assert "Unable to write Markdown report" in stderr.getvalue()


def test_run_continues_after_missing_activity_time(tmp_path: Path) -> None:
    input_directory = tmp_path / "activities"
    input_directory.mkdir()
    missing_time_fit = input_directory / "a.fit"
    valid_fit = input_directory / "b.fit"
    missing_time_fit.write_bytes(b"FIT")
    valid_fit.write_bytes(b"FIT")
    generator = StubGeneratorWithReports(
        {missing_time_fit: "# missing", valid_fit: "# valid"},
        {
            missing_time_fit: _report_with_start_time(None),
            valid_fit: _report_with_start_time(datetime(2026, 9, 16, 8, 10)),
        },
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = run(
        argv=[str(input_directory), "--output-by-activity-time"],
        report_generator=generator,
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 1
    assert not (input_directory / "2026-09-16 07:05.md").exists()
    assert (input_directory / "2026-09-16 08:10.md").read_text(
        encoding="utf-8"
    ) == "# valid"
    assert stdout.getvalue() == "# valid\n"
    assert "Activity start time unavailable" in stderr.getvalue()


def test_run_does_not_write_duplicate_activity_time_destinations(
    tmp_path: Path,
) -> None:
    input_directory = tmp_path / "activities"
    input_directory.mkdir()
    first_fit = input_directory / "a.fit"
    second_fit = input_directory / "b.fit"
    unique_fit = input_directory / "c.fit"
    for fit_file in (first_fit, second_fit, unique_fit):
        fit_file.write_bytes(b"FIT")
    duplicate_time = datetime(2026, 9, 16, 7, 5)
    generator = StubGeneratorWithReports(
        {
            first_fit: "# first",
            second_fit: "# second",
            unique_fit: "# unique",
        },
        {
            first_fit: _report_with_start_time(duplicate_time),
            second_fit: _report_with_start_time(duplicate_time),
            unique_fit: _report_with_start_time(datetime(2026, 9, 16, 8, 10)),
        },
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = run(
        argv=[str(input_directory), "--output-by-activity-time"],
        report_generator=generator,
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 1
    assert not (input_directory / "2026-09-16 07:05.md").exists()
    assert (input_directory / "2026-09-16 08:10.md").read_text(
        encoding="utf-8"
    ) == "# unique"
    assert stdout.getvalue() == "# unique\n"
    error_output = stderr.getvalue()
    assert "Output path collision" in error_output
    assert str(first_fit) in error_output
    assert str(second_fit) in error_output


def test_run_returns_friendly_error_for_invalid_fit_file(tmp_path: Path) -> None:
    fit_file = tmp_path / "invalid.fit"
    fit_file.write_bytes(b"not a FIT file")
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = run(argv=[str(fit_file)], stdout=stdout, stderr=stderr)

    assert exit_code == 1
    assert stdout.getvalue() == ""
    assert "Invalid FIT file" in stderr.getvalue()
    assert not fit_file.with_suffix(".md").exists()


def test_run_returns_friendly_error_for_multiple_sessions(tmp_path: Path) -> None:
    fit_file = tmp_path / "multisport.fit"
    fit_file.write_bytes(b"FIT")
    stdout = io.StringIO()
    stderr = io.StringIO()

    class MultipleSessionGenerator:
        def execute(self, source: Path) -> str:
            raise NotImplementedError(
                "FIT files with multiple sessions are not supported."
            )

    exit_code = run(
        argv=[str(fit_file)],
        report_generator=MultipleSessionGenerator(),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 1
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == (
        "FIT files with multiple sessions are not supported.\n"
    )
    assert not fit_file.with_suffix(".md").exists()


def test_run_returns_friendly_error_when_input_cannot_be_read(tmp_path: Path) -> None:
    fit_file = tmp_path / "activity.fit"
    fit_file.write_bytes(b"FIT")
    stdout = io.StringIO()
    stderr = io.StringIO()

    class UnreadableGenerator:
        def execute(self, source: Path) -> str:
            raise PermissionError("permission denied")

    exit_code = run(
        argv=[str(fit_file)],
        report_generator=UnreadableGenerator(),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 1
    assert stdout.getvalue() == ""
    assert "Unable to read input file" in stderr.getvalue()


def test_run_returns_friendly_error_when_output_cannot_be_written(
    tmp_path: Path,
) -> None:
    fit_file = tmp_path / "activity.fit"
    fit_file.write_bytes(b"FIT")
    output_directory = tmp_path / "report-directory"
    output_directory.mkdir()
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = run(
        argv=[str(fit_file), "--output", str(output_directory)],
        report_generator=StubGenerator("# FIT Report\n"),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 1
    assert stdout.getvalue() == ""
    assert "Unable to write Markdown report" in stderr.getvalue()


def test_parser_rejects_removed_transition_window_option() -> None:
    with pytest.raises(SystemExit) as error:
        build_parser().parse_args(["activity.fit", "--transition-window", "60"])

    assert error.value.code == 2


def test_default_configuration_does_not_enable_external_providers() -> None:
    args = build_parser().parse_args(["activity.fit"])
    generator = build_default_generator()
    extractor = generator._extractor

    assert args.weather_mode == "fit"
    assert args.elevation_source == "fit"
    assert args.dynamics_step_size == 30
    assert args.dem_sample_distance == 25.0
    assert extractor._weather_provider is None
    assert extractor._elevation_provider is None
    assert extractor._elevation_mode == "fit"
    assert extractor._transition_builder._sample_interval_s == 30
    assert extractor._elevation_sample_distance_m == 25.0


def test_run_passes_transition_options_to_default_generator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fit_file = tmp_path / "activity.fit"
    fit_file.write_bytes(b"FIT")
    stdout = io.StringIO()
    stderr = io.StringIO()
    calls: list[tuple[int, str, float, float, str, float, str, str]] = []

    def fake_build_default_generator(
        dynamics_step_size: int = 30,
        weather_mode: str = "fit",
        elevation_smoothing_distance: float = 170.0,
        elevation_min_change: float = 0.4,
        elevation_source: str = "fit",
        dem_sample_distance: float = 25.0,
        opentopodata_dataset: str = "eudem25m",
        opentopodata_base_url: str = "https://api.opentopodata.org",
    ) -> StubGenerator:
        calls.append(
            (
                dynamics_step_size,
                weather_mode,
                elevation_smoothing_distance,
                elevation_min_change,
                elevation_source,
                dem_sample_distance,
                opentopodata_dataset,
                opentopodata_base_url,
            )
        )
        return StubGenerator("# FIT Report\n")

    monkeypatch.setattr(cli, "build_default_generator", fake_build_default_generator)

    exit_code = run(
        argv=[
            str(fit_file),
            "--dynamics-step-size",
            "5",
            "--weather-mode",
            "fit",
            "--elevation-smoothing-distance",
            "220",
            "--elevation-min-change",
            "0.8",
            "--elevation-source",
            "hybrid",
            "--dem-sample-distance",
            "25",
            "--opentopodata-dataset",
            "copernicus",
            "--opentopodata-base-url",
            "https://elevation.internal",
        ],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert calls == [
        (
            5,
            "fit",
            220.0,
            0.8,
            "hybrid",
            25.0,
            "copernicus",
            "https://elevation.internal",
        )
    ]


def test_run_loads_default_options_from_config_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fit_file = tmp_path / "activity.fit"
    fit_file.write_bytes(b"FIT")
    config_file = tmp_path / ".config"
    config_file.write_text(
        "dynamics-step-size = 8\nweather-mode = auto\nelevation-source = hybrid\n",
        encoding="utf-8",
    )
    calls: list[tuple[int, str, str]] = []

    def fake_build_default_generator(**options) -> StubGenerator:
        calls.append(
            (
                options["dynamics_step_size"],
                options["weather_mode"],
                options["elevation_source"],
            )
        )
        return StubGenerator("# FIT Report\n")

    monkeypatch.setattr(cli, "build_default_generator", fake_build_default_generator)

    exit_code = run(argv=[str(fit_file), "--config", str(config_file)])

    assert exit_code == 0
    assert calls == [(8, "auto", "hybrid")]


def test_run_loads_activity_time_output_option_from_config_file(
    tmp_path: Path,
) -> None:
    fit_file = tmp_path / "activity.fit"
    fit_file.write_bytes(b"FIT")
    config_file = tmp_path / ".config"
    config_file.write_text("output-by-activity-time = true\n", encoding="utf-8")
    generator = StubGeneratorWithReport(
        "# FIT Report\n", _report_with_start_time(datetime(2026, 9, 16, 7, 5))
    )

    exit_code = run(
        argv=[str(fit_file), "--config", str(config_file)],
        report_generator=generator,
    )

    assert exit_code == 0
    assert (tmp_path / "2026-09-16 07:05.md").exists()


def test_run_rejects_invalid_boolean_output_option_from_config_file(
    tmp_path: Path,
) -> None:
    fit_file = tmp_path / "activity.fit"
    fit_file.write_bytes(b"FIT")
    config_file = tmp_path / ".config"
    config_file.write_text("output-by-activity-time = sometimes\n", encoding="utf-8")

    with pytest.raises(SystemExit) as error:
        run(argv=[str(fit_file), "--config", str(config_file)])

    assert error.value.code == 2


def test_explicit_command_line_option_overrides_config_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fit_file = tmp_path / "activity.fit"
    fit_file.write_bytes(b"FIT")
    config_file = tmp_path / ".config"
    config_file.write_text("dynamics-step-size = 8\n", encoding="utf-8")
    configured_step_sizes: list[int] = []

    def fake_build_default_generator(**options) -> StubGenerator:
        configured_step_sizes.append(options["dynamics_step_size"])
        return StubGenerator("# FIT Report\n")

    monkeypatch.setattr(cli, "build_default_generator", fake_build_default_generator)

    exit_code = run(
        argv=[
            str(fit_file),
            "--config",
            str(config_file),
            "--dynamics-step-size",
            "12",
        ]
    )

    assert exit_code == 0
    assert configured_step_sizes == [12]


def test_run_rejects_invalid_value_from_config_file(tmp_path: Path) -> None:
    fit_file = tmp_path / "activity.fit"
    fit_file.write_bytes(b"FIT")
    config_file = tmp_path / ".config"
    config_file.write_text("weather-mode = unsupported\n", encoding="utf-8")

    with pytest.raises(SystemExit) as error:
        run(argv=[str(fit_file), "--config", str(config_file)])

    assert error.value.code == 2


def test_build_default_generator_configures_transition_builder() -> None:
    generator = build_default_generator(
        dynamics_step_size=5,
        weather_mode="auto",
        elevation_smoothing_distance=220.0,
        elevation_min_change=0.8,
        elevation_source="hybrid",
        dem_sample_distance=25.0,
        opentopodata_dataset="copernicus",
        opentopodata_base_url="https://elevation.internal",
    )

    extractor = generator._extractor
    summary_builder = extractor._summary_builder
    transition_builder = extractor._transition_builder

    assert summary_builder._elevation_smoothing_distance_m == 220.0
    assert summary_builder._min_elevation_change_m == 0.8
    assert transition_builder._sample_interval_s == 5
    assert isinstance(extractor._weather_provider, OpenMeteoHistoricalWeatherProvider)
    assert extractor._elevation_provider is not None
    assert extractor._elevation_mode == "hybrid"
    assert extractor._elevation_sample_distance_m == 25.0
    assert extractor._elevation_provider._dataset == "copernicus"
    assert extractor._elevation_provider._base_url == "https://elevation.internal"


def test_run_rejects_invalid_elevation_min_change(tmp_path: Path) -> None:
    fit_file = tmp_path / "activity.fit"
    fit_file.write_bytes(b"FIT")
    stdout = io.StringIO()
    stderr = io.StringIO()

    with pytest.raises(SystemExit) as error:
        run(
            argv=[str(fit_file), "--elevation-min-change", "-1"],
            stdout=stdout,
            stderr=stderr,
        )

    assert error.value.code == 2


def test_run_rejects_invalid_dem_sample_distance(tmp_path: Path) -> None:
    fit_file = tmp_path / "activity.fit"
    fit_file.write_bytes(b"FIT")
    stdout = io.StringIO()
    stderr = io.StringIO()

    with pytest.raises(SystemExit) as error:
        run(
            argv=[str(fit_file), "--dem-sample-distance", "0"],
            stdout=stdout,
            stderr=stderr,
        )

    assert error.value.code == 2


def test_run_returns_error_for_runtime_limit_failure(tmp_path: Path) -> None:
    fit_file = tmp_path / "activity.fit"
    fit_file.write_bytes(b"FIT")
    stdout = io.StringIO()
    stderr = io.StringIO()

    class FailingGenerator:
        def execute(self, source: Path) -> str:
            raise RuntimeError("OpenTopoData public API limit exceeded")

    exit_code = run(
        argv=[str(fit_file)],
        report_generator=FailingGenerator(),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 1
    assert stdout.getvalue() == ""
    assert "OpenTopoData public API limit exceeded" in stderr.getvalue()


def _report_with_start_time(start_time: datetime | None) -> FitReport:
    return FitReport(
        summary=SessionSummary(
            start_time=start_time,
            activity_name="Running",
            activity_type="running",
            total_distance_km=None,
            total_timer_time_s=None,
            total_elapsed_time_s=None,
            total_ascent_m=None,
            total_descent_m=None,
            avg_heart_rate_bpm=None,
            max_heart_rate_bpm=None,
            avg_cadence_spm=None,
            avg_speed_kmh=None,
            avg_temperature_c=None,
            min_temperature_c=None,
            max_temperature_c=None,
        )
    )
