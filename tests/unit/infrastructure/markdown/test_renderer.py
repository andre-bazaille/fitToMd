from datetime import datetime

from fit_to_md.domain.reporting.entities import FitReport, SessionSummary, Split, TransitionDynamics, TransitionSample
from fit_to_md.infrastructure.markdown.renderer import MarkdownReportRenderer


def test_render_formats_expected_markdown_sections() -> None:
    report = FitReport(
        summary=SessionSummary(
            start_time=datetime(2026, 3, 29, 6, 30, 0),
            activity_name="Long Run",
            activity_type="running",
            total_distance_km=10.5,
            total_timer_time_s=3312.0,
            total_elapsed_time_s=3340.0,
            total_ascent_m=120.0,
            total_descent_m=115.0,
            avg_heart_rate_bpm=142,
            max_heart_rate_bpm=175,
            avg_cadence_spm=172,
            avg_speed_kmh=11.42,
        ),
        splits=(
            Split(
                kilometer=1,
                time_seconds=330.0,
                pace_seconds_per_km=330.0,
                elevation_delta_m=5.0,
                avg_heart_rate_bpm=125,
                max_heart_rate_bpm=135,
                avg_cadence_spm=168,
            ),
        ),
        transitions=(
            TransitionDynamics(
                label="Stop of Lap 4 to Start of Lap 5",
                samples=(
                    TransitionSample(
                        offset_seconds=10,
                        heart_rate_bpm=165,
                        speed_kmh=0.0,
                        grade_percent=0.0,
                    ),
                ),
            ),
        ),
    )

    markdown = MarkdownReportRenderer().render(report)

    assert "# FIT Report: 2026-03-29 Long Run" in markdown
    assert "## Session Summary" in markdown
    assert "- **Total Distance:** 10.50 km" in markdown
    assert "| 1 | 5:30 | 5:30 | +5m | 125 | 135 | 168 |" in markdown
    assert "- **Transition: Stop of Lap 4 to Start of Lap 5**" in markdown
    assert "T+10s: 165 bpm (Speed: 0.00 km/h, Grade: 0.00%)" in markdown
