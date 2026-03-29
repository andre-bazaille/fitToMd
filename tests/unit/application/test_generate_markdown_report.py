from pathlib import Path

from fit_to_md.application.use_cases.generate_markdown_report import GenerateMarkdownReport
from fit_to_md.domain.reporting.entities import FitReport, SessionSummary


class StubExtractor:
    def __init__(self, report: FitReport) -> None:
        self.report = report
        self.calls: list[Path] = []

    def extract(self, source: Path) -> FitReport:
        self.calls.append(source)
        return self.report


class StubRenderer:
    def __init__(self) -> None:
        self.calls: list[FitReport] = []

    def render(self, report: FitReport) -> str:
        self.calls.append(report)
        return "rendered markdown"


def test_generate_markdown_report_delegates_to_ports() -> None:
    report = FitReport(
        summary=SessionSummary(
            start_time=None,
            activity_name="Morning Run",
            activity_type="running",
            total_distance_km=10.0,
            total_timer_time_s=3000.0,
            total_elapsed_time_s=3050.0,
            total_ascent_m=100.0,
            total_descent_m=95.0,
            avg_heart_rate_bpm=145,
            max_heart_rate_bpm=172,
            avg_cadence_spm=170,
            avg_speed_kmh=12.0,
        )
    )
    extractor = StubExtractor(report)
    renderer = StubRenderer()
    use_case = GenerateMarkdownReport(extractor=extractor, renderer=renderer)

    result = use_case.execute(Path("activity.fit"))

    assert result == "rendered markdown"
    assert extractor.calls == [Path("activity.fit")]
    assert renderer.calls == [report]
