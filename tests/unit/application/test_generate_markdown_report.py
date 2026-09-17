from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from fit_to_md.application.use_cases.generate_markdown_report import (
    GenerateMarkdownReport,
)
from fit_to_md.domain.activity import Activity, ActivityRecord, ActivitySession
from fit_to_md.domain.reporting.entities import FitReport, WeatherSummary
from fit_to_md.domain.reporting.ports import ElevationCoordinate


class StubReader:
    def __init__(self, activity: Activity) -> None:
        self.activity = activity
        self.calls: list[Path] = []

    def read(self, source: Path) -> Activity:
        self.calls.append(source)
        return self.activity


class FailingReader:
    def read(self, source: Path) -> Activity:
        raise NotImplementedError("unsupported activity")


class StubRenderer:
    def __init__(self) -> None:
        self.calls: list[FitReport] = []

    def render(self, report: FitReport) -> str:
        self.calls.append(report)
        return "rendered markdown"


class StubWeatherProvider:
    def __init__(self, weather: WeatherSummary | None) -> None:
        self.weather = weather
        self.calls: list[tuple[datetime, datetime | None, float, float]] = []

    def lookup(
        self,
        start_time: datetime,
        end_time: datetime | None,
        latitude_deg: float,
        longitude_deg: float,
    ) -> WeatherSummary | None:
        self.calls.append((start_time, end_time, latitude_deg, longitude_deg))
        return self.weather


class StubElevationProvider:
    def __init__(self, elevations: tuple[float | None, ...]) -> None:
        self.elevations = elevations
        self.calls: list[tuple[ElevationCoordinate, ...]] = []

    def lookup(
        self, coordinates: Sequence[ElevationCoordinate]
    ) -> tuple[float | None, ...]:
        self.calls.append(tuple(coordinates))
        return self.elevations


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

    with pytest.raises(NotImplementedError, match="unsupported activity"):
        use_case.execute(Path("activity.fit"))
    assert provider.calls == []
    assert renderer.calls == []


@pytest.mark.parametrize("invalid_value", (float("nan"), float("inf"), 0.0))
def test_rejects_invalid_elevation_sample_distance(invalid_value: float) -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        GenerateMarkdownReport(
            reader=StubReader(_activity()),
            renderer=StubRenderer(),
            elevation_sample_distance_m=invalid_value,
        )
