from __future__ import annotations

import io
from pathlib import Path

import pytest

import fit_to_md.cli as cli
from fit_to_md.cli import build_default_generator, run
from fit_to_md.infrastructure.weather import OpenMeteoHistoricalWeatherProvider


class StubGenerator:
    def __init__(self, markdown: str) -> None:
        self.markdown = markdown
        self.calls: list[Path] = []

    def execute(self, source: Path) -> str:
        self.calls.append(source)
        return self.markdown


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
        self._extractor = type("Extractor", (), {"_elevation_provider": StubElevationProvider(summary)})()


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


def test_run_passes_transition_options_to_default_generator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fit_file = tmp_path / "activity.fit"
    fit_file.write_bytes(b"FIT")
    stdout = io.StringIO()
    stderr = io.StringIO()
    calls: list[tuple[int, str, float, float, str, float, str, str]] = []

    def fake_build_default_generator(
        dynamics_step_size: int = 15,
        weather_mode: str = "auto",
        elevation_smoothing_distance: float = 170.0,
        elevation_min_change: float = 0.4,
        elevation_source: str = "fit",
        dem_sample_distance: float = 30.0,
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
    assert calls == [(5, "fit", 220.0, 0.8, "hybrid", 25.0, "copernicus", "https://elevation.internal")]


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
