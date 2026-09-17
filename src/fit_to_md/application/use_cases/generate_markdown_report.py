from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from math import isfinite
from pathlib import Path

from fit_to_md.domain.activity.entities import Activity
from fit_to_md.domain.activity.ports import ActivityReader
from fit_to_md.domain.reporting.elevation import ElevationEnricher
from fit_to_md.domain.reporting.entities import FitReport, SessionSummary
from fit_to_md.domain.reporting.ports import (
    ElevationProvider,
    HistoricalWeatherProvider,
    ProviderDiagnostic,
    ReportRenderer,
)
from fit_to_md.domain.reporting.services import (
    SessionSummaryBuilder,
    SplitBuilder,
    TransitionBuilder,
    resolve_activity_start_time,
)


@dataclass(frozen=True)
class GeneratedMarkdownReport:
    report: FitReport
    markdown: str
    diagnostics: tuple[ProviderDiagnostic, ...] = ()


@dataclass(frozen=True)
class ReportGenerationMetadata:
    start_time: datetime | None


class GenerateMarkdownReport:
    def __init__(
        self,
        reader: ActivityReader,
        renderer: ReportRenderer,
        summary_builder: SessionSummaryBuilder | None = None,
        split_builder: SplitBuilder | None = None,
        transition_builder: TransitionBuilder | None = None,
        weather_provider: HistoricalWeatherProvider | None = None,
        elevation_provider: ElevationProvider | None = None,
        elevation_mode: str = "fit",
        elevation_sample_distance_m: float = 30.0,
    ) -> None:
        if elevation_mode not in {"fit", "dem", "hybrid"}:
            raise ValueError("elevation_mode must be one of: fit, dem, hybrid")
        if (
            not isfinite(elevation_sample_distance_m)
            or elevation_sample_distance_m <= 0
        ):
            raise ValueError("elevation_sample_distance_m must be finite and positive")

        self._reader = reader
        self._renderer = renderer
        self._summary_builder = summary_builder or SessionSummaryBuilder()
        self._split_builder = split_builder or SplitBuilder()
        self._transition_builder = transition_builder or TransitionBuilder()
        self._weather_provider = weather_provider
        self._elevation_provider = elevation_provider
        self._elevation_mode = elevation_mode
        self._elevation_sample_distance_m = elevation_sample_distance_m
        self._elevation_enricher = ElevationEnricher(elevation_sample_distance_m)

    def execute(self, source: Path) -> str:
        return self.execute_detailed(source).markdown

    def inspect(self, source: Path) -> ReportGenerationMetadata:
        """Read an activity and return only the metadata needed for planning."""
        activity = self._reader.read(source)
        return ReportGenerationMetadata(
            start_time=resolve_activity_start_time(activity),
        )

    def execute_with_report(self, source: Path) -> tuple[FitReport, str]:
        result = self.execute_detailed(source)
        return result.report, result.markdown

    def execute_detailed(self, source: Path) -> GeneratedMarkdownReport:
        parsed_activity = self._reader.read(source)
        activity, elevation_diagnostics = self._enrich_activity_elevation(
            parsed_activity
        )
        elevation_was_enriched = activity is not parsed_activity
        summary = self._summary_builder.build(activity)
        summary, weather_diagnostics = self._enrich_summary_weather(summary, activity)
        report = FitReport(
            summary=summary,
            splits=self._split_builder.build(
                activity,
                prefer_records=elevation_was_enriched,
            ),
            transitions=self._transition_builder.build(activity),
        )
        return GeneratedMarkdownReport(
            report=report,
            markdown=self._renderer.render(report),
            diagnostics=(*elevation_diagnostics, *weather_diagnostics),
        )

    def _enrich_activity_elevation(
        self, activity: Activity
    ) -> tuple[Activity, tuple[ProviderDiagnostic, ...]]:
        if self._elevation_mode == "fit" or self._elevation_provider is None:
            return activity, ()

        coordinates = self._elevation_enricher.sample_coordinates(activity.records)
        if not coordinates:
            return activity, ()
        lookup_result = self._elevation_provider.lookup(coordinates)
        enriched_records = self._elevation_enricher.enrich_records(
            activity.records,
            lookup_result.value,
            self._elevation_mode,
        )
        if enriched_records == activity.records:
            return activity, lookup_result.diagnostics
        return (
            replace(activity, records=enriched_records),
            lookup_result.diagnostics,
        )

    def _enrich_summary_weather(
        self, summary: SessionSummary, activity: Activity
    ) -> tuple[SessionSummary, tuple[ProviderDiagnostic, ...]]:
        if (
            self._weather_provider is None
            or summary.weather is not None
            or summary.has_fit_temperature
        ):
            return summary, ()

        start_time = summary.start_time
        if start_time is None:
            return summary, ()

        latitude_deg = activity.session.start_latitude_deg
        longitude_deg = activity.session.start_longitude_deg
        if latitude_deg is None or longitude_deg is None:
            return summary, ()

        end_time = activity.session.end_time
        if end_time is None and summary.total_elapsed_time_s is not None:
            end_time = start_time + timedelta(seconds=summary.total_elapsed_time_s)

        lookup_result = self._weather_provider.lookup(
            start_time=start_time,
            end_time=end_time,
            latitude_deg=latitude_deg,
            longitude_deg=longitude_deg,
        )
        if lookup_result.value is None:
            return summary, lookup_result.diagnostics
        return (
            replace(summary, weather=lookup_result.value),
            lookup_result.diagnostics,
        )
