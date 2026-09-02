import io
import json
from datetime import UTC, datetime

from fit_to_md.infrastructure.weather.open_meteo import (
    OpenMeteoHistoricalWeatherProvider,
)


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._buffer = io.StringIO(json.dumps(payload))

    def __enter__(self) -> io.StringIO:
        return self._buffer

    def __exit__(self, exc_type, exc, tb) -> None:
        self._buffer.close()
        return None


def test_open_meteo_provider_parses_hourly_weather() -> None:
    calls: list[tuple[str, int]] = []

    def fake_urlopen(url: str, timeout: int):
        calls.append((url, timeout))
        return FakeResponse(
            {
                "hourly": {
                    "time": ["2026-03-24T11:00", "2026-03-24T12:00"],
                    "temperature_2m": [15.2, 16.5],
                    "apparent_temperature": [14.8, 15.9],
                    "weather_code": [0, 1],
                    "wind_speed_10m": [18.0, 20.0],
                    "wind_direction_10m": [225, 220],
                }
            }
        )

    provider = OpenMeteoHistoricalWeatherProvider(urlopen_fn=fake_urlopen)

    weather = provider.lookup(
        start_time=datetime(2026, 3, 24, 11, 20, tzinfo=UTC),
        end_time=datetime(2026, 3, 24, 12, 21, tzinfo=UTC),
        latitude_deg=48.877191,
        longitude_deg=2.293044,
    )

    assert calls
    assert weather is not None
    assert weather.source == "historical"
    assert weather.temperature_c == 15.2
    assert weather.apparent_temperature_c == 14.8
    assert weather.condition_summary == "Clear sky"
    assert weather.wind_speed_kmh == 19.0
    assert weather.wind_direction_label == "SW"
    assert weather.temperature_min_c == 15.2
    assert weather.temperature_max_c == 16.5
