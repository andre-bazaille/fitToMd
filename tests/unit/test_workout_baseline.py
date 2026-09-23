"""Phase 0 fixtures and current behavior, not tests of future analytics."""

import io
from datetime import timedelta
from pathlib import Path

import pytest

from fit_to_md.application.use_cases.generate_markdown_report import (
    GenerateMarkdownReport,
)
from fit_to_md.cli import run
from fit_to_md.infrastructure.fitdecode.reader import FitdecodeActivityReader
from fit_to_md.infrastructure.markdown.renderer import MarkdownReportRenderer
from tests.support.workout import START, Variant, workout_scenario

SNAPSHOTS = Path(__file__).parents[1] / "fixtures" / "workout"


def _generator(variant: Variant = "structured") -> GenerateMarkdownReport:
    return GenerateMarkdownReport(
        reader=FitdecodeActivityReader(workout_scenario(variant).reader_factory),
        renderer=MarkdownReportRenderer(),
    )


def test_fixture_has_six_efforts_with_explicit_source_associations() -> None:
    scenario = workout_scenario()
    laps = [f for f in scenario.frames if f.name == "lap"]
    assert len(laps) == 14
    assert sum(f.values["total_distance"] for f in laps) == 5600
    assert sum(f.values["total_timer_time"] for f in laps) == 1560
    efforts = [a for a in scenario.domain_associations if a.role == "work"]
    assert [a.lap_index for a in efforts] == [2, 4, 6, 8, 10, 12]
    assert {a.step_identity for a in efforts} == {1}
    assert {a.duration_value for a in efforts} == {400}
    assert [lap.values.get("wkt_step_index") for lap in laps] == (
        [None] + [1, 2] * 6 + [None]
    )


def test_fixture_mutations_do_not_leak_between_calls() -> None:
    first = workout_scenario()
    first.frames[0].values["sport"] = "cycling"
    assert workout_scenario().frames[0].values["sport"] == "running"


def test_fixture_rejects_unknown_variant() -> None:
    with pytest.raises(ValueError, match="Unknown workout fixture"):
        workout_scenario("typo")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "variant",
    [
        "structured",
        "unlabeled",
        "lap_only",
        "sparse_hr",
        "paused",
        "unknown_pause",
        "overlapping",
        "malformed_metadata",
    ],
)
def test_fixture_variants_preserve_current_native_totals(variant: Variant) -> None:
    scenario = workout_scenario(variant)
    activity = FitdecodeActivityReader(scenario.reader_factory).read(
        Path("synthetic.fit")
    )
    assert len(activity.laps) == 14
    assert activity.session.total_distance_m == 5600
    assert activity.session.total_timer_time_s == 1560
    assert activity.session.total_elapsed_time_s == (
        1590 if variant in {"paused", "unknown_pause"} else 1560
    )
    if variant == "lap_only":
        assert activity.records == ()
    if variant == "sparse_hr":
        assert len(activity.records) == 79
        assert activity.records[2].heart_rate_bpm is None
        assert activity.records[1].timestamp - activity.records[
            0
        ].timestamp == timedelta(seconds=20)
    if variant == "paused":
        assert activity.has_active_record_timing
        assert activity.records[-1].elapsed_time_s == 1560
    if variant == "unknown_pause":
        assert not activity.has_active_record_timing
        assert activity.records[-1].elapsed_time_s == 1590
    if variant == "overlapping":
        assert activity.laps[2].start_time < activity.laps[1].end_time
    if variant == "unlabeled":
        assert scenario.domain_associations == ()
        assert all(
            "intensity" not in f.values for f in scenario.frames if f.name == "lap"
        )


@pytest.mark.parametrize("variant", ["structured", "paused", "lap_only"])
def test_default_markdown_matches_pre_feature_baseline(variant: Variant) -> None:
    markdown = _generator(variant).execute(Path("synthetic.fit"))
    assert markdown == (SNAPSHOTS / f"{variant}.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("variant", ["unlabeled", "malformed_metadata"])
def test_unsupported_metadata_does_not_invent_workout_analysis(
    variant: Variant,
) -> None:
    markdown = _generator(variant).execute(Path("synthetic.fit"))
    assert markdown == (SNAPSHOTS / "structured.md").read_text(encoding="utf-8")
    assert "400 m effort" not in markdown
    assert "Repetition Consistency" not in markdown


def test_single_file_cli_writes_and_echoes_baseline(tmp_path: Path) -> None:
    source = tmp_path / "workout.fit"
    source.write_bytes(b"synthetic placeholder: injected decoded-message reader")
    stdout, stderr = io.StringIO(), io.StringIO()
    assert (
        run([str(source)], report_generator=_generator(), stdout=stdout, stderr=stderr)
        == 0
    )
    expected = (SNAPSHOTS / "structured.md").read_text(encoding="utf-8")
    assert source.with_suffix(".md").read_text(encoding="utf-8") == expected
    assert stdout.getvalue() == expected
    assert stderr.getvalue() == ""


def test_directory_cli_skips_existing_and_nested_sources(tmp_path: Path) -> None:
    for name in ("b.fit", "a.FIT", "existing.fit"):
        (tmp_path / name).touch()
    (tmp_path / "existing.md").write_text("keep", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "ignored.fit").touch()
    scenario = workout_scenario()
    calls: list[str] = []

    def reader_factory(source: str):
        calls.append(Path(source).name)
        return scenario.reader_factory(source)

    generator = GenerateMarkdownReport(
        FitdecodeActivityReader(reader_factory),
        MarkdownReportRenderer(),
    )
    stdout, stderr = io.StringIO(), io.StringIO()
    assert (
        run([str(tmp_path)], report_generator=generator, stdout=stdout, stderr=stderr)
        == 0
    )
    expected = (SNAPSHOTS / "structured.md").read_text(encoding="utf-8")
    assert calls == ["a.FIT", "b.fit"]
    assert stdout.getvalue() == expected + expected
    assert stderr.getvalue() == ""
    assert (tmp_path / "a.md").read_text(encoding="utf-8") == expected
    assert (tmp_path / "b.md").read_text(encoding="utf-8") == expected
    assert (tmp_path / "existing.md").read_text(encoding="utf-8") == "keep"
    assert not (nested / "ignored.md").exists()


def test_fixture_uses_only_synthetic_time_and_no_location() -> None:
    scenario = workout_scenario()
    assert scenario.frames[-1].values["start_time"] == START
    assert not any(
        "position_lat" in f.values or "position_long" in f.values
        for f in scenario.frames
    )
