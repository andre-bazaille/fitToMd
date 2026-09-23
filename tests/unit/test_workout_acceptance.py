"""Release acceptance through binary FIT decoding and report generation."""

import io
from pathlib import Path

import fitdecode
import pytest

import fit_to_md.cli as cli
from fit_to_md.application.use_cases.generate_markdown_report import (
    ReportGenerationOptions,
    WorkoutReportMode,
)
from fit_to_md.domain.activity.workout import LapRole
from fit_to_md.domain.reporting.heart_rate_zones import HeartRateZoneBoundaries
from fit_to_md.infrastructure.fitdecode.reader import FitdecodeActivityReader
from tests.support.binary_workout import write_binary_workout


def _configured_report(source: Path):
    return cli.build_default_generator(
        report_options=ReportGenerationOptions(
            WorkoutReportMode.LAPS,
            HeartRateZoneBoundaries((130, 145, 160, 175)),
        )
    ).execute_detailed(source)


def test_binary_structured_workout_converts_with_six_repetitions_and_recoveries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "structured.fit"
    write_binary_workout(source)
    with fitdecode.FitReader(source, check_crc=fitdecode.CrcCheck.RAISE) as decoder:
        names = [
            frame.name
            for frame in decoder
            if frame.frame_type == fitdecode.FIT_FRAME_DATA
        ]
    assert names.count("lap") == 14
    assert names.count("workout_step") == 3
    assert names.count("record") == 313

    def unexpected_provider() -> None:
        raise AssertionError("default conversion constructed an external provider")

    monkeypatch.setattr(cli, "OpenMeteoHistoricalWeatherProvider", unexpected_provider)
    monkeypatch.setattr(cli, "OpenTopoDataElevationProvider", unexpected_provider)
    stdout, stderr = io.StringIO(), io.StringIO()
    code = cli.run(
        [
            str(source),
            "--workout-report",
            "laps",
            "--hr-zone-boundaries",
            "130,145,160,175",
        ],
        stdout=stdout,
        stderr=stderr,
    )
    assert code == 0
    assert stderr.getvalue() == ""
    markdown = source.with_suffix(".md").read_text(encoding="utf-8")
    assert markdown == stdout.getvalue()
    assert (
        markdown.index("## Workout Breakdown")
        < markdown.index("## Repetition Consistency")
        < markdown.index("## Recovery Heart-Rate Changes")
        < markdown.index("## Heart-Rate Zones")
    )
    assert markdown.count("| 400 m effort | work |") == 6
    assert markdown.count("| Recovery | recovery |") == 6
    assert "| Step 1 | 400 m; open target | 6 | 4:10/km" in markdown
    assert markdown.count("| -30 bpm |") == 6
    assert "26:00 covered / 26:00 active (100.0%)" in markdown
    assert "**Per-lap zones**" in markdown

    result = _configured_report(source)
    assert result.markdown == markdown
    workout = result.report.workout
    zones = result.report.zones
    assert workout is not None and zones is not None
    assert len(workout.laps) == 14
    assert [row.role for row in workout.laps].count(LapRole.WORK) == 6
    assert [row.role for row in workout.laps].count(LapRole.RECOVERY) == 6
    assert len(workout.repetitions.groups) == 1
    group = workout.repetitions.groups[0]
    assert group.count == 6
    assert group.mean == 250.0
    assert group.coefficient_variation_percent == 0.0
    assert [change.delta_bpm for change in workout.recoveries.changes] == [-30.0] * 6
    assert zones.session.zone_seconds == (0.0, 520.0, 240.0, 800.0, 0.0)
    assert zones.session.coverage.covered_seconds == 1560.0
    assert zones.session.coverage.total_active_seconds == 1560.0
    assert len(zones.laps) == 14


def test_binary_unlabeled_workout_renders_laps_without_inferred_structure(
    tmp_path: Path,
) -> None:
    source = tmp_path / "unlabeled.fit"
    write_binary_workout(source, "unlabeled")
    result = _configured_report(source)
    workout = result.report.workout
    assert workout is not None
    assert len(workout.laps) == 14
    assert all(row.role is LapRole.UNKNOWN for row in workout.laps)
    assert all(row.workout_step is None for row in workout.laps)
    assert workout.repetitions.groups == ()
    assert workout.recoveries.changes == ()
    assert result.markdown.count("| unknown |") == 14
    assert "400 m effort" not in result.markdown
    assert "Comparable repetitions unavailable" in result.markdown
    assert "No explicitly marked recovery laps recorded." in result.markdown


def test_binary_workout_default_conversion_omits_optional_sections(
    tmp_path: Path,
) -> None:
    source = tmp_path / "default.fit"
    write_binary_workout(source)
    activity = FitdecodeActivityReader().read(source)
    assert len(activity.laps) == 14
    assert activity.active_timeline.is_known
    stdout = io.StringIO()
    assert cli.run([str(source)], stdout=stdout, stderr=io.StringIO()) == 0
    markdown = source.with_suffix(".md").read_text(encoding="utf-8")
    assert markdown == stdout.getvalue()
    assert "## Kilometric Splits" in markdown
    assert "## Workout Breakdown" not in markdown
    assert "## Heart-Rate Zones" not in markdown


def test_auto_laps_without_step_links_do_not_gain_workout_roles(tmp_path: Path) -> None:
    from fit_to_md.application.use_cases.generate_markdown_report import (
        GenerateMarkdownReport,
    )
    from fit_to_md.infrastructure.markdown.renderer import MarkdownReportRenderer
    from tests.support.workout import workout_scenario

    scenario = workout_scenario("unlabeled")
    for frame in scenario.frames:
        if frame.name == "lap":
            frame.values["lap_trigger"] = "distance"
    generator = GenerateMarkdownReport(
        FitdecodeActivityReader(scenario.reader_factory),
        MarkdownReportRenderer(),
        options=ReportGenerationOptions(WorkoutReportMode.LAPS),
    )

    result = generator.execute_detailed(tmp_path / "auto.fit")

    assert result.report.workout is not None
    assert len(result.report.workout.laps) == 14
    assert all(row.role is LapRole.UNKNOWN for row in result.report.workout.laps)
    assert result.report.workout.repetitions.groups == ()
    assert result.report.workout.recoveries.changes == ()
    assert result.markdown.count("| unknown |") == 14


def test_binary_workout_fixture_rejects_unsupported_variant(tmp_path: Path) -> None:
    source = tmp_path / "invalid.fit"
    with pytest.raises(ValueError, match="structured or unlabeled"):
        write_binary_workout(source, "paused")
    assert not source.exists()
