from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from fit_to_md.application.use_cases.generate_markdown_report import (
    GenerateMarkdownReport,
)
from fit_to_md.domain.activity import Activity, ActivityRecord, ActivitySession
from fit_to_md.domain.activity.ports import UnsupportedActivityError
from fit_to_md.domain.reporting.entities import FitReport, WeatherSummary
from fit_to_md.domain.reporting.ports import (
    ElevationCoordinate,
    ProviderDiagnostic,
    ProviderDiagnosticKind,
    ProviderLookupResult,
)


class StubReader:
    def __init__(self, activity: Activity) -> None:
        self.activity = activity
        self.calls: list[Path] = []

    def read(self, source: Path) -> Activity:
        self.calls.append(source)
        return self.activity


class FailingReader:
    def read(self, source: Path) -> Activity:
        raise UnsupportedActivityError("unsupported activity")


class StubRenderer:
    def __init__(self) -> None:
        self.calls: list[FitReport] = []

    def render(self, report: FitReport) -> str:
        self.calls.append(report)
        return "rendered markdown"


class StubWeatherProvider:
    def __init__(
        self,
        weather: WeatherSummary | None,
        diagnostics: tuple[ProviderDiagnostic, ...] = (),
    ) -> None:
        self.weather = weather
        self.diagnostics = diagnostics
        self.calls: list[tuple[datetime, datetime | None, float, float]] = []

    def lookup(
        self,
        start_time: datetime,
        end_time: datetime | None,
        latitude_deg: float,
        longitude_deg: float,
    ) -> ProviderLookupResult[WeatherSummary | None]:
        self.calls.append((start_time, end_time, latitude_deg, longitude_deg))
        return ProviderLookupResult(self.weather, self.diagnostics)


class StubElevationProvider:
    def __init__(
        self,
        elevations: tuple[float | None, ...],
        diagnostics: tuple[ProviderDiagnostic, ...] = (),
    ) -> None:
        self.elevations = elevations
        self.diagnostics = diagnostics
        self.calls: list[tuple[ElevationCoordinate, ...]] = []

    def lookup(
        self, coordinates: Sequence[ElevationCoordinate]
    ) -> ProviderLookupResult[tuple[float | None, ...]]:
        self.calls.append(tuple(coordinates))
        return ProviderLookupResult(self.elevations, self.diagnostics)


def _activity(*, native_temperature: float | None = None) -> Activity:
    start = datetime(2026, 9, 17, 8, 0)
    return Activity(
        session=ActivitySession(
            start_time=start,
            end_time=start + timedelta(seconds=600),
            start_latitude_deg=48.85,
            start_longitude_deg=2.35,
            total_distance_m=1000.0,
            total_timer_time_s=600.0,
            avg_temperature_c=native_temperature,
        ),
        sport="running",
        records=(
            ActivityRecord(
                timestamp=start,
                elapsed_time_s=0.0,
                distance_m=0.0,
                latitude_deg=48.85,
                longitude_deg=2.35,
                heart_rate_bpm=120,
                cadence_spm=160,
                fractional_cadence=None,
                speed_mps=2.5,
                altitude_m=100.0,
                grade_percent=1.0,
                temperature_c=None,
            ),
            ActivityRecord(
                timestamp=start + timedelta(seconds=600),
                elapsed_time_s=600.0,
                distance_m=1000.0,
                latitude_deg=48.86,
                longitude_deg=2.36,
                heart_rate_bpm=140,
                cadence_spm=170,
                fractional_cadence=None,
                speed_mps=3.0,
                altitude_m=110.0,
                grade_percent=1.0,
                temperature_c=None,
            ),
        ),
    )


def test_generate_markdown_report_reads_assembles_and_renders() -> None:
    reader = StubReader(_activity())
    renderer = StubRenderer()
    use_case = GenerateMarkdownReport(reader=reader, renderer=renderer)

    report, markdown = use_case.execute_with_report(Path("activity.fit"))

    assert markdown == "rendered markdown"
    assert reader.calls == [Path("activity.fit")]
    assert renderer.calls == [report]
    assert report.summary.activity_type == "Running"
    assert report.summary.total_distance_km == 1.0


def test_inspect_reads_start_time_without_enrichment_or_rendering() -> None:
    reader = StubReader(_activity())
    renderer = StubRenderer()
    weather_provider = StubWeatherProvider(None)
    elevation_provider = StubElevationProvider((200.0, 220.0))
    use_case = GenerateMarkdownReport(
        reader=reader,
        renderer=renderer,
        weather_provider=weather_provider,
        elevation_provider=elevation_provider,
        elevation_mode="dem",
    )

    metadata = use_case.inspect(Path("activity.fit"))

    assert metadata.start_time == datetime(2026, 9, 17, 8, 0)
    assert reader.calls == [Path("activity.fit")]
    assert weather_provider.calls == []
    assert elevation_provider.calls == []
    assert renderer.calls == []


def test_historical_weather_enriches_missing_native_temperature() -> None:
    weather = WeatherSummary(
        source="Historical weather",
        temperature_c=12.0,
        apparent_temperature_c=11.0,
        condition_summary="Mainly clear",
        wind_speed_kmh=8.0,
        wind_direction_label="S",
    )
    provider = StubWeatherProvider(weather)
    use_case = GenerateMarkdownReport(
        reader=StubReader(_activity()),
        renderer=StubRenderer(),
        weather_provider=provider,
    )

    report, _ = use_case.execute_with_report(Path("activity.fit"))

    assert report.summary.weather is weather
    assert provider.calls == [
        (
            datetime(2026, 9, 17, 8, 0),
            datetime(2026, 9, 17, 8, 10),
            48.85,
            2.35,
        )
    ]


def test_native_temperature_prevents_historical_weather_lookup() -> None:
    provider = StubWeatherProvider(None)
    use_case = GenerateMarkdownReport(
        reader=StubReader(_activity(native_temperature=18.0)),
        renderer=StubRenderer(),
        weather_provider=provider,
    )

    report, _ = use_case.execute_with_report(Path("activity.fit"))

    assert report.summary.avg_temperature_c == 18.0
    assert provider.calls == []


def test_missing_provider_data_preserves_offline_summary() -> None:
    provider = StubWeatherProvider(None)
    use_case = GenerateMarkdownReport(
        reader=StubReader(_activity()),
        renderer=StubRenderer(),
        weather_provider=provider,
    )

    report, _ = use_case.execute_with_report(Path("activity.fit"))

    assert report.summary.weather is None
    assert len(provider.calls) == 1


def test_dem_enrichment_is_applied_before_report_calculation() -> None:
    provider = StubElevationProvider((200.0, 220.0))
    use_case = GenerateMarkdownReport(
        reader=StubReader(_activity()),
        renderer=StubRenderer(),
        elevation_provider=provider,
        elevation_mode="dem",
        elevation_sample_distance_m=1000.0,
    )

    report, _ = use_case.execute_with_report(Path("activity.fit"))

    assert len(provider.calls) == 1
    assert report.splits[0].elevation_delta_m == 20.0


def test_partial_dem_coverage_keeps_uncovered_fit_values() -> None:
    provider = StubElevationProvider((200.0, None))
    use_case = GenerateMarkdownReport(
        reader=StubReader(_activity()),
        renderer=StubRenderer(),
        elevation_provider=provider,
        elevation_mode="dem",
        elevation_sample_distance_m=1000.0,
    )

    report, _ = use_case.execute_with_report(Path("activity.fit"))

    assert report.splits[0].elevation_delta_m == 10.0


def test_provider_exception_propagates_without_rendering() -> None:
    class FailingElevationProvider:
        def lookup(self, coordinates):
            raise RuntimeError("quota exceeded")

    renderer = StubRenderer()
    use_case = GenerateMarkdownReport(
        reader=StubReader(_activity()),
        renderer=renderer,
        elevation_provider=FailingElevationProvider(),
        elevation_mode="dem",
    )

    with pytest.raises(RuntimeError, match="quota exceeded"):
        use_case.execute(Path("activity.fit"))
    assert renderer.calls == []


def test_unsupported_input_stops_before_enrichment_and_rendering() -> None:
    renderer = StubRenderer()
    provider = StubElevationProvider((100.0, 110.0))
    use_case = GenerateMarkdownReport(
        reader=FailingReader(),
        renderer=renderer,
        elevation_provider=provider,
        elevation_mode="dem",
    )

    with pytest.raises(UnsupportedActivityError, match="unsupported activity"):
        use_case.execute(Path("activity.fit"))
    assert provider.calls == []
    assert renderer.calls == []


def test_detailed_result_accumulates_provider_diagnostics() -> None:
    elevation_diagnostic = ProviderDiagnostic(
        "Terrain",
        ProviderDiagnosticKind.PARTIAL_COVERAGE,
        "partial terrain coverage",
    )
    weather_diagnostic = ProviderDiagnostic(
        "Weather",
        ProviderDiagnosticKind.PROVIDER_UNAVAILABLE,
        "weather service unavailable",
    )
    use_case = GenerateMarkdownReport(
        reader=StubReader(_activity()),
        renderer=StubRenderer(),
        elevation_provider=StubElevationProvider(
            (200.0, None), (elevation_diagnostic,)
        ),
        elevation_mode="dem",
        elevation_sample_distance_m=1000.0,
        weather_provider=StubWeatherProvider(None, (weather_diagnostic,)),
    )

    result = use_case.execute_detailed(Path("activity.fit"))

    assert result.markdown == "rendered markdown"
    assert result.diagnostics == (elevation_diagnostic, weather_diagnostic)


@pytest.mark.parametrize("invalid_value", (float("nan"), float("inf"), 0.0))
def test_rejects_invalid_elevation_sample_distance(invalid_value: float) -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        GenerateMarkdownReport(
            reader=StubReader(_activity()),
            renderer=StubRenderer(),
            elevation_sample_distance_m=invalid_value,
        )


def test_elevation_replacement_preserves_workout_metadata_and_timeline() -> None:
    from dataclasses import replace

    from fit_to_md.domain.reporting.entities import SessionSummary
    from fit_to_md.domain.reporting.services import SessionSummaryBuilder
    from fit_to_md.infrastructure.fitdecode.reader import FitdecodeActivityReader
    from tests.support.workout import workout_scenario

    scenario = workout_scenario("paused")
    original = FitdecodeActivityReader(scenario.reader_factory).read(
        Path("synthetic.fit")
    )
    activity = replace(
        original,
        records=tuple(
            replace(record, latitude_deg=48.0, longitude_deg=2.0)
            for record in original.records
        ),
    )

    class CapturingSummaryBuilder(SessionSummaryBuilder):
        def __init__(self) -> None:
            super().__init__()
            self.activities: list[Activity] = []

        def build(self, activity: Activity) -> SessionSummary:
            self.activities.append(activity)
            return super().build(activity)

    builder = CapturingSummaryBuilder()
    provider = StubElevationProvider((200.0, 220.0))
    generator = GenerateMarkdownReport(
        reader=StubReader(activity),
        renderer=StubRenderer(),
        summary_builder=builder,
        elevation_provider=provider,
        elevation_mode="dem",
        elevation_sample_distance_m=5600,
    )
    generator.execute(Path("synthetic.fit"))
    enriched = builder.activities[0]
    assert len(provider.calls) == 1
    assert enriched.records != activity.records
    assert enriched.records[0].altitude_m == 200.0
    assert enriched.laps == activity.laps
    assert enriched.active_timeline == activity.active_timeline
    assert enriched.has_active_record_timing == activity.has_active_record_timing
    assert enriched.laps[1].workout_step is not None
    assert len(enriched.active_timeline.intervals) == 2


@pytest.mark.parametrize("lap_mode", (False, True))
@pytest.mark.parametrize("with_zones", (False, True))
def test_optional_workout_results_follow_typed_options(
    lap_mode: bool, with_zones: bool
) -> None:
    from fit_to_md.application.use_cases.generate_markdown_report import (
        ReportGenerationOptions,
        WorkoutReportMode,
    )
    from fit_to_md.domain.reporting.heart_rate_zones import HeartRateZoneBoundaries

    boundaries = HeartRateZoneBoundaries((130, 145, 160, 175)) if with_zones else None
    reader = StubReader(_activity())
    renderer = StubRenderer()
    generator = GenerateMarkdownReport(
        reader,
        renderer,
        options=ReportGenerationOptions(
            WorkoutReportMode.LAPS if lap_mode else WorkoutReportMode.OFF,
            boundaries,
        ),
    )

    report, _ = generator.execute_with_report(Path("activity.fit"))

    assert (report.workout is not None) is lap_mode
    assert (report.zones is not None) is with_zones
    assert report.summary.total_distance_km == 1.0
    assert len(report.splits) == 1
    assert renderer.calls == [report]
    assert reader.calls == [Path("activity.fit")]
    if report.workout is not None:
        assert report.workout.laps == ()
    if report.zones is not None:
        assert len(report.zones.laps) == 0
        assert report.zones.boundaries == boundaries


def test_inspect_does_not_build_requested_workout_results() -> None:
    from fit_to_md.application.use_cases.generate_markdown_report import (
        ReportGenerationOptions,
        WorkoutReportMode,
    )
    from fit_to_md.domain.reporting.heart_rate_zones import HeartRateZoneBoundaries

    class FailingBuilder:
        def build(self, *args, **kwargs):
            raise AssertionError("workout calculation during inspect")

    reader = StubReader(_activity())
    generator = GenerateMarkdownReport(
        reader,
        StubRenderer(),
        options=ReportGenerationOptions(
            WorkoutReportMode.LAPS, HeartRateZoneBoundaries((130, 145, 160, 175))
        ),
        native_lap_builder=FailingBuilder(),
        repetition_builder=FailingBuilder(),
        recovery_builder=FailingBuilder(),
        zone_builder=FailingBuilder(),
    )

    assert generator.inspect(Path("activity.fit")).start_time is not None
    assert reader.calls == [Path("activity.fit")]


def test_invalid_report_options_fail_before_activity_or_provider_calls() -> None:
    from fit_to_md.application.use_cases.generate_markdown_report import (
        ReportGenerationOptions,
    )

    reader = StubReader(_activity())
    provider = StubElevationProvider((200.0, 220.0))
    with pytest.raises(ValueError, match="workout_report"):
        ReportGenerationOptions(workout_report="unknown")
    with pytest.raises(TypeError, match="hr_zone_boundaries"):
        ReportGenerationOptions(hr_zone_boundaries=(130, 145, 160, 175))
    with pytest.raises(TypeError, match="options"):
        GenerateMarkdownReport(
            reader,
            StubRenderer(),
            elevation_provider=provider,
            elevation_mode="dem",
            options="invalid",
        )
    assert reader.calls == []
    assert provider.calls == []


def test_missing_optional_workout_data_renders_successfully() -> None:
    from fit_to_md.application.use_cases.generate_markdown_report import (
        ReportGenerationOptions,
        WorkoutReportMode,
    )
    from fit_to_md.domain.reporting.heart_rate_zones import HeartRateZoneBoundaries
    from fit_to_md.infrastructure.markdown.renderer import MarkdownReportRenderer

    generator = GenerateMarkdownReport(
        StubReader(Activity()),
        MarkdownReportRenderer(),
        options=ReportGenerationOptions(
            WorkoutReportMode.LAPS, HeartRateZoneBoundaries((130, 145, 160, 175))
        ),
    )

    result = generator.execute_detailed(Path("empty.fit"))

    assert result.report.workout is not None
    assert result.report.zones is not None
    assert "## Workout Breakdown" in result.markdown
    assert "## Heart-Rate Zones" in result.markdown


def test_enrichment_failure_with_workout_options_stops_before_report_assembly() -> None:
    from fit_to_md.application.use_cases.generate_markdown_report import (
        ReportGenerationOptions,
        WorkoutReportMode,
    )
    from fit_to_md.domain.reporting.heart_rate_zones import HeartRateZoneBoundaries

    class FailingElevationProvider:
        def __init__(self) -> None:
            self.calls = 0

        def lookup(self, coordinates):
            self.calls += 1
            raise RuntimeError("provider failed")

    provider = FailingElevationProvider()
    reader = StubReader(_activity())
    renderer = StubRenderer()
    generator = GenerateMarkdownReport(
        reader,
        renderer,
        elevation_provider=provider,
        elevation_mode="dem",
        options=ReportGenerationOptions(
            WorkoutReportMode.LAPS, HeartRateZoneBoundaries((130, 145, 160, 175))
        ),
    )

    with pytest.raises(RuntimeError, match="provider failed"):
        generator.execute(Path("activity.fit"))
    assert reader.calls == [Path("activity.fit")]
    assert provider.calls == 1
    assert renderer.calls == []


@pytest.mark.parametrize("lap_mode", (False, True))
def test_lap_zone_rows_follow_workout_mode(lap_mode: bool) -> None:
    from dataclasses import replace

    from fit_to_md.application.use_cases.generate_markdown_report import (
        ReportGenerationOptions,
        WorkoutReportMode,
    )
    from fit_to_md.domain.activity import ActivityLap
    from fit_to_md.domain.activity.timeline import (
        ActiveInterval,
        ActiveTimeline,
        TimelineSource,
    )
    from fit_to_md.domain.reporting.heart_rate_zones import HeartRateZoneBoundaries

    activity = _activity()
    start = activity.session.start_time
    end = activity.session.end_time
    assert start is not None and end is not None
    lap = ActivityLap(
        index=0,
        start_time=start,
        end_time=end,
        total_distance_m=1000.0,
        total_timer_time_s=600.0,
        total_ascent_m=None,
        total_descent_m=None,
        avg_heart_rate_bpm=None,
        max_heart_rate_bpm=None,
        avg_cadence_spm=None,
        avg_temperature_c=None,
        min_temperature_c=None,
        max_temperature_c=None,
    )
    activity = replace(
        activity,
        laps=(lap,),
        active_timeline=ActiveTimeline(
            (ActiveInterval(start, end),), TimelineSource.SESSION_TOTALS, None
        ),
    )
    generator = GenerateMarkdownReport(
        StubReader(activity),
        StubRenderer(),
        options=ReportGenerationOptions(
            WorkoutReportMode.LAPS if lap_mode else WorkoutReportMode.OFF,
            HeartRateZoneBoundaries((130, 145, 160, 175)),
        ),
    )

    report, _ = generator.execute_with_report(Path("activity.fit"))

    assert report.zones is not None
    assert len(report.zones.laps) == (1 if lap_mode else 0)
    assert (report.workout is not None) is lap_mode
