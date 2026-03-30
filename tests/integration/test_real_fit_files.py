from __future__ import annotations

import io
from pathlib import Path

import pytest

from fit_to_md.cli import run
from fit_to_md.infrastructure.fitdecode.builders import TransitionBuilder
from fit_to_md.infrastructure.fitdecode.extractor import FitdecodeActivityExtractor
from fit_to_md.infrastructure.markdown.renderer import MarkdownReportRenderer


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fit_files"

FIT_EXPECTATIONS = {
    "2026-03-24-12-20-27.fit": {
        "distance_km": 11.98832,
        "timer_s": 3679.86,
        "avg_hr": 140,
        "max_hr": 150,
        "avg_cad": 160,
        "avg_speed_kmh": 11.728150527465719,
        "ascent_m": 171.5,
        "descent_m": 158.8,
        "splits": 11,
        "transitions": 11,
    },
    "2026-03-27-08-13-16.fit": {
        "distance_km": 12.81122,
        "timer_s": 4065.255,
        "avg_hr": 140,
        "max_hr": 151,
        "avg_cad": 159,
        "avg_speed_kmh": 11.34501820918983,
        "ascent_m": 192.7,
        "descent_m": 188.5,
        "splits": 12,
        "transitions": 12,
    },
    "2026-03-28-10-24-00.fit": {
        "distance_km": 8.96621,
        "timer_s": 2712.293,
        "avg_hr": 146,
        "max_hr": 167,
        "avg_cad": 162,
        "avg_speed_kmh": 11.90076293379808,
        "ascent_m": 303.4,
        "descent_m": 404.8,
        "splits": 8,
        "transitions": 8,
    },
}


@pytest.mark.parametrize(
    ("file_name", "expected"),
    sorted(FIT_EXPECTATIONS.items()),
)
def test_extractor_decodes_real_fit_files(file_name: str, expected: dict[str, float | int]) -> None:
    extractor = FitdecodeActivityExtractor()

    report = extractor.extract(FIXTURE_DIR / file_name)

    assert report.summary.activity_type == "Running"
    assert report.summary.total_distance_km == pytest.approx(expected["distance_km"], abs=0.001)
    assert report.summary.total_timer_time_s == pytest.approx(expected["timer_s"], abs=0.01)
    assert report.summary.total_elapsed_time_s == pytest.approx(expected["timer_s"], abs=0.01)
    assert report.summary.avg_heart_rate_bpm == expected["avg_hr"]
    assert report.summary.max_heart_rate_bpm == expected["max_hr"]
    assert report.summary.avg_cadence_spm == expected["avg_cad"]
    assert report.summary.avg_speed_kmh == pytest.approx(expected["avg_speed_kmh"], abs=0.01)
    assert report.summary.total_ascent_m == pytest.approx(expected["ascent_m"], abs=0.2)
    assert report.summary.total_descent_m == pytest.approx(expected["descent_m"], abs=0.2)
    assert report.summary.avg_temperature_c is None
    assert report.summary.min_temperature_c is None
    assert report.summary.max_temperature_c is None
    assert len(report.splits) == expected["splits"]
    assert len(report.transitions) == expected["transitions"]
    assert report.transitions[0].label == "Km 1"
    assert report.transitions[0].samples[0].elapsed_seconds == pytest.approx(0.0)
    assert report.transitions[0].samples[-1].elapsed_seconds == pytest.approx(report.splits[0].time_seconds, abs=0.1)


@pytest.mark.parametrize("file_name", sorted(FIT_EXPECTATIONS))
def test_renderer_generates_markdown_for_real_fit_files(file_name: str) -> None:
    extractor = FitdecodeActivityExtractor()
    renderer = MarkdownReportRenderer()

    markdown = renderer.render(extractor.extract(FIXTURE_DIR / file_name))

    assert markdown.startswith("# FIT Report:")
    assert "## Session Summary" in markdown
    assert "## Kilometric Splits" in markdown
    assert "## Heart Rate Dynamics (Per Kilometer)" in markdown
    assert "- **Avg Pace:**" in markdown
    assert "- **Weather:** FIT and historical weather data unavailable" in markdown
    assert "| Km | Time | Pace | Elev +/- | Avg HR | Max HR | Avg Cad |" in markdown
    assert "(Pace:" in markdown
    assert "- **Km 1**" in markdown
    assert "0:00:" in markdown


def test_cli_uses_real_fit_file_and_respects_transition_options() -> None:
    fit_file = FIXTURE_DIR / "2026-03-24-12-20-27.fit"
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = run(
        argv=[
            str(fit_file),
            "--dynamics-step-size",
            "5",
            "--weather-mode",
            "fit",
        ],
        stdout=stdout,
        stderr=stderr,
    )

    output = stdout.getvalue()
    error_output = stderr.getvalue()

    assert exit_code == 0
    assert "OpenTopoData progress: request" in error_output
    assert "OpenTopoData public API calls this run:" in error_output
    assert "# FIT Report: 2026-03-24 Running" in output
    assert "0:05:" in output
    assert output.count("0:00:") == FIT_EXPECTATIONS[fit_file.name]["transitions"]


def test_extractor_transition_builder_configuration_affects_real_fit_output() -> None:
    fit_file = FIXTURE_DIR / "2026-03-24-12-20-27.fit"
    default_report = FitdecodeActivityExtractor().extract(fit_file)
    dense_report = FitdecodeActivityExtractor(
        transition_builder=TransitionBuilder(sample_interval_s=5)
    ).extract(fit_file)

    assert len(default_report.transitions) == len(dense_report.transitions)
    assert len(dense_report.transitions[0].samples) > len(default_report.transitions[0].samples)
    assert dense_report.transitions[0].samples[0].elapsed_seconds == pytest.approx(0.0)
    assert dense_report.transitions[0].samples[-1].elapsed_seconds == pytest.approx(
        dense_report.splits[0].time_seconds,
        abs=0.1,
    )


def test_extractor_omits_grade_for_stationary_kilometer_samples_in_real_fit_file() -> None:
    fit_file = FIXTURE_DIR / "2026-03-24-12-20-27.fit"

    report = FitdecodeActivityExtractor().extract(fit_file)

    assert report.transitions[0].samples[0].elapsed_seconds == pytest.approx(0.0)
    assert report.transitions[0].samples[0].speed_kmh == pytest.approx(0.0)
    assert report.transitions[0].samples[0].grade_percent is None


def test_extractor_estimates_smoothed_transition_grade_for_real_fit_file() -> None:
    fit_file = FIXTURE_DIR / "2026-03-24-12-20-27.fit"

    report = FitdecodeActivityExtractor().extract(fit_file)

    transition_grades = [
        sample.grade_percent
        for transition in report.transitions
        for sample in transition.samples
        if sample.speed_kmh is not None and sample.speed_kmh > 0
    ]

    assert transition_grades
    assert max(abs(grade) for grade in transition_grades if grade is not None) < 22.0
    assert min(transition_grades) < -15.0
    assert max(transition_grades) > 15.0


def test_renderer_shows_estimated_grade_for_real_fit_file_without_native_grade() -> None:
    fit_file = FIXTURE_DIR / "2026-03-24-12-20-27.fit"

    markdown = MarkdownReportRenderer().render(FitdecodeActivityExtractor().extract(fit_file))

    assert "## Heart Rate Dynamics (Per Kilometer)" in markdown
    assert "Grade:" in markdown