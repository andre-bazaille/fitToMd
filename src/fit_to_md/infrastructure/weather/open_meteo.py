from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import urlopen

from fit_to_md.domain.reporting.entities import WeatherSummary


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
    ) -> WeatherSummary | None:
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
        except Exception:
            return None

        samples = _parse_samples(payload)
        if not samples:
            return None

        window_start = normalized_start.replace(minute=0, second=0, microsecond=0)
        window_end = normalized_end.replace(minute=0, second=0, microsecond=0)
        window_samples = [
            sample for sample in samples if window_start <= sample.timestamp <= window_end
        ]
        if not window_samples:
            representative_sample = _nearest_sample(samples, normalized_start)
            window_samples = [representative_sample] if representative_sample is not None else []
        if not window_samples:
            return None

        representative_sample = _nearest_sample(window_samples, normalized_start)
        if representative_sample is None:
            return None

        wind_speed_kmh = _average_optional(sample.wind_speed_kmh for sample in window_samples)
        wind_direction_label = _degrees_to_compass(
            _average_wind_direction(sample.wind_direction_deg for sample in window_samples)
        )

        return WeatherSummary(
            source="historical",
            temperature_c=representative_sample.temperature_c,
            apparent_temperature_c=representative_sample.apparent_temperature_c,
            condition_summary=_weather_code_to_label(representative_sample.weather_code),
            wind_speed_kmh=wind_speed_kmh,
            wind_direction_label=wind_direction_label,
            temperature_min_c=_min_optional(sample.temperature_c for sample in window_samples),
            temperature_max_c=_max_optional(sample.temperature_c for sample in window_samples),
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
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_samples(payload: dict[str, Any]) -> list[_HourlyWeatherSample]:
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        return []

    times = hourly.get("time")
    if not isinstance(times, list):
        return []

    temperatures = _as_list(hourly.get("temperature_2m"), len(times))
    apparent_temperatures = _as_list(hourly.get("apparent_temperature"), len(times))
    weather_codes = _as_list(hourly.get("weather_code"), len(times))
    wind_speeds = _as_list(hourly.get("wind_speed_10m"), len(times))
    wind_directions = _as_list(hourly.get("wind_direction_10m"), len(times))

    samples: list[_HourlyWeatherSample] = []
    for index, raw_time in enumerate(times):
        if not isinstance(raw_time, str):
            continue
        try:
            timestamp = datetime.fromisoformat(raw_time).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        samples.append(
            _HourlyWeatherSample(
                timestamp=timestamp,
                temperature_c=_to_float(temperatures[index]),
                apparent_temperature_c=_to_float(apparent_temperatures[index]),
                weather_code=_to_int(weather_codes[index]),
                wind_speed_kmh=_to_float(wind_speeds[index]),
                wind_direction_deg=_to_float(wind_directions[index]),
            )
        )
    return samples


def _as_list(value: Any, length: int) -> list[Any]:
    if not isinstance(value, list):
        return [None] * length
    if len(value) < length:
        return value + ([None] * (length - len(value)))
    return value


def _nearest_sample(samples: list[_HourlyWeatherSample], target: datetime) -> _HourlyWeatherSample | None:
    if not samples:
        return None
    return min(samples, key=lambda sample: abs((sample.timestamp - target).total_seconds()))


def _average_optional(values: object) -> float | None:
    collected = [float(value) for value in values if value is not None]
    if not collected:
        return None
    return mean(collected)


def _average_wind_direction(values: object) -> float | None:
    collected = [float(value) for value in values if value is not None]
    if not collected:
        return None

    import math

    sin_total = sum(math.sin(math.radians(value)) for value in collected)
    cos_total = sum(math.cos(math.radians(value)) for value in collected)
    if sin_total == 0 and cos_total == 0:
        return None
    return (math.degrees(math.atan2(sin_total, cos_total)) + 360.0) % 360.0


def _degrees_to_compass(value: float | None) -> str | None:
    if value is None:
        return None
    directions = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")
    index = round(value / 22.5) % len(directions)
    return directions[index]


def _weather_code_to_label(value: int | None) -> str | None:
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


def _min_optional(values: object) -> float | None:
    collected = [float(value) for value in values if value is not None]
    if not collected:
        return None
    return min(collected)


def _max_optional(values: object) -> float | None:
    collected = [float(value) for value in values if value is not None]
    if not collected:
        return None
    return max(collected)


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _to_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value)
    return None
