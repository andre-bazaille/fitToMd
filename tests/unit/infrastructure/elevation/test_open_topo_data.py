import io
import json
from urllib.request import Request

import pytest

from fit_to_md.domain.reporting.ports import (
    ElevationCoordinate,
    ElevationRunStatistics,
)
from fit_to_md.infrastructure.elevation.open_topo_data import (
    OpenTopoDataElevationProvider,
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


def _coordinates(count: int) -> tuple[ElevationCoordinate, ...]:
    return tuple(
        ElevationCoordinate(latitude_deg=45.0, longitude_deg=7.0 + index)
        for index in range(count)
    )


def _lookup_elevations(payload: object, count: int = 1) -> tuple[float | None, ...]:
    provider = OpenTopoDataElevationProvider(
        urlopen_fn=lambda *args, **kwargs: FakeResponse(payload)
    )
    return provider.lookup(_coordinates(count))


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

    elevations = provider.lookup(
        (ElevationCoordinate(latitude_deg=45.0, longitude_deg=7.0),)
    )

    assert elevations == (None,)


@pytest.mark.parametrize("payload", ([], None, "unexpected", 42, True))
def test_open_topo_data_provider_returns_none_for_non_object_payloads(
    payload: object,
) -> None:
    assert _lookup_elevations(payload) == (None,)


@pytest.mark.parametrize(
    "payload",
    (
        {},
        {"status": "OK"},
        {"status": "OK", "results": {}},
        {"status": "OK", "results": "unexpected"},
    ),
)
def test_open_topo_data_provider_returns_none_for_malformed_nested_values(
    payload: object,
) -> None:
    assert _lookup_elevations(payload) == (None,)


def test_open_topo_data_provider_keeps_valid_values_from_partial_results() -> None:
    elevations = _lookup_elevations(
        {
            "status": "OK",
            "results": [
                {"elevation": 123},
                {"elevation": True},
                {"elevation": "456"},
                {"elevation": {}},
                {"elevation": float("nan")},
                {"elevation": float("inf")},
                {"elevation": 10**400},
                "unexpected",
            ],
        },
        count=8,
    )

    assert elevations == (123.0, None, None, None, None, None, None, None)


@pytest.mark.parametrize(
    ("results", "expected"),
    (
        ([{"elevation": 12}], (12.0, None)),
        ([{"elevation": 12}, {"elevation": 13}, {"elevation": 14}], (12.0, 13.0)),
    ),
)
def test_open_topo_data_provider_preserves_expected_result_length(
    results: list[object], expected: tuple[float | None, ...]
) -> None:
    assert _lookup_elevations({"status": "OK", "results": results}, count=2) == expected


def test_open_topo_data_provider_returns_none_for_invalid_json() -> None:
    provider = OpenTopoDataElevationProvider(
        urlopen_fn=lambda *args, **kwargs: RawResponse("{")
    )

    assert provider.lookup(_coordinates(2)) == (None, None)


def test_open_topo_data_provider_returns_none_for_transport_failure() -> None:
    def failing_urlopen(*args, **kwargs):
        raise OSError("network unavailable")

    provider = OpenTopoDataElevationProvider(urlopen_fn=failing_urlopen)

    assert provider.lookup(_coordinates(2)) == (None, None)


def test_open_topo_data_provider_continues_after_malformed_batch() -> None:
    responses = iter(([], {"status": "OK", "results": [{"elevation": 456}]}))
    provider = OpenTopoDataElevationProvider(
        base_url="https://elevation.internal",
        max_batch_size=1,
        urlopen_fn=lambda *args, **kwargs: FakeResponse(next(responses)),
    )

    assert provider.lookup(_coordinates(2)) == (None, 456.0)


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

    elevations = provider.lookup(
        (ElevationCoordinate(latitude_deg=45.0, longitude_deg=7.0),)
    )

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
                ElevationCoordinate(
                    latitude_deg=45.0, longitude_deg=7.0 + (index * 0.0001)
                )
                for index in range(1001)
            )
        )

    assert "more than 1000 requests" in str(error.value)


def test_open_topo_data_provider_tracks_public_api_run_statistics() -> None:
    def fake_urlopen(request: Request, timeout: int):
        return FakeResponse(
            {
                "status": "OK",
                "results": [{"elevation": 123}],
            }
        )

    provider = OpenTopoDataElevationProvider(urlopen_fn=fake_urlopen)

    provider.lookup((ElevationCoordinate(latitude_deg=45.0, longitude_deg=7.0),))

    assert provider.run_statistics() == ElevationRunStatistics(
        provider_name="OpenTopoData",
        request_count=1,
        request_limit=1000,
    )


def test_open_topo_data_provider_tracks_self_hosted_run_statistics() -> None:
    provider = OpenTopoDataElevationProvider(base_url="https://elevation.internal")

    assert provider.run_statistics() == ElevationRunStatistics(
        provider_name="OpenTopoData",
        request_count=0,
        request_limit=None,
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
    provider.set_progress_callback(
        lambda current, total: progress_updates.append((current, total))
    )

    provider.lookup(
        (
            ElevationCoordinate(latitude_deg=45.0, longitude_deg=7.0),
            ElevationCoordinate(latitude_deg=45.1, longitude_deg=7.1),
        )
    )

    assert progress_updates == [(1, 2), (2, 2)]
