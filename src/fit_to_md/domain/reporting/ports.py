from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from fit_to_md.domain.reporting.entities import FitReport, WeatherSummary


@dataclass(frozen=True)
class ElevationCoordinate:
    latitude_deg: float
    longitude_deg: float


@dataclass(frozen=True)
class ElevationRunStatistics:
    provider_name: str
    request_count: int
    request_limit: int | None


class ElevationDiagnostics(Protocol):
    def set_progress_callback(
        self, callback: Callable[[int, int], None] | None
    ) -> None: ...

    def run_statistics(self) -> ElevationRunStatistics: ...


class ActivityExtractor(Protocol):
    def extract(self, source: Path) -> FitReport: ...


class ReportRenderer(Protocol):
    def render(self, report: FitReport) -> str: ...


class ElevationProvider(Protocol):
    def lookup(
        self, coordinates: Sequence[ElevationCoordinate]
    ) -> tuple[float | None, ...]: ...


class HistoricalWeatherProvider(Protocol):
    def lookup(
        self,
        start_time: datetime,
        end_time: datetime | None,
        latitude_deg: float,
        longitude_deg: float,
    ) -> WeatherSummary | None: ...
