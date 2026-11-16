import io
import json
from datetime import UTC, datetime
from http.client import IncompleteRead
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

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


def _lookup_weather_result(payload: object):
    provider = OpenMeteoHistoricalWeatherProvider(
        urlopen_fn=lambda *args, **kwargs: FakeResponse(payload)
    )
    return provider.lookup(
        start_time=datetime(2026, 3, 24, 11, 20, tzinfo=UTC),
        end_time=datetime(2026, 3, 24, 12, 21, tzinfo=UTC),
        latitude_deg=48.877191,
        longitude_deg=2.293044,
    )


def _lookup_weather(payload: object):
    return _lookup_weather_result(payload).value


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


@pytest.mark.parametrize(
    ("start_day", "end_day", "expected_host"),
    [
        (8, 8, "api.open-meteo.com"),
        (3, 3, "api.open-meteo.com"),
        (2, 3, "api.open-meteo.com"),
        (2, 2, "archive-api.open-meteo.com"),
        (9, 9, "archive-api.open-meteo.com"),
    ],
)
def test_open_meteo_uses_recent_past_endpoint_during_archive_delay(
    start_day: int,
    end_day: int,
    expected_host: str,
) -> None:
    calls: list[str] = []

    def fake_urlopen(url: str, timeout: int):
        calls.append(url)
        return FakeResponse(
            {
                "hourly": {
                    "time": [f"2026-11-{start_day:02}T11:00"],
                    "temperature_2m": [6],
                }
            }
        )

    provider = OpenMeteoHistoricalWeatherProvider(
        urlopen_fn=fake_urlopen,
        now_fn=lambda: datetime(2026, 11, 8, 23, tzinfo=UTC),
    )
    result = provider.lookup(
        start_time=datetime(2026, 11, start_day, 11, tzinfo=UTC),
        end_time=datetime(2026, 11, end_day, 12, tzinfo=UTC),
        latitude_deg=48.85,
        longitude_deg=2.35,
    )

    assert urlsplit(calls[0]).netloc == expected_host
    assert parse_qs(urlsplit(calls[0]).query)["start_date"] == [
        f"2026-11-{start_day:02}"
    ]
    assert result.value is not None
    assert result.value.temperature_c == 6


def test_open_meteo_preserves_custom_endpoint_for_recent_dates() -> None:
    calls: list[str] = []

    def fake_urlopen(url: str, timeout: int):
        calls.append(url)
        return FakeResponse({"hourly": {"time": []}})

    provider = OpenMeteoHistoricalWeatherProvider(
        base_url="https://weather.example/archive",
        urlopen_fn=fake_urlopen,
        now_fn=lambda: datetime(2026, 11, 8, tzinfo=UTC),
    )
    provider.lookup(datetime(2026, 11, 8), None, 48.85, 2.35)

    assert calls[0].startswith("https://weather.example/archive?")


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
    result = _lookup_weather_result(
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

    weather = result.value
    assert weather is not None
    assert weather.temperature_c == 15.2
    assert weather.apparent_temperature_c is None
    assert weather.condition_summary is None
    assert weather.wind_speed_kmh is None
    assert weather.wind_direction_label is None
    assert result.diagnostics[0].kind is ProviderDiagnosticKind.INVALID_RESPONSE


def test_open_meteo_provider_classifies_malformed_series_as_invalid_response() -> None:
    result = _lookup_weather_result(
        {
            "hourly": {
                "time": ["2026-03-24T11:00"],
                "temperature_2m": "15.2",
                "apparent_temperature": [None],
                "weather_code": [None],
                "wind_speed_10m": [None],
                "wind_direction_10m": [None],
            }
        }
    )

    assert result.value is None
    assert result.diagnostics[0].kind is ProviderDiagnosticKind.INVALID_RESPONSE


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


def test_open_meteo_provider_returns_fallback_for_interrupted_response() -> None:
    def failing_urlopen(*args, **kwargs):
        raise IncompleteRead(b'{"hourly":')

    provider = OpenMeteoHistoricalWeatherProvider(urlopen_fn=failing_urlopen)

    result = provider.lookup(
        start_time=datetime(2026, 3, 24, 11, 20, tzinfo=UTC),
        end_time=None,
        latitude_deg=48.877191,
        longitude_deg=2.293044,
    )

    assert result.value is None
    assert result.diagnostics[0].kind is ProviderDiagnosticKind.PROVIDER_UNAVAILABLE


def test_open_meteo_provider_classifies_http_429_as_quota_exceeded() -> None:
    def failing_urlopen(*args, **kwargs):
        raise HTTPError(
            "https://weather.invalid",
            429,
            "Too Many Requests",
            hdrs=None,
            fp=None,
        )

    provider = OpenMeteoHistoricalWeatherProvider(urlopen_fn=failing_urlopen)

    result = provider.lookup(
        start_time=datetime(2026, 3, 24, 11, 20, tzinfo=UTC),
        end_time=None,
        latitude_deg=48.877191,
        longitude_deg=2.293044,
    )

    assert result.value is None
    assert result.diagnostics[0].kind is ProviderDiagnosticKind.QUOTA_EXCEEDED


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
