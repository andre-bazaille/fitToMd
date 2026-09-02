import io
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from fit_to_md.application.use_cases.generate_markdown_report import (
    GenerateMarkdownReport,
)
from fit_to_md.cli import run
from fit_to_md.domain.activity import Activity
from fit_to_md.domain.reporting.entities import FitReport, SessionSummary
from fit_to_md.domain.reporting.services import SessionSummaryBuilder, TransitionBuilder
from fit_to_md.infrastructure.fitdecode.extractor import FitdecodeActivityExtractor
from fit_to_md.infrastructure.markdown.renderer import MarkdownReportRenderer

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fit_files"

FIT_EXPECTATIONS = {
    "0001.fit": {
        "distance_km": 11.98832,
        "timer_s": 3679.86,
        "elapsed_s": 3679.86,
        "avg_hr": 140,
        "max_hr": 150,
        "avg_cad": 160,
        "avg_speed_kmh": 11.728150527465719,
        "ascent_m": 171.5,
        "descent_m": 158.8,
        "splits": 11,
        "transitions": 11,
    },
    "0002.fit": {
        "distance_km": 12.81122,
        "timer_s": 4065.255,
        "elapsed_s": 4065.255,
        "avg_hr": 140,
        "max_hr": 151,
        "avg_cad": 159,
        "avg_speed_kmh": 11.34501820918983,
        "ascent_m": 192.7,
        "descent_m": 188.5,
        "splits": 12,
        "transitions": 12,
    },
    "0003.fit": {
        "distance_km": 8.96621,
        "timer_s": 2712.293,
        "elapsed_s": 2712.293,
        "avg_hr": 146,
        "max_hr": 167,
        "avg_cad": 162,
        "avg_speed_kmh": 11.90076293379808,
        "ascent_m": 303.4,
        "descent_m": 404.8,
        "splits": 8,
        "transitions": 8,
    },
    "0004.fit": {
        "distance_km": 14.07929,
        "timer_s": 4513.122,
        "elapsed_s": 5116.335,
        "avg_hr": 138,
        "max_hr": 153,
        "avg_cad": 158,
        "avg_speed_kmh": 11.230683327417252,
        "ascent_m": 188.96862745098048,
        "descent_m": 187.87968514328816,
        "splits": 14,
        "transitions": 14,
    },
}


@dataclass(frozen=True)
class _DecodedFitFixture:
    activity: Activity
    report: FitReport


class _CapturingSessionSummaryBuilder(SessionSummaryBuilder):
    def __init__(self) -> None:
        super().__init__()
        self.activity: Activity | None = None

    def build(self, activity: Activity) -> SessionSummary:
        self.activity = activity
        return super().build(activity)


class _CachedReportExtractor:
    def __init__(self, reports: dict[str, FitReport]) -> None:
        self._reports = reports

    def extract(self, source: Path) -> FitReport:
        return self._reports[source.name]


@pytest.fixture(scope="module")
def decoded_fit_files() -> dict[str, _DecodedFitFixture]:
    decoded_files: dict[str, _DecodedFitFixture] = {}
    for file_name in sorted(FIT_EXPECTATIONS):
        summary_builder = _CapturingSessionSummaryBuilder()
        report = FitdecodeActivityExtractor(summary_builder=summary_builder).extract(
            FIXTURE_DIR / file_name
        )
        activity = summary_builder.activity
        assert activity is not None
        decoded_files[file_name] = _DecodedFitFixture(
            activity=activity,
            report=report,
        )
    return decoded_files


@pytest.mark.parametrize(
    ("file_name", "expected"),
    sorted(FIT_EXPECTATIONS.items()),
)
def test_extractor_decodes_real_fit_files(
    file_name: str,
    expected: dict[str, float | int],
    decoded_fit_files: dict[str, _DecodedFitFixture],
) -> None:
    report = decoded_fit_files[file_name].report

    assert report.summary.activity_type == "Running"
    assert report.summary.total_distance_km == pytest.approx(
        expected["distance_km"], abs=0.001
    )
    assert report.summary.total_timer_time_s == pytest.approx(
        expected["timer_s"], abs=0.01
    )
    assert report.summary.total_elapsed_time_s == pytest.approx(
        expected["elapsed_s"], abs=0.01
    )
    assert report.summary.avg_heart_rate_bpm == expected["avg_hr"]
    assert report.summary.max_heart_rate_bpm == expected["max_hr"]
    assert report.summary.avg_cadence_spm == expected["avg_cad"]
    assert report.summary.avg_speed_kmh == pytest.approx(
        expected["avg_speed_kmh"], abs=0.01
    )
    assert report.summary.total_ascent_m == pytest.approx(expected["ascent_m"], abs=0.2)
    assert report.summary.total_descent_m == pytest.approx(
        expected["descent_m"], abs=0.2
    )
    assert report.summary.avg_temperature_c is None
    assert report.summary.min_temperature_c is None
    assert report.summary.max_temperature_c is None
    assert len(report.splits) == expected["splits"]
    assert len(report.transitions) == expected["transitions"]
    assert report.transitions[0].label == "Km 1"
    assert report.transitions[0].samples[0].elapsed_seconds == pytest.approx(0.0)
    assert report.transitions[0].samples[-1].elapsed_seconds == pytest.approx(
        report.splits[0].time_seconds, abs=0.1
    )


def test_extractor_excludes_paused_time_from_real_fit_split_and_transition_durations(
    decoded_fit_files: dict[str, _DecodedFitFixture],
) -> None:
    report = decoded_fit_files["0004.fit"].report

    assert report.splits[1].time_seconds == pytest.approx(370.829, abs=0.05)
    assert report.transitions[1].samples[-1].elapsed_seconds == pytest.approx(
        370.829, abs=0.05
    )


@pytest.mark.parametrize("file_name", sorted(FIT_EXPECTATIONS))
def test_renderer_generates_markdown_for_real_fit_files(
    file_name: str,
    decoded_fit_files: dict[str, _DecodedFitFixture],
) -> None:
    renderer = MarkdownReportRenderer()

    markdown = renderer.render(decoded_fit_files[file_name].report)

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


def test_cli_uses_real_fit_file_without_external_network_by_default(
    tmp_path: Path,
    decoded_fit_files: dict[str, _DecodedFitFixture],
) -> None:
    fit_file = tmp_path / "0001.fit"
    fit_file.write_bytes((FIXTURE_DIR / "0001.fit").read_bytes())
    stdout = io.StringIO()
    stderr = io.StringIO()
    output_file = tmp_path / "0001.md"
    decoded_file = decoded_fit_files[fit_file.name]
    dense_report = replace(
        decoded_file.report,
        transitions=TransitionBuilder(sample_interval_s=5).build(decoded_file.activity),
    )

    exit_code = run(
        argv=[
            str(fit_file),
            "--dynamics-step-size",
            "5",
        ],
        report_generator=GenerateMarkdownReport(
            extractor=_CachedReportExtractor({fit_file.name: dense_report}),
            renderer=MarkdownReportRenderer(),
        ),
        stdout=stdout,
        stderr=stderr,
    )

    output = output_file.read_text(encoding="utf-8")
    stdout_output = stdout.getvalue()
    error_output = stderr.getvalue()

    assert exit_code == 0
    assert stdout_output == output
    assert error_output == ""
    assert "# FIT Report: 2020-01-01 Running" in output
    assert "0:05:" in output
    assert output.count("0:00:") == FIT_EXPECTATIONS[fit_file.name]["transitions"]


def test_transition_builder_configuration_affects_real_fit_output(
    decoded_fit_files: dict[str, _DecodedFitFixture],
) -> None:
    decoded_file = decoded_fit_files["0001.fit"]
    default_transitions = decoded_file.report.transitions
    dense_transitions = TransitionBuilder(sample_interval_s=5).build(
        decoded_file.activity
    )

    assert len(default_transitions) == len(dense_transitions)
    assert len(dense_transitions[0].samples) > len(default_transitions[0].samples)
    assert dense_transitions[0].samples[0].elapsed_seconds == pytest.approx(0.0)
    assert dense_transitions[0].samples[-1].elapsed_seconds == pytest.approx(
        decoded_file.report.splits[0].time_seconds,
        abs=0.1,
    )


def test_extractor_omits_grade_for_stationary_kilometer_samples_in_real_fit_file(
    decoded_fit_files: dict[str, _DecodedFitFixture],
) -> None:
    report = decoded_fit_files["0001.fit"].report

    assert report.transitions[0].samples[0].elapsed_seconds == pytest.approx(0.0)
    assert report.transitions[0].samples[0].speed_kmh == pytest.approx(0.0)
    assert report.transitions[0].samples[0].grade_percent is None


def test_extractor_estimates_smoothed_transition_grade_for_real_fit_file(
    decoded_fit_files: dict[str, _DecodedFitFixture],
) -> None:
    report = decoded_fit_files["0001.fit"].report

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


def test_renderer_shows_estimated_grade_for_real_fit_file_without_native_grade(
    decoded_fit_files: dict[str, _DecodedFitFixture],
) -> None:
    markdown = MarkdownReportRenderer().render(decoded_fit_files["0001.fit"].report)

    assert "## Heart Rate Dynamics (Per Kilometer)" in markdown
    assert "Grade:" in markdown
