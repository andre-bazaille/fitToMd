from __future__ import annotations

import io
from pathlib import Path

import pytest

import fit_to_md.cli as cli
from fit_to_md.cli import build_default_generator, run


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
    calls: list[tuple[int, int]] = []

    def fake_build_default_generator(
        transition_sample_interval: int = 10,
        transition_window: int = 60,
    ) -> StubGenerator:
        calls.append((transition_sample_interval, transition_window))
        return StubGenerator("# FIT Report\n")

    monkeypatch.setattr(cli, "build_default_generator", fake_build_default_generator)

    exit_code = run(
        argv=[
            str(fit_file),
            "--transition-sample-interval",
            "5",
            "--transition-window",
            "90",
        ],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert calls == [(5, 90)]


def test_build_default_generator_configures_transition_builder() -> None:
    generator = build_default_generator(transition_sample_interval=5, transition_window=90)

    extractor = generator._extractor
    transition_builder = extractor._transition_builder

    assert transition_builder._sample_interval_s == 5
    assert transition_builder._window_s == 90
