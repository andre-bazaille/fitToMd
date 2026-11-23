import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from io import BytesIO
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from fit_to_md.application.synchronization_discovery import (
    CatalogueDates,
    CatalogueFailure,
    CatalogueFailureKind,
)
from fit_to_md.domain.coros_connection import ISSUERS, CorosAccount, Session
from fit_to_md.infrastructure import coros_gateway
from fit_to_md.infrastructure.coros_gateway import (
    CorosGateway,
    HttpsTransport,
    Response,
)
from tests.support.coros import FakeTransport, catalogue_reply, reply, rpc_reply

DATES = CatalogueDates(date(2026, 11, 19), date(2026, 11, 22))
ACCOUNT = CorosAccount("eu", ISSUERS["eu"], "private-subject")
SESSION = Session("client", "private-access", "private-refresh", 10**12)
INIT = rpc_reply(1, {"protocolVersion": "2025-06-18"})
NOW = 1800000000
TIMES = "startTimestamp=1795262400 | endTimestamp=1795262500"


def empty() -> Response:
    return catalogue_reply(DATES.start, DATES.end, [])


def gateway(
    transport: FakeTransport, sleep: Callable[[float], None] | None = None
) -> CorosGateway:
    return CorosGateway(
        "eu",
        transport,
        now=lambda: NOW,
        monotonic=lambda: 0,
        sleep=sleep or (lambda _: pytest.fail("unexpected retry")),
    )


@pytest.mark.parametrize("encoded", [False, True])
def test_catalogue_maps_rows_and_sends_only_explicit_metadata_query(
    encoded: bool,
) -> None:
    transport = FakeTransport(
        INIT,
        catalogue_reply(
            DATES.start, DATES.end, [("1234", 101, TIMES)], encoded=encoded
        ),
    )
    page = gateway(transport).prepare_catalogue(ACCOUNT, SESSION)(DATES)
    assert page.dates == DATES
    (row,) = page.activities
    assert row.identity.account.subject == ACCOUNT.subject
    assert row.identity.remote_id == "1234"
    assert row.identity.sport == 101
    assert row.start_at == datetime.fromtimestamp(1795262400, UTC)
    init, query = [json.loads(request.data or b"") for request in transport.requests]
    assert init["method"] == "initialize"
    assert query["params"] == {
        "name": "querySportRecords",
        "arguments": {"startDate": "20261119", "endDate": "20261122", "limit": 20},
    }


def test_recognizes_verified_and_unverified_empty_catalogues() -> None:
    transport = FakeTransport(
        INIT, empty(), rpc_reply(2, {"structuredContent": {"records": []}})
    )
    reader = gateway(transport).prepare_catalogue(ACCOUNT, SESSION)
    assert reader(DATES).dates == DATES
    page = reader(DATES)
    assert not page.activities
    assert page.dates is None


@pytest.mark.parametrize(
    ("response", "kind"),
    [
        (Response(200, {}, b"private malformed body"), CatalogueFailureKind.RESPONSE),
        (rpc_reply(3, {}), CatalogueFailureKind.RESPONSE),
        (
            rpc_reply(2, {"structuredContent": {"records": [{}]}}),
            CatalogueFailureKind.RESPONSE,
        ),
        (rpc_reply(2, {"content": [None]}), CatalogueFailureKind.RESPONSE),
        (
            rpc_reply(2, {"isError": True, "content": "private"}),
            CatalogueFailureKind.TOOL,
        ),
        (rpc_reply(2, {"isError": "private"}), CatalogueFailureKind.RESPONSE),
        (
            reply({"jsonrpc": "2.0", "id": 2, "error": {"message": "private"}}),
            CatalogueFailureKind.RPC,
        ),
        (reply({"message": "private"}, 404), CatalogueFailureKind.HTTP),
        (reply({"message": "private"}, 401), CatalogueFailureKind.AUTHENTICATION),
        (reply({"message": "private"}, 403), CatalogueFailureKind.AUTHENTICATION),
        (
            catalogue_reply(DATES.start, DATES.end, [], count=2),
            CatalogueFailureKind.RESPONSE,
        ),
        (catalogue_reply(DATES.end, DATES.start, []), CatalogueFailureKind.BOUNDS),
        (
            rpc_reply(
                2,
                {
                    "content": [
                        {
                            "type": "text",
                            "text": "Sport Records — 2026-02-31 to 2026-11-22 (0 records)",
                        }
                    ]
                },
            ),
            CatalogueFailureKind.BOUNDS,
        ),
    ],
)
def test_distinguishes_permanent_errors_without_retries_or_private_data(
    response: Response, kind: CatalogueFailureKind
) -> None:
    transport = FakeTransport(INIT, response)
    reader = gateway(transport).prepare_catalogue(ACCOUNT, SESSION)
    with pytest.raises(CatalogueFailure) as error:
        reader(DATES)
    assert error.value.kind is kind
    assert "private" not in str(error.value)
    assert len(transport.requests) == 2


@pytest.mark.parametrize(
    "invalid",
    [
        "endTimestamp=1795262500",
        "startTimestamp=1795262400000 | endTimestamp=1795262500",
        "startTimestamp= | startTimestamp=1795262400 | endTimestamp=1795262500",
        "startTimestamp=1795262400 | endTimestamp=1795262000",
        "startTimestamp=999999999999 | endTimestamp=1795262500",
        "startTimestamp=1795262400",
    ],
)
def test_invalid_times_do_not_shift_to_another_identity(invalid: str) -> None:
    response = catalogue_reply(
        DATES.start, DATES.end, [("12", 100, invalid), ("13", 101, TIMES)]
    )
    reader = gateway(FakeTransport(INIT, response)).prepare_catalogue(ACCOUNT, SESSION)
    bad, good = reader(DATES).activities
    assert (bad.identity.remote_id, bad.start_at) == ("12", None)
    assert good.identity.remote_id == "13" and good.start_at is not None


@pytest.mark.parametrize("success", [True, False])
def test_retries_http_at_most_three_times_and_reuses_negotiation(success: bool) -> None:
    waits: list[float] = []
    transport = FakeTransport(
        INIT,
        reply({}, 408),
        Response(429, {"Retry-After": "2"}, b""),
        empty() if success else reply({}, 503),
        empty(),
    )
    reader = gateway(transport, waits.append).prepare_catalogue(ACCOUNT, SESSION)
    if success:
        assert reader(DATES).dates == DATES
    else:
        with pytest.raises(CatalogueFailure) as error:
            reader(DATES)
        assert error.value.status == 503
    assert waits == [1, 2]
    assert len(transport.requests) == 4
    assert reader(DATES).dates == DATES
    assert len(transport.requests) == 5


def test_retry_after_exceeding_next_pause_is_returned_without_waiting() -> None:
    retry = Response(503, {"retry-after": "90"}, b"")
    transport = FakeTransport(INIT, retry)
    reader = gateway(transport).prepare_catalogue(ACCOUNT, SESSION)
    with pytest.raises(CatalogueFailure) as error:
        reader(DATES)
    assert error.value.retry_at == datetime.fromtimestamp(NOW + 90, UTC)
    assert len(transport.requests) == 2


def test_network_retries_and_timeout_using_https_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeouts = []

    def open_request(request: Request, *, timeout: int) -> None:
        timeouts.append(timeout)
        raise URLError("private network details")

    monkeypatch.setattr(
        coros_gateway, "build_opener", lambda *_: SimpleNamespace(open=open_request)
    )
    waits: list[float] = []
    transport = FakeTransport(INIT, *([HttpsTransport().send] * 3))
    reader = gateway(transport, waits.append).prepare_catalogue(ACCOUNT, SESSION)
    with pytest.raises(CatalogueFailure) as error:
        reader(DATES)
    assert error.value.kind is CatalogueFailureKind.NETWORK
    assert "private" not in str(error.value)
    assert timeouts == [10, 10, 10]
    assert waits == [1, 2]


def test_unreadable_mcp_error_body_does_not_hide_revocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnreadableBody(BytesIO):
        def read(self, size: int = -1) -> bytes:
            pytest.fail("MCP error body was read")

    def open_request(request: Request, *, timeout: int) -> None:
        raise HTTPError(request.full_url, 403, "Forbidden", {}, UnreadableBody())

    monkeypatch.setattr(
        coros_gateway, "build_opener", lambda *_: SimpleNamespace(open=open_request)
    )
    transport = FakeTransport(INIT, HttpsTransport().send)
    reader = gateway(transport).prepare_catalogue(ACCOUNT, SESSION)
    with pytest.raises(CatalogueFailure) as error:
        reader(DATES)
    assert error.value.kind is CatalogueFailureKind.AUTHENTICATION
    assert len(transport.requests) == 2


def test_negotiation_retry_and_version_validation() -> None:
    waits: list[float] = []
    transport = FakeTransport(
        reply({}, 502), rpc_reply(1, {"protocolVersion": "unknown"})
    )
    with pytest.raises(CatalogueFailure) as error:
        gateway(transport, waits.append).prepare_catalogue(ACCOUNT, SESSION)
    assert error.value.kind is CatalogueFailureKind.RESPONSE
    assert waits == [1]
    assert len(transport.requests) == 2


def test_mismatched_gateway_does_not_negotiate() -> None:
    other = CorosAccount("us", ISSUERS["us"], "private-subject")
    with pytest.raises(CatalogueFailure) as error:
        gateway(FakeTransport()).prepare_catalogue(other, SESSION)
    assert error.value.kind is CatalogueFailureKind.AUTHENTICATION
