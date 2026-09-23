"""CLI and configuration wiring for optional workout reporting."""

import io
from datetime import datetime
from pathlib import Path

import pytest

import fit_to_md.cli as cli
from fit_to_md.application.use_cases.generate_markdown_report import (
    ReportGenerationOptions,
    WorkoutReportMode,
)
from fit_to_md.domain.activity import Activity, ActivitySession
from fit_to_md.domain.activity.ports import InvalidActivityError
from fit_to_md.domain.reporting.heart_rate_zones import HeartRateZoneBoundaries


class CountingReader:
    def __init__(self) -> None:
        self.calls: list[Path] = []

    def read(self, source: Path) -> Activity:
        self.calls.append(source)
        if source.name == "broken.fit":
            raise InvalidActivityError("damaged")
        return Activity(
            session=ActivitySession(start_time=datetime(2026, 9, 17, 8, 0)),
            sport="running",
        )


@pytest.mark.parametrize("directory", (False, True))
@pytest.mark.parametrize("lap_mode", (False, True))
@pytest.mark.parametrize("with_zones", (False, True))
def test_flags_generate_requested_sections_for_files_and_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    directory: bool,
    lap_mode: bool,
    with_zones: bool,
) -> None:
    source = tmp_path / "activity.fit"
    source.write_bytes(b"FIT")
    reader = CountingReader()
    monkeypatch.setattr(cli, "FitdecodeActivityReader", lambda: reader)
    args = [str(tmp_path if directory else source)]
    if lap_mode:
        args.extend(("--workout-report", "laps"))
    if with_zones:
        args.extend(("--hr-zone-boundaries", "130,145,160,175"))

    assert cli.run(args, stdout=io.StringIO(), stderr=io.StringIO()) == 0

    markdown = source.with_suffix(".md").read_text(encoding="utf-8")
    assert ("## Workout Breakdown" in markdown) is lap_mode
    assert ("## Repetition Consistency" in markdown) is lap_mode
    assert ("## Recovery Heart-Rate Changes" in markdown) is lap_mode
    assert ("## Heart-Rate Zones" in markdown) is with_zones
    assert reader.calls == [source]


def test_config_options_are_independent_and_explicit_off_overrides_laps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "activity.fit"
    source.write_bytes(b"FIT")
    config = tmp_path / "options.conf"
    config.write_text(
        "workout_report = laps\nhr_zone_boundaries = 130,145,160,175\n",
        encoding="utf-8",
    )
    reader = CountingReader()
    monkeypatch.setattr(cli, "FitdecodeActivityReader", lambda: reader)

    assert cli.run([str(source), "--config", str(config)]) == 0
    configured = source.with_suffix(".md").read_text(encoding="utf-8")
    assert "## Workout Breakdown" in configured
    assert "## Heart-Rate Zones" in configured

    assert (
        cli.run(
            [str(source), "--config", str(config), "--workout-report", "off"],
            stdout=io.StringIO(),
        )
        == 0
    )
    overridden = source.with_suffix(".md").read_text(encoding="utf-8")
    assert "## Workout Breakdown" not in overridden
    assert "## Heart-Rate Zones" in overridden
    assert reader.calls == [source, source]


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--workout-report", "unknown"),
        ("--hr-zone-boundaries", "130,145,160"),
        ("--hr-zone-boundaries", "130,145,160,175,190"),
        ("--hr-zone-boundaries", "130,145,no,175"),
        ("--hr-zone-boundaries", "0,145,160,175"),
        ("--hr-zone-boundaries", "145,130,160,175"),
        ("--hr-zone-boundaries", "130,130,160,175"),
        ("--hr-zone-boundaries", "1_30,145,160,175"),
    ],
)
@pytest.mark.parametrize("from_config", (False, True))
def test_invalid_options_fail_before_reader_or_provider_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    option: str,
    value: str,
    from_config: bool,
) -> None:
    source = tmp_path / "activity.fit"
    source.write_bytes(b"FIT")
    reader = CountingReader()
    provider_creations: list[str] = []
    monkeypatch.setattr(cli, "FitdecodeActivityReader", lambda: reader)
    monkeypatch.setattr(
        cli,
        "OpenMeteoHistoricalWeatherProvider",
        lambda: provider_creations.append("weather"),
    )
    config = tmp_path / "options.conf"
    if from_config:
        config.write_text(f"{option.removeprefix('--')} = {value}\n", encoding="utf-8")
        args = [str(source), "--config", str(config), "--weather-mode", "auto"]
    else:
        args = [str(source), option, value, "--weather-mode", "auto"]

    with pytest.raises(SystemExit) as error:
        cli.run(args)

    assert error.value.code == 2
    assert reader.calls == []
    assert provider_creations == []
    assert not source.with_suffix(".md").exists()


def test_configured_options_reach_default_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "activity.fit"
    source.write_bytes(b"FIT")
    config = tmp_path / "options.conf"
    config.write_text(
        "workout-report = laps\nhr-zone-boundaries = 130,145,160,175\n",
        encoding="utf-8",
    )
    options_seen: list[ReportGenerationOptions] = []
    real_runtime = cli._build_default_runtime

    def capture_runtime(**kwargs) -> cli._DefaultRuntime:
        options_seen.append(kwargs["report_options"])
        return real_runtime(**kwargs)

    monkeypatch.setattr(cli, "_build_default_runtime", capture_runtime)
    monkeypatch.setattr(cli, "FitdecodeActivityReader", CountingReader)

    assert cli.run([str(source), "--config", str(config)], stdout=io.StringIO()) == 0
    assert options_seen == [
        ReportGenerationOptions(
            WorkoutReportMode.LAPS, HeartRateZoneBoundaries((130, 145, 160, 175))
        )
    ]


def test_directory_skips_existing_and_continues_after_failure_with_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skipped = tmp_path / "a.fit"
    broken = tmp_path / "broken.fit"
    good = tmp_path / "z.fit"
    for source in (skipped, broken, good):
        source.write_bytes(b"FIT")
    skipped.with_suffix(".md").write_text("existing", encoding="utf-8")
    reader = CountingReader()
    monkeypatch.setattr(cli, "FitdecodeActivityReader", lambda: reader)
    stderr = io.StringIO()

    code = cli.run(
        [
            str(tmp_path),
            "--workout-report",
            "laps",
            "--hr-zone-boundaries",
            "130,145,160,175",
        ],
        stdout=io.StringIO(),
        stderr=stderr,
    )

    assert code == 1
    assert reader.calls == [broken, good]
    assert skipped.with_suffix(".md").read_text(encoding="utf-8") == "existing"
    assert "## Workout Breakdown" in good.with_suffix(".md").read_text(encoding="utf-8")
    assert "## Heart-Rate Zones" in good.with_suffix(".md").read_text(encoding="utf-8")
    assert "Invalid FIT file" in stderr.getvalue()


def test_workout_options_keep_source_overwrite_protection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "activity.fit"
    source.write_bytes(b"FIT")
    monkeypatch.setattr(cli, "FitdecodeActivityReader", CountingReader)

    code = cli.run(
        [str(source), "--workout-report", "laps", "-o", str(source)],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert code == 2
    assert source.read_bytes() == b"FIT"


def test_cli_zone_boundaries_override_configured_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "activity.fit"
    source.write_bytes(b"FIT")
    config = tmp_path / "options.conf"
    config.write_text("hr-zone-boundaries = 110,125,140,155\n", encoding="utf-8")
    monkeypatch.setattr(cli, "FitdecodeActivityReader", CountingReader)

    assert (
        cli.run(
            [
                str(source),
                "--config",
                str(config),
                "--hr-zone-boundaries",
                "130,145,160,175",
            ],
            stdout=io.StringIO(),
        )
        == 0
    )
    markdown = source.with_suffix(".md").read_text(encoding="utf-8")
    assert "130" in markdown and "175" in markdown
    assert "110" not in markdown


def test_activity_time_collision_planning_ignores_workout_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "a.fit"
    second = tmp_path / "b.fit"
    first.write_bytes(b"FIT")
    second.write_bytes(b"FIT")
    reader = CountingReader()
    monkeypatch.setattr(cli, "FitdecodeActivityReader", lambda: reader)
    stderr = io.StringIO()

    code = cli.run(
        [
            str(tmp_path),
            "--output-by-activity-time",
            "--workout-report",
            "laps",
            "--hr-zone-boundaries",
            "130,145,160,175",
        ],
        stdout=io.StringIO(),
        stderr=stderr,
    )

    assert code == 1
    assert reader.calls == [first, second]
    assert "Output path collision" in stderr.getvalue()
    assert not list(tmp_path.glob("*.md"))
