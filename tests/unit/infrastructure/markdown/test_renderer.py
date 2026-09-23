from datetime import datetime

import pytest

from fit_to_md.domain.activity.workout import (
    DurationKind,
    LapRole,
    TargetKind,
    WorkoutDuration,
    WorkoutIssue,
    WorkoutTarget,
)
from fit_to_md.domain.reporting.entities import (
    FitReport,
    HeartRateZoneReport,
    LapZoneMeasurement,
    MeasurementCoverage,
    MeasurementIssue,
    MeasurementReason,
    NativeLapRow,
    RecoveryAnalysis,
    RecoveryChange,
    RecoveryReason,
    RepetitionAnalysis,
    RepetitionExclusion,
    RepetitionGroup,
    RepetitionMetric,
    RepetitionReason,
    SessionSummary,
    Split,
    TransitionDynamics,
    TransitionSample,
    WeatherSummary,
    WorkoutReport,
    ZoneMeasurement,
)
from fit_to_md.domain.reporting.heart_rate_zones import HeartRateZoneBoundaries
from fit_to_md.infrastructure.markdown.renderer import MarkdownReportRenderer
from fit_to_md.infrastructure.markdown.sections import (
    SessionSummarySectionRenderer,
    SplitSectionRenderer,
    TransitionSectionRenderer,
)


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
            sport="running",
        ),
        splits=(
            Split(
                kilometer=1,
                distance_m=1000.0,
                is_partial=False,
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
    assert "| 1 | 1.00 km | 5:30 | 5:30 | +5m | 125 | 135 | 168 |" in markdown
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
            sport="running",
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


def test_render_labels_final_partial_split_and_normalized_pace() -> None:
    report = FitReport(
        summary=SessionSummary(
            start_time=None,
            activity_name=None,
            activity_type=None,
            total_distance_km=10.772,
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
        ),
        splits=(
            Split(
                kilometer=11,
                distance_m=772.07,
                is_partial=True,
                time_seconds=216.65,
                pace_seconds_per_km=280.61,
                elevation_delta_m=6.0,
                avg_heart_rate_bpm=162,
                max_heart_rate_bpm=174,
                avg_cadence_spm=162,
            ),
        ),
    )

    markdown = MarkdownReportRenderer().render(report)

    assert "| 10.00–10.77 | 0.77 km | 3:36 | 4:40 |" in markdown


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
            sport="running",
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
            sport="running",
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


def test_render_prefers_fit_temperature_when_both_weather_sources_are_present() -> None:
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
            avg_temperature_c=20.0,
            min_temperature_c=None,
            max_temperature_c=None,
            sport="running",
            weather=WeatherSummary(
                source="historical",
                temperature_c=5.0,
                apparent_temperature_c=3.0,
                condition_summary="Cloudy",
                wind_speed_kmh=None,
                wind_direction_label=None,
            ),
        )
    )

    markdown = MarkdownReportRenderer().render(report)

    assert "- **Weather:** Avg 20.0C / Min - / Max - [fit]" in markdown
    assert "[historical]" not in markdown


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
            sport="cycling",
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


@pytest.mark.parametrize(
    ("activity_type", "sport", "expected_summary", "expected_dynamics"),
    [
        ("Treadmill", "running", "- **Avg Pace:** 5:33/km", "Pace: 5:33/km"),
        ("Track", "running", "- **Avg Pace:** 5:33/km", "Pace: 5:33/km"),
        ("Running", "running", "- **Avg Pace:** 5:33/km", "Pace: 5:33/km"),
        ("Trail Running", "running", "- **Avg Pace:** 5:33/km", "Pace: 5:33/km"),
        (
            "Cycling",
            "cycling",
            "- **Avg Speed:** 10.80 km/h",
            "Speed: 10.80 km/h",
        ),
    ],
)
def test_render_selects_speed_units_from_canonical_sport(
    activity_type: str,
    sport: str,
    expected_summary: str,
    expected_dynamics: str,
) -> None:
    report = FitReport(
        summary=SessionSummary(
            start_time=None,
            activity_name=activity_type,
            activity_type=activity_type,
            total_distance_km=None,
            total_timer_time_s=None,
            total_elapsed_time_s=None,
            total_ascent_m=None,
            total_descent_m=None,
            avg_heart_rate_bpm=None,
            max_heart_rate_bpm=None,
            avg_cadence_spm=None,
            avg_speed_kmh=10.8,
            avg_temperature_c=None,
            min_temperature_c=None,
            max_temperature_c=None,
            sport=sport,
        ),
        transitions=(
            TransitionDynamics(
                label="Km 1",
                samples=(
                    TransitionSample(
                        elapsed_seconds=0.0,
                        heart_rate_bpm=150,
                        speed_kmh=10.8,
                        grade_percent=None,
                    ),
                ),
            ),
        ),
    )

    markdown = MarkdownReportRenderer().render(report)

    assert expected_summary in markdown
    assert expected_dynamics in markdown


def _workout_summary(
    sport: str = "running", activity_type: str = "Treadmill"
) -> SessionSummary:
    return SessionSummary(
        start_time=datetime(2026, 9, 23, 8),
        activity_name=activity_type,
        activity_type=activity_type,
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
        sport=sport,
    )


def _native_row(
    index: int,
    *,
    label: str | None = None,
    role: LapRole = LapRole.UNKNOWN,
    distance: float | None = 400,
    duration: float | None = 100,
    pace: float | None = 250,
    speed: float | None = 14.4,
    avg_hr: int | None = 160,
    max_hr: int | None = 170,
    cadence: int | None = 180,
    issues: tuple[MeasurementIssue, ...] = (),
    workout_issues: tuple[WorkoutIssue, ...] = (),
) -> NativeLapRow:
    return NativeLapRow(
        index=index,
        label=label,
        role=role,
        workout_step=None,
        workout_issues=workout_issues,
        start_time=None,
        end_time=None,
        distance_m=distance,
        active_duration_s=duration,
        pace_seconds_per_km=pace,
        speed_kmh=speed,
        avg_heart_rate_bpm=avg_hr,
        max_heart_rate_bpm=max_hr,
        avg_cadence_spm=cadence,
        hr_coverage=MeasurementCoverage(0, duration, MeasurementReason.NO_HR_COVERAGE),
        cadence_coverage=MeasurementCoverage(
            0, duration, MeasurementReason.NO_CADENCE_COVERAGE
        ),
        issues=issues,
    )


def _workout(
    *laps: NativeLapRow,
    repetitions: RepetitionAnalysis | None = None,
    recoveries: RecoveryAnalysis | None = None,
) -> WorkoutReport:
    return WorkoutReport(
        laps=laps,
        repetitions=repetitions
        or RepetitionAnalysis((), (), RepetitionReason.NO_WORK_LAPS),
        recoveries=recoveries or RecoveryAnalysis(()),
    )


def _zones(*, with_lap: bool = True) -> HeartRateZoneReport:
    session = ZoneMeasurement(
        zone_seconds=(1, 1, 1, 1, 2),
        zone_percentages=(100 / 6, 100 / 6, 100 / 6, 100 / 6, 200 / 6),
        coverage=MeasurementCoverage(6, 10),
        coverage_percent=60,
        unknown_seconds=4,
    )
    lap = LapZoneMeasurement(
        source_position=0,
        lap_index=2,
        measurement=ZoneMeasurement(
            zone_seconds=(0, 0, 2, 0, 0),
            zone_percentages=(0, 0, 100, 0, 0),
            coverage=MeasurementCoverage(2, 4),
            coverage_percent=50,
            unknown_seconds=2,
        ),
    )
    return HeartRateZoneReport(
        boundaries=HeartRateZoneBoundaries((130, 145, 160, 175)),
        session=session,
        laps=(lap,) if with_lap else (),
    )


def test_optional_sections_are_absent_without_extra_lines_in_default_report() -> None:
    report = FitReport(summary=_workout_summary())
    default = MarkdownReportRenderer().render(report)
    legacy = MarkdownReportRenderer(
        section_renderers=(
            SessionSummarySectionRenderer(),
            SplitSectionRenderer(),
            TransitionSectionRenderer(),
        )
    ).render(report)
    assert default == legacy
    assert "Workout Breakdown" not in default
    assert "Repetition Consistency" not in default
    assert "Recovery Heart-Rate Changes" not in default
    assert "Heart-Rate Zones" not in default
    assert default.count("\n\n## ") == 3


def test_all_enabled_sections_follow_existing_sections_in_specified_order() -> None:
    repeated = RepetitionGroup(
        step_identity="1",
        duration=WorkoutDuration(DurationKind.METERS, 400),
        target=WorkoutTarget(TargetKind.OPEN),
        secondary_target=None,
        source_positions=(0, 2),
        lap_indices=(2, 4),
        metric=RepetitionMetric.PACE_SECONDS_PER_KM,
        mean=250,
        fastest=245,
        slowest=255,
        coefficient_variation_percent=2,
    )
    report = FitReport(
        summary=_workout_summary(),
        workout=_workout(
            _native_row(2, label="400 | **effort**\n[go]", role=LapRole.WORK),
            _native_row(
                3,
                label="Recovery",
                role=LapRole.RECOVERY,
                distance=0,
                duration=60,
                pace=None,
                speed=None,
                avg_hr=None,
                max_hr=None,
                cadence=0,
            ),
            _native_row(4, role=LapRole.WORK),
            repetitions=RepetitionAnalysis((repeated,), ()),
            recoveries=RecoveryAnalysis(
                (
                    RecoveryChange(
                        source_position=1,
                        lap_index=3,
                        preceding_lap_index=2,
                        delta_bpm=-25,
                        start_hr_bpm=160,
                        hr_60_bpm=135,
                        start_observation_offset_s=2,
                        sixty_second_observation_offset_s=-1,
                    ),
                )
            ),
        ),
        zones=_zones(),
    )
    markdown = MarkdownReportRenderer().render(report)
    headings = [line for line in markdown.splitlines() if line.startswith("## ")]
    assert headings == [
        "## Session Summary",
        "## Kilometric Splits",
        "## Heart Rate Dynamics (Per Kilometer)",
        "## Workout Breakdown",
        "## Repetition Consistency",
        "## Recovery Heart-Rate Changes",
        "## Heart-Rate Zones",
    ]
    assert "400 \\| \\*\\*effort\\*\\* \\[go\\]" in markdown
    assert "| 3 | Recovery | recovery | 0.00 km | 1:00 | - | - | - | 0 |" in markdown
    assert (
        "| Step 1 | 400 m; open target | 2 | 4:10/km | 4:05/km | 4:15/km | 2.0% | 0 |"
        in markdown
    )
    assert "| 3 | 2 | 160 bpm | 135 bpm | -25 bpm | +2.0 s | -1.0 s | - |" in markdown
    assert (
        "Z1 <130 bpm; Z2 130–<145 bpm; Z3 145–<160 bpm; Z4 160–<175 bpm; Z5 ≥175 bpm"
        in markdown
    )
    assert "five wall-clock seconds" in markdown
    assert (
        "**Session HR coverage:** 0:06 covered / 0:10 active (60.0%); unknown active time 0:04."
        in markdown
    )
    assert "**Per-lap zones**" in markdown
    assert "0:02 (100.0%)" in markdown


def test_requested_workout_with_no_laps_has_explanations_and_no_zones_estimate() -> (
    None
):
    report = FitReport(summary=_workout_summary(), workout=_workout())
    markdown = MarkdownReportRenderer().render(report)
    assert "Workout breakdown unavailable: no native laps recorded." in markdown
    assert (
        "Comparable repetitions unavailable: no explicitly marked work laps."
        in markdown
    )
    assert "No explicitly marked recovery laps recorded." in markdown
    assert "HR zones are not configured; no zone boundaries are estimated." in markdown
    assert "## Heart-Rate Zones" not in markdown


def test_zones_can_render_without_workout_sections_or_lap_table() -> None:
    report = FitReport(summary=_workout_summary(), zones=_zones(with_lap=False))
    markdown = MarkdownReportRenderer().render(report)
    assert "## Heart-Rate Zones" in markdown
    assert "## Workout Breakdown" not in markdown
    assert "**Per-lap zones**" not in markdown
    assert "| Z5 | ≥175 bpm | 0:02 | 33.3% |" in markdown


def test_unavailable_zone_coverage_and_zero_zone_time_are_distinct() -> None:
    unknown = ZoneMeasurement(
        zone_seconds=None,
        zone_percentages=None,
        coverage=MeasurementCoverage(
            None, 600, MeasurementReason.ACTIVE_TIMING_UNAVAILABLE
        ),
        coverage_percent=None,
        unknown_seconds=None,
        issues=(MeasurementReason.ACTIVE_TIMING_UNAVAILABLE,),
    )
    zero = ZoneMeasurement(
        zone_seconds=(0, 0, 0, 0, 0),
        zone_percentages=(None, None, None, None, None),
        coverage=MeasurementCoverage(0, 600, MeasurementReason.NO_HR_COVERAGE),
        coverage_percent=0,
        unknown_seconds=600,
        issues=(MeasurementReason.NO_HR_COVERAGE,),
    )
    for session, expected in (
        (unknown, "| Z1 | <130 bpm | - | - |"),
        (zero, "| Z1 | <130 bpm | 0:00 | - |"),
    ):
        report = FitReport(
            summary=_workout_summary(),
            zones=HeartRateZoneReport(
                HeartRateZoneBoundaries((130, 145, 160, 175)), session
            ),
        )
        markdown = MarkdownReportRenderer().render(report)
        assert expected in markdown
        assert "10:00 active" in markdown
        assert "coverage percentage" in markdown or "0.0%" in markdown


def test_inconsistent_zone_total_shows_unrounded_measurement_and_explanation() -> None:
    session = ZoneMeasurement(
        zone_seconds=(0, 10, 0, 0, 0),
        zone_percentages=(0, 100, 0, 0, 0),
        coverage=MeasurementCoverage(10, 8.9, MeasurementReason.INCONSISTENT_TIMING),
        coverage_percent=None,
        unknown_seconds=None,
        issues=(MeasurementReason.INCONSISTENT_TIMING,),
    )
    report = FitReport(
        summary=_workout_summary(),
        zones=HeartRateZoneReport(
            HeartRateZoneBoundaries((130, 145, 160, 175)), session
        ),
    )
    markdown = MarkdownReportRenderer().render(report)
    assert "| Z2 | 130–<145 bpm | 0:10 | 100.0% |" in markdown
    assert "0:10 covered / 0:08.9 active (-); unknown active time -." in markdown
    assert "exceeds native active time by more than one second" in markdown


def test_workout_notes_and_recovery_unavailability_render_inside_markdown() -> None:
    excluded = RepetitionExclusion(0, 2, RepetitionReason.MISSING_STEP_ASSOCIATION)
    report = FitReport(
        summary=_workout_summary(),
        workout=_workout(
            _native_row(
                2,
                label="  Effort\n\t| *hard* ",
                role=LapRole.WORK,
                avg_hr=None,
                cadence=None,
                issues=(MeasurementIssue("lap", MeasurementReason.OVERLAPPING_LAPS),),
                workout_issues=(WorkoutIssue.ROLE_CONFLICT,),
            ),
            _native_row(3, role=LapRole.RECOVERY),
            repetitions=RepetitionAnalysis(
                (), (excluded,), RepetitionReason.INSUFFICIENT_REPETITIONS
            ),
            recoveries=RecoveryAnalysis(
                (
                    RecoveryChange(
                        1,
                        3,
                        2,
                        start_hr_bpm=150,
                        start_observation_offset_s=0,
                        reason=RecoveryReason.SIXTY_SECOND_HR_UNAVAILABLE,
                    ),
                )
            ),
        ),
    )
    markdown = MarkdownReportRenderer().render(report)
    assert "Effort \\| \\*hard\\*" in markdown
    assert "overlapping lap boundaries; record-derived measurements omitted" in markdown
    assert "lap and linked-step roles disagree; lap role shown" in markdown
    assert "Excluded work laps: 1." in markdown
    assert "Laps 2: no explicit workout-step association." in markdown
    assert (
        "| 3 | 2 | 150 bpm | - | - | +0.0 s | - | valid 60-second HR observation unavailable"
        in markdown
    )


def test_workout_pace_and_speed_follow_canonical_sport() -> None:
    row = _native_row(1, role=LapRole.WORK, pace=250, speed=14.4)
    running = MarkdownReportRenderer().render(
        FitReport(
            summary=_workout_summary("running", "Trail Running"), workout=_workout(row)
        )
    )
    cycling = MarkdownReportRenderer().render(
        FitReport(summary=_workout_summary("cycling", "Ride"), workout=_workout(row))
    )
    assert "| Active time | Pace |" in running
    assert "| 1 | Lap 1 | work | 0.40 km | 1:40 | 4:10/km |" in running
    assert "| Active time | Speed |" in cycling
    assert "| 1 | Lap 1 | work | 0.40 km | 1:40 | 14.40 km/h |" in cycling


def test_workout_metric_rounding_happens_only_in_presentation() -> None:
    row = _native_row(1, duration=100.6, pace=250.6, distance=400)
    report = FitReport(summary=_workout_summary(), workout=_workout(row))
    markdown = MarkdownReportRenderer().render(report)
    assert "| 0.40 km | 1:41 | 4:11/km |" in markdown
    assert row.active_duration_s == 100.6
    session = ZoneMeasurement(
        zone_seconds=(0.04, 1.26, 0, 0, 0),
        zone_percentages=(3.08, 96.92, 0, 0, 0),
        coverage=MeasurementCoverage(1.3, None),
        coverage_percent=None,
        unknown_seconds=None,
        issues=(MeasurementReason.TOTAL_ACTIVE_TIME_UNAVAILABLE,),
    )
    zone_markdown = MarkdownReportRenderer().render(
        FitReport(
            summary=_workout_summary(),
            zones=HeartRateZoneReport(
                HeartRateZoneBoundaries((130, 145, 160, 175)), session
            ),
        )
    )
    assert "<0.1 s" in zone_markdown
    assert "0:01.3" in zone_markdown
    assert "3.1%" in zone_markdown
    assert "total active time unavailable" in zone_markdown.lower()


class DisabledCustomSection:
    heading = "Disabled custom"

    def is_enabled(self, report: FitReport) -> bool:
        return False

    def render_lines(self, report: FitReport) -> list[str]:
        raise AssertionError("Disabled section must not render")


def test_custom_sections_remain_compatible_with_optional_eligibility() -> None:
    report = FitReport(summary=_workout_summary())
    markdown = MarkdownReportRenderer(
        section_renderers=(CustomSection(), DisabledCustomSection())
    ).render(report)
    assert "## Custom" in markdown
    assert "## Disabled custom" not in markdown
    assert markdown.count("\n\n## ") == 1


def test_nonrunning_repetition_uses_speed_and_zero_recovery_delta_is_visible() -> None:
    group = RepetitionGroup(
        step_identity="ride-1",
        duration=WorkoutDuration(DurationKind.SECONDS, 30),
        target=WorkoutTarget(TargetKind.SPEED_MPS, 3, 4),
        secondary_target=None,
        source_positions=(0, 1),
        lap_indices=(1, 2),
        metric=RepetitionMetric.SPEED_KMH,
        mean=16.2,
        fastest=18,
        slowest=14.4,
        coefficient_variation_percent=11.111,
    )
    report = FitReport(
        summary=_workout_summary("cycling", "Ride"),
        workout=_workout(
            _native_row(1, role=LapRole.WORK),
            _native_row(2, role=LapRole.WORK),
            repetitions=RepetitionAnalysis((group,), ()),
            recoveries=RecoveryAnalysis(
                (
                    RecoveryChange(
                        2,
                        3,
                        2,
                        delta_bpm=0,
                        start_hr_bpm=130,
                        hr_60_bpm=130,
                        start_observation_offset_s=0,
                        sixty_second_observation_offset_s=0,
                    ),
                )
            ),
        ),
    )
    markdown = MarkdownReportRenderer().render(report)
    assert "30 s; 3–4 m/s" in markdown
    assert "16.20 km/h | 18.00 km/h | 14.40 km/h | 11.1%" in markdown
    assert "+0 bpm | +0.0 s | +0.0 s" in markdown


def test_per_lap_zone_unavailability_is_separate_from_session_measurement() -> None:
    unavailable = ZoneMeasurement(
        zone_seconds=None,
        zone_percentages=None,
        coverage=MeasurementCoverage(None, 100, MeasurementReason.OVERLAPPING_LAPS),
        coverage_percent=None,
        unknown_seconds=None,
        issues=(MeasurementReason.OVERLAPPING_LAPS,),
    )
    zones = HeartRateZoneReport(
        HeartRateZoneBoundaries((130, 145, 160, 175)),
        _zones().session,
        (LapZoneMeasurement(0, 7, unavailable),),
    )
    markdown = MarkdownReportRenderer().render(
        FitReport(summary=_workout_summary(), zones=zones)
    )
    assert "| Z5 | ≥175 bpm | 0:02 | 33.3% |" in markdown
    assert (
        "| 7 | - | - | - | - | - | - / 1:40 (-); unknown - | Overlapping lap boundaries; per-lap zones unavailable |"
        in markdown
    )


def test_repeated_lap_limitations_are_grouped_for_readability() -> None:
    issue = MeasurementIssue("lap", MeasurementReason.OVERLAPPING_LAPS)
    report = FitReport(
        summary=_workout_summary(),
        workout=_workout(
            _native_row(1, issues=(issue,)),
            _native_row(2, issues=(issue,)),
        ),
    )
    markdown = MarkdownReportRenderer().render(report)
    assert (
        "Laps 1, 2: overlapping lap boundaries; record-derived measurements omitted."
        in markdown
    )
    assert (
        markdown.count(
            "overlapping lap boundaries; record-derived measurements omitted"
        )
        == 1
    )


class LegacySectionWithNoncallableEligibility:
    heading = "Legacy enabled"
    is_enabled = False

    def render_lines(self, report: FitReport) -> list[str]:
        return ["Legacy content"]


def test_noncallable_custom_eligibility_does_not_hide_legacy_section() -> None:
    markdown = MarkdownReportRenderer(
        section_renderers=(LegacySectionWithNoncallableEligibility(),)
    ).render(FitReport(summary=_workout_summary()))
    assert "## Legacy enabled" in markdown
    assert "Legacy content" in markdown
