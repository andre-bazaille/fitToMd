from __future__ import annotations

import os
import subprocess
from pathlib import Path

PROJECT_DIRECTORY = Path(__file__).parents[2]
CI_SCRIPT = PROJECT_DIRECTORY / "ci.sh"


def test_local_ci_script_runs_all_quality_checks(tmp_path: Path) -> None:
    log_file = tmp_path / "calls.log"
    python_stub = _write_python_stub(tmp_path, exit_on_format_check=False)

    result = subprocess.run(
        [str(CI_SCRIPT)],
        cwd=tmp_path,
        env={
            **os.environ,
            "FIT_TO_MD_PYTHON": str(python_stub),
            "FIT_TO_MD_CI_TEST_LOG": str(log_file),
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert log_file.read_text(encoding="utf-8").splitlines() == [
        "-m ruff check src tests",
        "-m ruff format --check src tests",
        "-m mypy",
        "-m pytest",
    ]


def test_local_ci_script_stops_after_a_failed_check(tmp_path: Path) -> None:
    log_file = tmp_path / "calls.log"
    python_stub = _write_python_stub(tmp_path, exit_on_format_check=True)

    result = subprocess.run(
        [str(CI_SCRIPT)],
        env={
            **os.environ,
            "FIT_TO_MD_PYTHON": str(python_stub),
            "FIT_TO_MD_CI_TEST_LOG": str(log_file),
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 3
    assert log_file.read_text(encoding="utf-8").splitlines() == [
        "-m ruff check src tests",
        "-m ruff format --check src tests",
    ]


def _write_python_stub(tmp_path: Path, *, exit_on_format_check: bool) -> Path:
    python_stub = tmp_path / "python-stub"
    failure = (
        '\nif [ "$*" = "-m ruff format --check src tests" ]; then exit 3; fi'
        if exit_on_format_check
        else ""
    )
    python_stub.write_text(
        f'#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$FIT_TO_MD_CI_TEST_LOG"{failure}\nexit 0\n',
        encoding="utf-8",
    )
    python_stub.chmod(0o755)
    return python_stub
