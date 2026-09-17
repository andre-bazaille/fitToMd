from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol

from fit_to_md.domain.reporting.entities import FitReport, WeatherSummary


class ProviderDiagnosticKind(Enum):
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    INVALID_RESPONSE = "invalid_response"
    QUOTA_EXCEEDED = "quota_exceeded"
    PARTIAL_COVERAGE = "partial_coverage"
    NO_COVERAGE = "no_coverage"


@dataclass(frozen=True)
class ProviderDiagnostic:
    provider_name: str
    kind: ProviderDiagnosticKind
    message: str


@dataclass(frozen=True)
class ProviderLookupResult[T]:
    value: T
    diagnostics: tuple[ProviderDiagnostic, ...] = ()


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


class ReportRenderer(Protocol):
    def render(self, report: FitReport) -> str: ...


class ElevationProvider(Protocol):
    def lookup(
        self, coordinates: Sequence[ElevationCoordinate]
    ) -> ProviderLookupResult[tuple[float | None, ...]]: ...


class HistoricalWeatherProvider(Protocol):
    def lookup(
        self,
        start_time: datetime,
        end_time: datetime | None,
        latitude_deg: float,
        longitude_deg: float,
    ) -> ProviderLookupResult[WeatherSummary | None]: ...
