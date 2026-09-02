import io
from datetime import UTC, datetime
from pathlib import Path

from fit_to_md.domain.privacy import FitSanitizationPolicy, SanitizationSummary
from fit_to_md.sanitize_cli import run


class StubUseCase:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, Path, datetime, FitSanitizationPolicy | None]] = []

    def execute(
        self,
        source: Path,
        destination: Path,
        target_start: datetime,
        policy: FitSanitizationPolicy | None = None,
    ) -> SanitizationSummary:
        self.calls.append((source, destination, target_start, policy))
        return SanitizationSummary(10, 20, 30)


def test_cli_uses_public_name_and_deterministic_default_date(tmp_path: Path) -> None:
    source = tmp_path / "activity.fit"
    destination = tmp_path / "running-a.fit"
    source.write_bytes(b"FIT")
    stdout = io.StringIO()
    stderr = io.StringIO()
    use_case = StubUseCase()

    exit_code = run(
        [str(source), "--output", str(destination)],
        use_case=use_case,
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert use_case.calls == [
        (
            source,
            destination,
            datetime(2020, 1, 1, 12, tzinfo=UTC),
            None,
        )
    ]
    assert "10 messages" in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_cli_reports_missing_input(tmp_path: Path) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = run(
        [
            str(tmp_path / "missing.fit"),
            "--output",
            str(tmp_path / "public.fit"),
        ],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert "not found" in stderr.getvalue()
