import json
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from http.client import HTTPException
from statistics import mean
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

from fit_to_md.domain.reporting.entities import WeatherSummary
from fit_to_md.domain.reporting.ports import (
    ProviderDiagnostic,
    ProviderDiagnosticKind,
    ProviderLookupResult,
)

_PROVIDER_NAME = "Open-Meteo"


@dataclass(frozen=True)
class _HourlyWeatherSample:
    timestamp: datetime
    temperature_c: float | None
    apparent_temperature_c: float | None
    weather_code: int | None
    wind_speed_kmh: float | None
    wind_direction_deg: float | None


class OpenMeteoHistoricalWeatherProvider:
    def __init__(
        self,
        base_url: str = "https://archive-api.open-meteo.com/v1/archive",
        timeout_s: int = 10,
        urlopen_fn: Callable[..., Any] = urlopen,
    ) -> None:
        self._base_url = base_url
        self._timeout_s = timeout_s
        self._urlopen_fn = urlopen_fn

    def lookup(
        self,
        start_time: datetime,
        end_time: datetime | None,
        latitude_deg: float,
        longitude_deg: float,
    ) -> ProviderLookupResult[WeatherSummary | None]:
        normalized_start = _normalize_datetime(start_time)
        normalized_end = _normalize_datetime(end_time or start_time)
        if normalized_end < normalized_start:
            normalized_end = normalized_start

        url = self._build_url(
            start_time=normalized_start,
            end_time=normalized_end,
            latitude_deg=latitude_deg,
            longitude_deg=longitude_deg,
        )

        try:
            with self._urlopen_fn(url, timeout=self._timeout_s) as response:
                payload = json.load(response)
        except HTTPError as error:
            kind = (
                ProviderDiagnosticKind.QUOTA_EXCEEDED
                if error.code == 429
                else ProviderDiagnosticKind.PROVIDER_UNAVAILABLE
            )
            return _failure_result(
                kind,
                f"historical weather request failed with HTTP {error.code}: {error.reason}",
            )
        except (HTTPException, OSError, TimeoutError) as error:
            return _failure_result(
                ProviderDiagnosticKind.PROVIDER_UNAVAILABLE,
                f"historical weather request failed: {error}",
            )
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            return _failure_result(
                ProviderDiagnosticKind.INVALID_RESPONSE,
                f"historical weather response was not valid JSON: {error}",
            )

        if not _has_valid_payload_shape(payload):
            return _failure_result(
                ProviderDiagnosticKind.INVALID_RESPONSE,
                "historical weather response has an invalid structure",
            )

        samples, has_malformed_data = _parse_samples(payload)
        if not samples:
            return _failure_result(
                (
                    ProviderDiagnosticKind.INVALID_RESPONSE
                    if has_malformed_data
                    else ProviderDiagnosticKind.NO_COVERAGE
                ),
                (
                    "historical weather response contains malformed hourly data"
                    if has_malformed_data
                    else "historical weather data is unavailable for this activity"
                ),
            )

        window_start = normalized_start.replace(minute=0, second=0, microsecond=0)
        window_end = normalized_end.replace(minute=0, second=0, microsecond=0)
        window_samples = [
            sample
            for sample in samples
            if window_start <= sample.timestamp <= window_end
        ]
        if not window_samples:
            representative_sample = _nearest_sample(samples, normalized_start)
            window_samples = (
                [representative_sample] if representative_sample is not None else []
            )
        if not window_samples:
            return _failure_result(
                ProviderDiagnosticKind.NO_COVERAGE,
                "historical weather data is unavailable for this activity",
            )

        representative_sample = _nearest_sample(window_samples, normalized_start)
        if representative_sample is None:
            return _failure_result(
                ProviderDiagnosticKind.NO_COVERAGE,
                "historical weather data is unavailable for this activity",
            )

        wind_speed_kmh = _average_optional(
            sample.wind_speed_kmh for sample in window_samples
        )
        wind_direction_label = _degrees_to_compass(
            _average_wind_direction(
                sample.wind_direction_deg for sample in window_samples
            )
        )

        diagnostics = (
            (
                ProviderDiagnostic(
                    _PROVIDER_NAME,
                    ProviderDiagnosticKind.INVALID_RESPONSE,
                    "historical weather response contains malformed hourly data; valid values were preserved",
                ),
            )
            if has_malformed_data
            else ()
        )
        return ProviderLookupResult(
            value=WeatherSummary(
                source="historical",
                temperature_c=representative_sample.temperature_c,
                apparent_temperature_c=representative_sample.apparent_temperature_c,
                condition_summary=_weather_code_to_label(
                    representative_sample.weather_code
                ),
                wind_speed_kmh=wind_speed_kmh,
                wind_direction_label=wind_direction_label,
                temperature_min_c=_min_optional(
                    sample.temperature_c for sample in window_samples
                ),
                temperature_max_c=_max_optional(
                    sample.temperature_c for sample in window_samples
                ),
            ),
            diagnostics=diagnostics,
        )

    def _build_url(
        self,
        start_time: datetime,
        end_time: datetime,
        latitude_deg: float,
        longitude_deg: float,
    ) -> str:
        query = urlencode(
            {
                "latitude": f"{latitude_deg:.6f}",
                "longitude": f"{longitude_deg:.6f}",
                "start_date": start_time.date().isoformat(),
                "end_date": end_time.date().isoformat(),
                "hourly": ",".join(
                    (
                        "temperature_2m",
                        "apparent_temperature",
                        "weather_code",
                        "wind_speed_10m",
                        "wind_direction_10m",
                    )
                ),
                "timezone": "UTC",
                "temperature_unit": "celsius",
                "wind_speed_unit": "kmh",
            }
        )
        return f"{self._base_url}?{query}"


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _failure_result(
    kind: ProviderDiagnosticKind, message: str
) -> ProviderLookupResult[WeatherSummary | None]:
    return ProviderLookupResult(
        value=None,
        diagnostics=(ProviderDiagnostic(_PROVIDER_NAME, kind, message),),
    )


def _has_valid_payload_shape(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        return False
    return isinstance(hourly.get("time"), list)


def _parse_samples(payload: Any) -> tuple[list[_HourlyWeatherSample], bool]:
    if not isinstance(payload, dict):
        return [], True

    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        return [], True

    times = hourly.get("time")
    if not isinstance(times, list):
        return [], True

    if not times:
        return [], False

    raw_series = (
        hourly.get("temperature_2m"),
        hourly.get("apparent_temperature"),
        hourly.get("weather_code"),
        hourly.get("wind_speed_10m"),
        hourly.get("wind_direction_10m"),
    )
    normalized_series = tuple(_as_list(value, len(times)) for value in raw_series)
    temperatures, apparent_temperatures, weather_codes, wind_speeds, wind_directions = (
        series for series, _ in normalized_series
    )
    has_malformed_data = any(malformed for _, malformed in normalized_series)

    samples: list[_HourlyWeatherSample] = []
    for index, raw_time in enumerate(times):
        if not isinstance(raw_time, str):
            has_malformed_data = True
            continue
        try:
            timestamp = datetime.fromisoformat(raw_time).replace(tzinfo=UTC)
        except ValueError:
            has_malformed_data = True
            continue
        converted_values = (
            _to_float(temperatures[index]),
            _to_float(apparent_temperatures[index]),
            _to_int(weather_codes[index]),
            _to_float(wind_speeds[index]),
            _to_float(wind_directions[index]),
        )
        raw_values = (
            temperatures[index],
            apparent_temperatures[index],
            weather_codes[index],
            wind_speeds[index],
            wind_directions[index],
        )
        if any(
            raw_value is not None and converted_value is None
            for raw_value, converted_value in zip(
                raw_values, converted_values, strict=True
            )
        ):
            has_malformed_data = True
        sample = _HourlyWeatherSample(
            timestamp=timestamp,
            temperature_c=converted_values[0],
            apparent_temperature_c=converted_values[1],
            weather_code=converted_values[2],
            wind_speed_kmh=converted_values[3],
            wind_direction_deg=converted_values[4],
        )
        if _has_usable_weather(sample):
            samples.append(sample)
    return samples, has_malformed_data


def _has_usable_weather(sample: _HourlyWeatherSample) -> bool:
    return any(
        value is not None
        for value in (
            sample.temperature_c,
            sample.apparent_temperature_c,
            _weather_code_to_label(sample.weather_code),
            sample.wind_speed_kmh,
            sample.wind_direction_deg,
        )
    )


def _as_list(value: Any, length: int) -> tuple[list[Any], bool]:
    if not isinstance(value, list):
        return [None] * length, True
    malformed = len(value) != length
    if len(value) < length:
        return value + ([None] * (length - len(value))), malformed
    return value[:length], malformed


def _nearest_sample(
    samples: list[_HourlyWeatherSample], target: datetime
) -> _HourlyWeatherSample | None:
    if not samples:
        return None
    return min(
        samples, key=lambda sample: abs((sample.timestamp - target).total_seconds())
    )


def _average_optional(values: Iterable[int | float | None]) -> float | None:
    collected = [float(value) for value in values if value is not None]
    if not collected:
        return None
    return mean(collected)


def _average_wind_direction(values: Iterable[int | float | None]) -> float | None:
    collected = [float(value) for value in values if value is not None]
    if not collected:
        return None

    sin_total = sum(math.sin(math.radians(value)) for value in collected)
    cos_total = sum(math.cos(math.radians(value)) for value in collected)
    if sin_total == 0 and cos_total == 0:
        return None
    return (math.degrees(math.atan2(sin_total, cos_total)) + 360.0) % 360.0


def _degrees_to_compass(value: float | None) -> str | None:
    if value is None:
        return None
    directions = (
        "N",
        "NNE",
        "NE",
        "ENE",
        "E",
        "ESE",
        "SE",
        "SSE",
        "S",
        "SSW",
        "SW",
        "WSW",
        "W",
        "WNW",
        "NW",
        "NNW",
    )
    index = round(value / 22.5) % len(directions)
    return directions[index]


def _weather_code_to_label(value: int | None) -> str | None:
    if value is None:
        return None
    labels = {
        0: "Clear sky",
        1: "Mainly clear",
        2: "Partly cloudy",
        3: "Overcast",
        45: "Fog",
        48: "Depositing rime fog",
        51: "Light drizzle",
        53: "Moderate drizzle",
        55: "Dense drizzle",
        56: "Light freezing drizzle",
        57: "Dense freezing drizzle",
        61: "Slight rain",
        63: "Moderate rain",
        65: "Heavy rain",
        66: "Light freezing rain",
        67: "Heavy freezing rain",
        71: "Slight snow fall",
        73: "Moderate snow fall",
        75: "Heavy snow fall",
        77: "Snow grains",
        80: "Slight rain showers",
        81: "Moderate rain showers",
        82: "Violent rain showers",
        85: "Slight snow showers",
        86: "Heavy snow showers",
        95: "Thunderstorm",
        96: "Thunderstorm with slight hail",
        99: "Thunderstorm with heavy hail",
    }
    return labels.get(value)


def _min_optional(values: Iterable[int | float | None]) -> float | None:
    collected = [float(value) for value in values if value is not None]
    if not collected:
        return None
    return min(collected)


def _max_optional(values: Iterable[int | float | None]) -> float | None:
    collected = [float(value) for value in values if value is not None]
    if not collected:
        return None
    return max(collected)


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            converted = float(value)
        except OverflowError:
            return None
        return converted if math.isfinite(converted) else None
    return None


def _to_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return round(value)
    return None
