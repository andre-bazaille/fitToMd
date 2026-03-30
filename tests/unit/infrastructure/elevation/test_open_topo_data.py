from __future__ import annotations

import io
import json
from urllib.request import Request

import pytest

from fit_to_md.domain.reporting.ports import ElevationCoordinate
from fit_to_md.infrastructure.elevation.open_topo_data import OpenTopoDataElevationProvider


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._buffer = io.StringIO(json.dumps(payload))

    def __enter__(self) -> io.StringIO:
        return self._buffer

    def __exit__(self, exc_type, exc, tb) -> None:
        self._buffer.close()
        return None


def test_open_topo_data_provider_posts_coordinates_and_parses_elevations() -> None:
    calls: list[tuple[Request, int]] = []

    def fake_urlopen(request: Request, timeout: int):
        calls.append((request, timeout))
        return FakeResponse(
            {
                "status": "OK",
                "results": [
                    {"elevation": 123},
                    {"elevation": 456.5},
                ],
            }
        )

    provider = OpenTopoDataElevationProvider(urlopen_fn=fake_urlopen)

    elevations = provider.lookup(
        (
            ElevationCoordinate(latitude_deg=45.1234567, longitude_deg=7.1234567),
            ElevationCoordinate(latitude_deg=45.2234567, longitude_deg=7.2234567),
        )
    )

    assert elevations == (123.0, 456.5)
    assert calls
    request, timeout = calls[0]
    assert request.full_url == "https://api.opentopodata.org/v1/eudem25m"
    assert request.get_method() == "POST"
    assert timeout == 10
    assert json.loads(request.data.decode("utf-8")) == {
        "locations": "45.123457,7.123457|45.223457,7.223457",
        "interpolation": "bilinear",
        "nodata_value": "null",
    }


def test_open_topo_data_provider_returns_none_for_failed_response() -> None:
    def fake_urlopen(request: Request, timeout: int):
        return FakeResponse(
            {
                "status": "SERVER_ERROR",
                "error": "rate limited",
            }
        )

    provider = OpenTopoDataElevationProvider(urlopen_fn=fake_urlopen)

    elevations = provider.lookup((ElevationCoordinate(latitude_deg=45.0, longitude_deg=7.0),))

    assert elevations == (None,)


def test_open_topo_data_provider_uses_custom_dataset_and_base_url() -> None:
    calls: list[tuple[Request, int]] = []

    def fake_urlopen(request: Request, timeout: int):
        calls.append((request, timeout))
        return FakeResponse(
            {
                "status": "OK",
                "results": [{"elevation": 123}],
            }
        )

    provider = OpenTopoDataElevationProvider(
        base_url="https://elevation.internal/api",
        dataset="copernicus",
        urlopen_fn=fake_urlopen,
    )

    elevations = provider.lookup((ElevationCoordinate(latitude_deg=45.0, longitude_deg=7.0),))

    assert elevations == (123.0,)
    request, _ = calls[0]
    assert request.full_url == "https://elevation.internal/api/v1/copernicus"


def test_open_topo_data_public_api_rate_limits_to_one_call_per_second() -> None:
    calls: list[tuple[Request, int]] = []
    sleeps: list[float] = []
    monotonic_values = iter((0.0, 0.2, 1.0))

    def fake_urlopen(request: Request, timeout: int):
        calls.append((request, timeout))
        return FakeResponse(
            {
                "status": "OK",
                "results": [{"elevation": 123}],
            }
        )

    provider = OpenTopoDataElevationProvider(
        max_batch_size=1,
        urlopen_fn=fake_urlopen,
        sleep_fn=sleeps.append,
        monotonic_fn=lambda: next(monotonic_values),
    )

    elevations = provider.lookup(
        (
            ElevationCoordinate(latitude_deg=45.0, longitude_deg=7.0),
            ElevationCoordinate(latitude_deg=45.1, longitude_deg=7.1),
        )
    )

    assert elevations == (123.0, 123.0)
    assert len(calls) == 2
    assert sleeps == [0.8]
    assert provider.request_count == 2


def test_open_topo_data_public_api_rejects_more_than_1000_calls_in_one_run() -> None:
    provider = OpenTopoDataElevationProvider(max_batch_size=1)

    with pytest.raises(RuntimeError) as error:
        provider.lookup(
            tuple(
                ElevationCoordinate(latitude_deg=45.0, longitude_deg=7.0 + (index * 0.0001))
                for index in range(1001)
            )
        )

    assert "more than 1000 requests" in str(error.value)


def test_open_topo_data_provider_tracks_usage_summary() -> None:
    def fake_urlopen(request: Request, timeout: int):
        return FakeResponse(
            {
                "status": "OK",
                "results": [{"elevation": 123}],
            }
        )

    provider = OpenTopoDataElevationProvider(urlopen_fn=fake_urlopen)

    provider.lookup((ElevationCoordinate(latitude_deg=45.0, longitude_deg=7.0),))

    assert provider.usage_summary() == (
        "OpenTopoData public API calls this run: 1/1000 (daily usage is not persisted by the CLI)."
    )


def test_open_topo_data_provider_reports_batch_progress() -> None:
    progress_updates: list[tuple[int, int]] = []

    def fake_urlopen(request: Request, timeout: int):
        return FakeResponse(
            {
                "status": "OK",
                "results": [{"elevation": 123}],
            }
        )

    provider = OpenTopoDataElevationProvider(max_batch_size=1, urlopen_fn=fake_urlopen)
    provider.set_progress_callback(lambda current, total: progress_updates.append((current, total)))

    provider.lookup(
        (
            ElevationCoordinate(latitude_deg=45.0, longitude_deg=7.0),
            ElevationCoordinate(latitude_deg=45.1, longitude_deg=7.1),
        )
    )

    assert progress_updates == [(1, 2), (2, 2)]