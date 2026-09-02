from datetime import datetime

from fit_to_md.domain.reporting.entities import (
    FitReport,
    SessionSummary,
    Split,
    TransitionDynamics,
    TransitionSample,
    WeatherSummary,
)
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
            avg_temperature_c=18.4,
            min_temperature_c=15.0,
            max_temperature_c=22.5,
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
                label="Km 4",
                samples=(
                    TransitionSample(
                        elapsed_seconds=10.0,
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
    assert "- **Start Time:** 2026-03-29 06:30:00" in markdown
    assert "- **Activity Type:** running" in markdown
    assert "- **Total Distance:** 10.50 km" in markdown
    assert "- **Avg Pace:** 5:15/km" in markdown
    assert "- **Weather:** Avg 18.4C / Min 15.0C / Max 22.5C [fit]" in markdown
    assert "| 1 | 5:30 | 5:30 | +5m | 125 | 135 | 168 |" in markdown
    assert "## Heart Rate Dynamics (Per Kilometer)" in markdown
    assert "- **Km 4**" in markdown
    assert "0:10: 165 bpm (Pace: -, Grade: 0.00%)" in markdown


def test_render_omits_missing_transition_grade() -> None:
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
            avg_temperature_c=18.4,
            min_temperature_c=15.0,
            max_temperature_c=22.5,
        ),
        transitions=(
            TransitionDynamics(
                label="Km 1",
                samples=(
                    TransitionSample(
                        elapsed_seconds=0.0,
                        heart_rate_bpm=155,
                        speed_kmh=12.0,
                        grade_percent=None,
                    ),
                ),
            ),
        ),
    )

    markdown = MarkdownReportRenderer().render(report)

    assert "0:00: 155 bpm (Pace: 5:00/km)" in markdown
    assert "Grade:" not in markdown


class CustomSection:
    heading = "Custom"

    def render_lines(self, report: FitReport):
        return ["- **Extra:** enabled"]


def test_render_accepts_custom_section_renderers() -> None:
    report = FitReport(
        summary=SessionSummary(
            start_time=datetime(2026, 3, 29, 6, 30, 0),
            activity_name="Long Run",
            activity_type="running",
            total_distance_km=None,
            total_timer_time_s=None,
            total_elapsed_time_s=None,
            total_ascent_m=None,
            total_descent_m=None,
            avg_heart_rate_bpm=None,
            max_heart_rate_bpm=None,
            avg_cadence_spm=None,
            avg_speed_kmh=None,
            avg_temperature_c=None,
            min_temperature_c=None,
            max_temperature_c=None,
        )
    )

    renderer = MarkdownReportRenderer(section_renderers=(CustomSection(),))

    markdown = renderer.render(report)

    assert "## Custom" in markdown
    assert "- **Extra:** enabled" in markdown


def test_render_prefers_enriched_weather_summary() -> None:
    report = FitReport(
        summary=SessionSummary(
            start_time=datetime(2026, 3, 29, 6, 30, 0),
            activity_name="Long Run",
            activity_type="running",
            total_distance_km=None,
            total_timer_time_s=None,
            total_elapsed_time_s=None,
            total_ascent_m=None,
            total_descent_m=None,
            avg_heart_rate_bpm=None,
            max_heart_rate_bpm=None,
            avg_cadence_spm=None,
            avg_speed_kmh=None,
            avg_temperature_c=None,
            min_temperature_c=None,
            max_temperature_c=None,
            weather=WeatherSummary(
                source="historical",
                temperature_c=15.2,
                apparent_temperature_c=14.8,
                condition_summary="Sunny",
                wind_speed_kmh=19.0,
                wind_direction_label="SW",
            ),
        )
    )

    markdown = MarkdownReportRenderer().render(report)

    assert (
        "- **Weather:** 15.2C, feels like 14.8C, Sunny, Wind 19.0 km/h SW [historical]"
        in markdown
    )


def test_render_keeps_speed_units_for_non_running_activities() -> None:
    report = FitReport(
        summary=SessionSummary(
            start_time=datetime(2026, 3, 29, 6, 30, 0),
            activity_name="Ride",
            activity_type="cycling",
            total_distance_km=20.0,
            total_timer_time_s=2400.0,
            total_elapsed_time_s=2400.0,
            total_ascent_m=200.0,
            total_descent_m=200.0,
            avg_heart_rate_bpm=140,
            max_heart_rate_bpm=170,
            avg_cadence_spm=90,
            avg_speed_kmh=30.0,
            avg_temperature_c=None,
            min_temperature_c=None,
            max_temperature_c=None,
        ),
        transitions=(
            TransitionDynamics(
                label="Km 1",
                samples=(
                    TransitionSample(
                        elapsed_seconds=0.0,
                        heart_rate_bpm=155,
                        speed_kmh=30.0,
                        grade_percent=1.2,
                    ),
                ),
            ),
        ),
    )

    markdown = MarkdownReportRenderer().render(report)

    assert "- **Avg Speed:** 30.00 km/h" in markdown
    assert "0:00: 155 bpm (Speed: 30.00 km/h, Grade: 1.20%)" in markdown
