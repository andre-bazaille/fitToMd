import io
import json
from datetime import UTC, datetime

import pytest

from fit_to_md.domain.reporting.ports import ProviderDiagnosticKind
from fit_to_md.infrastructure.weather.open_meteo import (
    OpenMeteoHistoricalWeatherProvider,
)


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self._buffer = io.StringIO(json.dumps(payload))

    def __enter__(self) -> io.StringIO:
        return self._buffer

    def __exit__(self, exc_type, exc, tb) -> None:
        self._buffer.close()
        return None


class RawResponse:
    def __init__(self, body: str) -> None:
        self._buffer = io.StringIO(body)

    def __enter__(self) -> io.StringIO:
        return self._buffer

    def __exit__(self, exc_type, exc, tb) -> None:
        self._buffer.close()
        return None


def _lookup_weather(payload: object):
    provider = OpenMeteoHistoricalWeatherProvider(
        urlopen_fn=lambda *args, **kwargs: FakeResponse(payload)
    )
    return provider.lookup(
        start_time=datetime(2026, 3, 24, 11, 20, tzinfo=UTC),
        end_time=datetime(2026, 3, 24, 12, 21, tzinfo=UTC),
        latitude_deg=48.877191,
        longitude_deg=2.293044,
    ).value


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

    result = provider.lookup(
        start_time=datetime(2026, 3, 24, 11, 20, tzinfo=UTC),
        end_time=datetime(2026, 3, 24, 12, 21, tzinfo=UTC),
        latitude_deg=48.877191,
        longitude_deg=2.293044,
    )

    weather = result.value
    assert calls
    assert result.diagnostics == ()
    assert weather is not None
    assert weather.source == "historical"
    assert weather.temperature_c == 15.2
    assert weather.apparent_temperature_c == 14.8
    assert weather.condition_summary == "Clear sky"
    assert weather.wind_speed_kmh == 19.0
    assert weather.wind_direction_label == "SW"
    assert weather.temperature_min_c == 15.2
    assert weather.temperature_max_c == 16.5


@pytest.mark.parametrize("payload", ([], None, "unexpected", 42, True))
def test_open_meteo_provider_returns_none_for_non_object_payloads(
    payload: object,
) -> None:
    assert _lookup_weather(payload) is None


@pytest.mark.parametrize(
    "payload",
    (
        {},
        {"hourly": []},
        {"hourly": {"time": "2026-03-24T11:00"}},
        {"hourly": {"time": [None, 42, "invalid"]}},
        {
            "hourly": {
                "time": ["2026-03-24T11:00"],
                "temperature_2m": [10**400],
                "apparent_temperature": ["14.8"],
                "weather_code": [float("nan")],
                "wind_speed_10m": [{}],
                "wind_direction_10m": [float("inf")],
            }
        },
    ),
)
def test_open_meteo_provider_returns_none_for_unusable_nested_values(
    payload: object,
) -> None:
    assert _lookup_weather(payload) is None


def test_open_meteo_provider_keeps_valid_fields_from_partial_sample() -> None:
    weather = _lookup_weather(
        {
            "hourly": {
                "time": ["2026-03-24T11:00"],
                "temperature_2m": [15.2],
                "apparent_temperature": [float("-inf")],
                "weather_code": [False],
                "wind_speed_10m": ["18.0"],
                "wind_direction_10m": [{}],
            }
        }
    )

    assert weather is not None
    assert weather.temperature_c == 15.2
    assert weather.apparent_temperature_c is None
    assert weather.condition_summary is None
    assert weather.wind_speed_kmh is None
    assert weather.wind_direction_label is None


def test_open_meteo_provider_returns_none_for_invalid_json() -> None:
    provider = OpenMeteoHistoricalWeatherProvider(
        urlopen_fn=lambda *args, **kwargs: RawResponse("{")
    )

    result = provider.lookup(
        start_time=datetime(2026, 3, 24, 11, 20, tzinfo=UTC),
        end_time=None,
        latitude_deg=48.877191,
        longitude_deg=2.293044,
    )

    assert result.value is None
    assert result.diagnostics[0].kind is ProviderDiagnosticKind.INVALID_RESPONSE


def test_open_meteo_provider_returns_none_for_transport_failure() -> None:
    def failing_urlopen(*args, **kwargs):
        raise OSError("network unavailable")

    provider = OpenMeteoHistoricalWeatherProvider(urlopen_fn=failing_urlopen)

    result = provider.lookup(
        start_time=datetime(2026, 3, 24, 11, 20, tzinfo=UTC),
        end_time=None,
        latitude_deg=48.877191,
        longitude_deg=2.293044,
    )

    assert result.value is None
    assert result.diagnostics[0].kind is ProviderDiagnosticKind.PROVIDER_UNAVAILABLE


def test_open_meteo_provider_reports_valid_empty_data_as_no_coverage() -> None:
    provider = OpenMeteoHistoricalWeatherProvider(
        urlopen_fn=lambda *args, **kwargs: FakeResponse({"hourly": {"time": []}})
    )

    result = provider.lookup(
        start_time=datetime(2026, 3, 24, 11, 20, tzinfo=UTC),
        end_time=None,
        latitude_deg=48.877191,
        longitude_deg=2.293044,
    )

    assert result.value is None
    assert result.diagnostics[0].kind is ProviderDiagnosticKind.NO_COVERAGE


def test_open_meteo_provider_does_not_swallow_unexpected_errors() -> None:
    def failing_urlopen(*args, **kwargs):
        raise RuntimeError("programming error")

    provider = OpenMeteoHistoricalWeatherProvider(urlopen_fn=failing_urlopen)

    with pytest.raises(RuntimeError, match="programming error"):
        provider.lookup(
            start_time=datetime(2026, 3, 24, 11, 20, tzinfo=UTC),
            end_time=None,
            latitude_deg=48.877191,
            longitude_deg=2.293044,
        )
