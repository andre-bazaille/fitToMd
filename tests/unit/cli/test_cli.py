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


def test_run_writes_markdown_to_stdout(tmp_path: Path) -> None:
    fit_file = tmp_path / "activity.fit"
    fit_file.write_bytes(b"FIT")
    stdout = io.StringIO()
    stderr = io.StringIO()
    generator = StubGenerator("# FIT Report\n")

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
    calls: list[tuple[int, int, str, float, float]] = []

    def fake_build_default_generator(
        transition_sample_interval: int = 10,
        transition_window: int = 60,
        weather_mode: str = "auto",
        elevation_smoothing_distance: float = 170.0,
        elevation_min_change: float = 0.4,
    ) -> StubGenerator:
        calls.append(
            (
                transition_sample_interval,
                transition_window,
                weather_mode,
                elevation_smoothing_distance,
                elevation_min_change,
            )
        )
        return StubGenerator("# FIT Report\n")

    monkeypatch.setattr(cli, "build_default_generator", fake_build_default_generator)

    exit_code = run(
        argv=[
            str(fit_file),
            "--transition-sample-interval",
            "5",
            "--transition-window",
            "90",
            "--weather-mode",
            "fit",
            "--elevation-smoothing-distance",
            "220",
            "--elevation-min-change",
            "0.8",
        ],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert calls == [(5, 90, "fit", 220.0, 0.8)]


def test_build_default_generator_configures_transition_builder() -> None:
    generator = build_default_generator(
        transition_sample_interval=5,
        transition_window=90,
        weather_mode="auto",
        elevation_smoothing_distance=220.0,
        elevation_min_change=0.8,
    )

    extractor = generator._extractor
    summary_builder = extractor._summary_builder
    transition_builder = extractor._transition_builder

    assert summary_builder._elevation_smoothing_distance_m == 220.0
    assert summary_builder._min_elevation_change_m == 0.8
    assert transition_builder._sample_interval_s == 5
    assert transition_builder._window_s == 90
    assert isinstance(extractor._weather_provider, OpenMeteoHistoricalWeatherProvider)


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
