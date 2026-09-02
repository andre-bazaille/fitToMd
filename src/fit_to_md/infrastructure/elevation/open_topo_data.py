import json
from collections.abc import Callable, Sequence
from time import monotonic, sleep
from typing import Any
from urllib.request import Request, urlopen

from fit_to_md.domain.reporting.ports import ElevationCoordinate

_PUBLIC_API_BASE_URL = "https://api.opentopodata.org"
_PUBLIC_API_MAX_BATCH_SIZE = 100
_PUBLIC_API_MAX_CALLS_PER_RUN = 1000
_PUBLIC_API_MIN_INTERVAL_S = 1.0


class OpenTopoDataElevationProvider:
    def __init__(
        self,
        base_url: str = "https://api.opentopodata.org",
        dataset: str = "eudem25m",
        interpolation: str = "bilinear",
        timeout_s: int = 10,
        max_batch_size: int = 100,
        urlopen_fn: Callable[..., Any] = urlopen,
        sleep_fn: Callable[[float], None] = sleep,
        monotonic_fn: Callable[[], float] = monotonic,
    ) -> None:
        if max_batch_size <= 0:
            raise ValueError("max_batch_size must be positive")

        self._base_url = base_url.rstrip("/")
        self._dataset = dataset
        self._interpolation = interpolation
        self._timeout_s = timeout_s
        self._max_batch_size = (
            min(max_batch_size, _PUBLIC_API_MAX_BATCH_SIZE)
            if self.is_public_api
            else max_batch_size
        )
        self._urlopen_fn = urlopen_fn
        self._sleep_fn = sleep_fn
        self._monotonic_fn = monotonic_fn
        self._request_count = 0
        self._last_request_started_at: float | None = None
        self._progress_callback: Callable[[int, int], None] | None = None

    def lookup(
        self, coordinates: Sequence[ElevationCoordinate]
    ) -> tuple[float | None, ...]:
        if not coordinates:
            return tuple()

        batches = _chunk_coordinates(coordinates, self._max_batch_size)
        if (
            self.is_public_api
            and (self._request_count + len(batches)) > _PUBLIC_API_MAX_CALLS_PER_RUN
        ):
            raise RuntimeError(
                "OpenTopoData public API limit exceeded for this run: more than 1000 requests would be required. "
                "Reduce DEM sampling density or use a self-hosted instance."
            )

        elevations: list[float | None] = []
        total_batches = len(batches)
        for index, batch in enumerate(batches, start=1):
            self._throttle_if_needed()
            request = self._build_request(batch)
            self._request_count += 1
            if self._progress_callback is not None:
                self._progress_callback(index, total_batches)
            try:
                with self._urlopen_fn(request, timeout=self._timeout_s) as response:
                    payload = json.load(response)
            except Exception:
                elevations.extend([None] * len(batch))
                continue

            elevations.extend(_parse_elevations(payload, expected_count=len(batch)))
        return tuple(elevations)

    @property
    def is_public_api(self) -> bool:
        return self._base_url == _PUBLIC_API_BASE_URL

    @property
    def request_count(self) -> int:
        return self._request_count

    def usage_summary(self) -> str:
        if self.is_public_api:
            return (
                f"OpenTopoData public API calls this run: {self._request_count}/{_PUBLIC_API_MAX_CALLS_PER_RUN} "
                "(daily usage is not persisted by the CLI)."
            )
        return f"OpenTopoData requests this run: {self._request_count}."

    def set_progress_callback(
        self, callback: Callable[[int, int], None] | None
    ) -> None:
        self._progress_callback = callback

    def _build_request(self, coordinates: Sequence[ElevationCoordinate]) -> Request:
        locations = "|".join(
            f"{coordinate.latitude_deg:.6f},{coordinate.longitude_deg:.6f}"
            for coordinate in coordinates
        )
        payload = json.dumps(
            {
                "locations": locations,
                "interpolation": self._interpolation,
                "nodata_value": "null",
            }
        ).encode("utf-8")
        return Request(
            url=f"{self._base_url}/v1/{self._dataset}",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

    def _throttle_if_needed(self) -> None:
        if not self.is_public_api:
            return
        if self._last_request_started_at is None:
            self._last_request_started_at = self._monotonic_fn()
            return

        elapsed_s = self._monotonic_fn() - self._last_request_started_at
        if elapsed_s < _PUBLIC_API_MIN_INTERVAL_S:
            self._sleep_fn(_PUBLIC_API_MIN_INTERVAL_S - elapsed_s)
        self._last_request_started_at = self._monotonic_fn()


def _chunk_coordinates(
    coordinates: Sequence[ElevationCoordinate],
    max_batch_size: int,
) -> list[Sequence[ElevationCoordinate]]:
    return [
        coordinates[index : index + max_batch_size]
        for index in range(0, len(coordinates), max_batch_size)
    ]


def _parse_elevations(
    payload: dict[str, Any], expected_count: int
) -> list[float | None]:
    if payload.get("status") != "OK":
        return [None] * expected_count

    results = payload.get("results")
    if not isinstance(results, list):
        return [None] * expected_count

    elevations = [
        _to_float(result.get("elevation")) if isinstance(result, dict) else None
        for result in results
    ]
    if len(elevations) < expected_count:
        elevations.extend([None] * (expected_count - len(elevations)))
    return elevations[:expected_count]


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None
