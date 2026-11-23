import base64
import hashlib
import json
import warnings
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request
from zoneinfo import ZoneInfo

import pytest

from fit_to_md import coros_probe_cli
from fit_to_md.application.coros_connection import CorosConnection
from fit_to_md.domain.coros_connection import ConnectionFailure, Session
from fit_to_md.infrastructure import coros_vault, local_time
from fit_to_md.infrastructure.coros_gateway import CorosGateway, Response
from fit_to_md.infrastructure.coros_vault import VaultSessionStore
from tests.support.coros import FakeTransport as Transport
from tests.support.coros import FakeVault as Vault
from tests.support.coros import reply

TOKEN = Session("client", "old-token", "old-refresh", 1000)
NEW = Session("client", "new-token", "new-refresh", 3700)


def token_reply() -> Response:
    return reply(
        {
            "token_type": "Bearer",
            "access_token": "new-token",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
        }
    )


def gateway(transport: Transport, clock: list[float] | None = None) -> CorosGateway:
    tick = clock if clock is not None else [0]
    return CorosGateway(
        "eu",
        transport,
        now=lambda: 100,
        monotonic=lambda: tick[0],
        sleep=lambda duration: tick.__setitem__(0, tick[0] + duration),
    )


def browser_session(login_url: str = "https://mcpeu.coros.com/login") -> Response:
    return reply(
        {
            "sessionId": "session",
            "pollToken": "poll",
            "loginUrl": login_url,
            "intervalSeconds": 2,
        },
        201,
    )


def test_browser_login_uses_pkce_and_vault() -> None:
    def callback(request: Request) -> Response:
        query.update(parse_qs(urlsplit(request.full_url).query))
        location = "http://127.0.0.1:43123/callback?" + urlencode(
            {"code": "code", "state": query["state"][0]}
        )
        return Response(303, {"Location": location}, b"")

    query: dict[str, list[str]] = {}
    transport = Transport(
        reply({"client_id": "client"}, 201),
        browser_session(),
        reply({"status": "pending"}),
        reply({"status": "authorized", "loginTicket": "ticket"}),
        callback,
        token_reply(),
    )
    opened: list[str] = []
    backend = Vault()
    CorosConnection(
        gateway(transport), VaultSessionStore("eu", backend), lambda: 100
    ).login(opened.append)

    assert opened == ["https://mcpeu.coros.com/login"]
    registration = json.loads(transport.requests[0].data or b"")
    assert registration["token_endpoint_auth_method"] == "none"
    assert transport.requests[2].get_header("X-poll-token") == "poll"
    form = parse_qs((transport.requests[-1].data or b"").decode())
    verifier = form["code_verifier"][0]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    assert query["code_challenge"] == [challenge.decode().rstrip("=")]
    assert query["code_challenge_method"] == ["S256"]
    assert all("client_secret" not in key for key in form)
    assert VaultSessionStore("eu", backend).load() == NEW
    assert "new-token" not in repr(NEW)


@pytest.mark.parametrize(
    ("responses", "message"),
    [
        ([reply({}, 403)], "registration failed"),
        (
            [
                reply({"client_id": "client"}),
                browser_session("https://coros.com.evil.test/login"),
            ],
            "invalid browser destination",
        ),
        (
            [
                reply({"client_id": "client"}),
                browser_session(),
                reply({"status": "expired"}),
            ],
            "failed or expired",
        ),
        (
            [
                reply({"client_id": "client"}),
                browser_session(),
                reply({"status": "authorized", "loginTicket": "ticket"}),
                Response(
                    302,
                    {"Location": "http://127.0.0.1:43123/callback?code=x&state=wrong"},
                    b"",
                ),
            ],
            "invalid COROS OAuth callback state",
        ),
        (
            [
                reply({"client_id": "client"}),
                browser_session(),
                reply({"status": "authorized", "loginTicket": "ticket"}),
                Response(302, {"Location": "https://evil.test/callback?code=x"}, b""),
            ],
            "expected OAuth callback",
        ),
    ],
)
def test_login_failures_do_not_store_tokens(
    responses: list[Response], message: str
) -> None:
    backend = Vault()
    store = VaultSessionStore("eu", backend)
    with pytest.raises(ConnectionFailure, match=f"(?i){message}"):
        CorosConnection(gateway(Transport(*responses)), store, lambda: 0).login(
            lambda _: None
        )
    assert store.load() is None


def test_locked_vault_blocks_login_before_network() -> None:
    backend = Vault()
    backend.available = False
    transport = Transport()
    with pytest.raises(ConnectionFailure, match="Could not read the system vault"):
        CorosConnection(
            gateway(transport), VaultSessionStore("eu", backend), lambda: 0
        ).login(lambda _: pytest.fail("Browser should not be opened"))
    assert transport.requests == []


def rpc(request_id: int, result: object) -> Response:
    return reply({"jsonrpc": "2.0", "id": request_id, "result": result})


def test_reuse_across_instances_and_paginate_tool_schema_list() -> None:
    backend = Vault()
    VaultSessionStore("eu", backend).save(TOKEN)
    transport = Transport(
        rpc(1, {"protocolVersion": "2025-06-18"}),
        rpc(2, {"tools": [{"name": "querySportRecords"}], "nextCursor": "next"}),
        Response(
            200,
            {"Content-Type": "text/event-stream"},
            b'data: {"method":"notice"}\n\ndata: {"id":3,"result":{"tools":[{"name":"downloadActivityFitFiles"}]}}\n\n',
        ),
    )
    result = CorosConnection(
        gateway(transport), VaultSessionStore("eu", backend), lambda: 100
    ).probe()
    assert result.tools == {"querySportRecords", "downloadActivityFitFiles"}
    assert not result.refreshed
    assert all(
        request.get_header("Authorization") == "Bearer old-token"
        for request in transport.requests
    )
    assert json.loads(transport.requests[-1].data or b"")["params"] == {
        "cursor": "next"
    }


@pytest.mark.parametrize(("now", "force"), [(100, True), (950, False)])
def test_refresh_is_saved_before_probe_even_if_probe_fails(
    now: float, force: bool
) -> None:
    backend = Vault()
    store = VaultSessionStore("eu", backend)
    store.save(TOKEN)

    def check_rotation(request: Request) -> Response:
        assert store.load() == NEW
        assert request.get_header("Authorization") == "Bearer new-token"
        return reply({}, 503)

    transport = Transport(token_reply(), check_rotation)
    with pytest.raises(ConnectionFailure, match="503"):
        CorosConnection(gateway(transport), store, lambda: now).probe(
            force_refresh=force
        )
    assert parse_qs((transport.requests[0].data or b"").decode()) == {
        "grant_type": ["refresh_token"],
        "refresh_token": ["old-refresh"],
        "client_id": ["client"],
    }


def test_refresh_rejection_leaves_vault_unchanged_and_redacts_response() -> None:
    store = VaultSessionStore("eu", Vault())
    store.save(TOKEN)
    transport = Transport(
        reply({"error_description": "secret in remote response"}, 400)
    )
    with pytest.raises(ConnectionFailure, match="COROS rejected the refresh") as error:
        CorosConnection(gateway(transport), store, lambda: 1100).probe()
    assert "secret" not in str(error.value)
    assert store.load() == TOKEN


def test_isolated_vault_slots_and_local_logout() -> None:
    backend = Vault()
    eu = VaultSessionStore("eu", backend)
    us = VaultSessionStore("us", backend)
    eu.save(TOKEN)
    us.save(NEW)
    transport = Transport()
    connection = CorosConnection(gateway(transport), eu, lambda: 100)
    connection.logout()
    connection.logout()
    with pytest.raises(ConnectionFailure, match="Run login first"):
        connection.probe()
    assert eu.load() is None and us.load() == NEW
    assert not transport.requests


def test_linux_locked_collection_fails_without_requesting_unlock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Bus:
        def close(self) -> None:
            pass

    class Collection:
        def __init__(self, bus: Bus) -> None:
            pass

        def is_locked(self) -> bool:
            return True

        def unlock(self) -> None:
            pytest.fail("Must not request an interactive unlock")

    module = ModuleType("secretstorage")
    module.dbus_init = Bus
    module.Collection = Collection
    monkeypatch.setattr(coros_vault.sys, "platform", "linux")
    monkeypatch.setattr(coros_vault, "import_module", lambda _: module)
    with pytest.raises(ConnectionFailure, match="Unlock the system vault before"):
        VaultSessionStore("eu", coros_vault.system_backend()).load()


def test_cli_reports_available_tools_and_missing_session(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    backend = Vault()
    transport = Transport(
        rpc(1, {"protocolVersion": "2025-06-18"}),
        rpc(
            2,
            {
                "tools": [
                    {"name": "querySportRecords"},
                    {"name": "downloadActivityFitFiles"},
                ]
            },
        ),
    )
    monkeypatch.setattr(coros_probe_cli, "system_backend", lambda: backend)
    monkeypatch.setattr(coros_probe_cli, "HttpsTransport", lambda: transport)
    monkeypatch.setattr(coros_probe_cli.time, "time", lambda: 100)
    assert coros_probe_cli.main(["probe"]) == 1
    assert "Run login first" in capsys.readouterr().err
    VaultSessionStore("eu", backend).save(TOKEN)
    assert coros_probe_cli.main(["probe"]) == 0
    assert "downloadActivityFitFiles: available" in capsys.readouterr().out
    assert coros_probe_cli.main(["logout"]) == 0
    assert VaultSessionStore("eu", backend).load() is None


def test_corrupted_vault_entry_reports_error_without_leaking_content() -> None:
    backend = Vault()
    backend.values["fit-to-md/coros-prototype/eu", "session"] = (
        '{"access_token":"private"}'
    )
    with pytest.raises(ConnectionFailure, match="Invalid COROS session") as error:
        VaultSessionStore("eu", backend).load()
    assert "private" not in str(error.value)


def test_probe_rejects_repeated_cursor() -> None:
    transport = Transport(
        rpc(1, {"protocolVersion": "2025-06-18"}),
        rpc(2, {"tools": [], "nextCursor": "same"}),
        rpc(3, {"tools": [], "nextCursor": "same"}),
    )
    with pytest.raises(ConnectionFailure, match="Invalid COROS tool catalog cursor"):
        gateway(transport).discover_tools(TOKEN)


def test_probe_cli_reports_missing_tools(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    backend = Vault()
    VaultSessionStore("eu", backend).save(TOKEN)
    transport = Transport(
        rpc(1, {"protocolVersion": "2025-06-18"}), rpc(2, {"tools": []})
    )
    monkeypatch.setattr(coros_probe_cli, "system_backend", lambda: backend)
    monkeypatch.setattr(coros_probe_cli, "HttpsTransport", lambda: transport)
    monkeypatch.setattr(coros_probe_cli.time, "time", lambda: 100)
    assert coros_probe_cli.main(["probe"]) == 1
    assert "Required COROS catalog or FIT tools are missing" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("result", "expected", "status"),
    [
        (
            {
                "content": [
                    {
                        "type": "text",
                        "text": "Sport Records — 2020-11-08 to 2020-11-08 (1 record)\nPrivate activity title",
                    }
                ]
            },
            "Activities reported: 1.",
            0,
        ),
        ({"structuredContent": {"records": []}}, "Activities returned: 0.", 0),
        (
            {
                "isError": True,
                "content": [{"type": "text", "text": "Private activity title"}],
            },
            "COROS refused the activity query.",
            1,
        ),
        (
            {"content": [{"type": "text", "text": "Private activity title"}]},
            "Unrecognized COROS activity response",
            1,
        ),
    ],
)
def test_historical_activity_probe(
    result: object,
    expected: str,
    status: int,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return date(2026, 11, 16)

    monkeypatch.setattr(coros_probe_cli, "date", FixedDate)
    backend = Vault()
    VaultSessionStore("eu", backend).save(TOKEN)
    transport = Transport(rpc(1, {"protocolVersion": "2025-06-18"}), rpc(2, result))
    monkeypatch.setattr(coros_probe_cli, "system_backend", lambda: backend)
    monkeypatch.setattr(coros_probe_cli, "HttpsTransport", lambda: transport)
    monkeypatch.setattr(coros_probe_cli.time, "time", lambda: 100)
    assert coros_probe_cli.main(["activities", "--on", "2020-11-08"]) == status
    output = capsys.readouterr()
    assert expected in output.out + output.err
    assert "Private activity title" not in output.out + output.err
    assert json.loads(transport.requests[1].data or b"")["params"] == {
        "name": "querySportRecords",
        "arguments": {"startDate": "20201108", "endDate": "20201108", "limit": 20},
    }

    inspected_transport = Transport(
        rpc(1, {"protocolVersion": "2025-06-18"}), rpc(2, result)
    )
    monkeypatch.setattr(coros_probe_cli, "HttpsTransport", lambda: inspected_transport)
    assert (
        coros_probe_cli.main(["activities", "--on", "2020-11-08", "--inspect-response"])
        == status
    )
    inspected = capsys.readouterr()
    assert "COROS activity response (local inspection only)" in inspected.err
    assert json.dumps(result, ensure_ascii=False, indent=2) in inspected.err
    assert TOKEN.access_token not in inspected.err


def test_double_encoded_activity_summary() -> None:
    day = date(2020, 11, 8)
    transport = Transport(
        rpc(1, {"protocolVersion": "2025-06-18"}),
        rpc(2, wrapped_activity()),
    )
    result = gateway(transport).probe_activities(TOKEN, day)
    assert (result.count, result.returned_period) == (1, ("2020-11-08", "2020-11-08"))
    assert [(ref.label_id, ref.sport_type) for ref in result.references] == [
        ("111222333444555666", 100)
    ]


def wrapped_activity(count: int = 1) -> dict[str, object]:
    summary = (
        f"Sport Records — 2020-11-08 to 2020-11-08 ({count} records)\n"
        "========================\n\n"
        "1. Outdoor Run — 2020-11-08\n"
        "   Time Window: startTimestamp=1604830000 | endTimestamp=1604831800\n"
        "   LabelId: 111222333444555666 | SportType: 100"
    )
    return {
        "content": [{"type": "text", "text": json.dumps(summary)}],
        "isError": False,
    }


@pytest.mark.parametrize("valid", [True, False])
def test_fit_sample_uses_exact_activity_and_writes_only_valid_fit(
    valid: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = Vault()
    VaultSessionStore("eu", backend).save(
        Session("client", "access", "refresh", 10**12)
    )
    data = (
        Path(__file__).parents[1] / "fit_files" / "coros_synthetic.fit"
    ).read_bytes()
    binary = data if valid else b"invalid FIT"
    transport = Transport(
        rpc(1, {"protocolVersion": "2025-06-18"}),
        rpc(2, wrapped_activity()),
        rpc(
            3,
            {
                "content": [
                    {
                        "type": "resource",
                        "resource": {
                            "blob": base64.b64encode(binary).decode(),
                        },
                    }
                ]
            },
        ),
    )
    monkeypatch.setattr(coros_probe_cli, "system_backend", lambda: backend)
    monkeypatch.setattr(coros_probe_cli, "HttpsTransport", lambda: transport)
    output = tmp_path / "sample.fit"
    args = ["fit", "--on", "2020-11-08", "--output", str(output)]
    assert coros_probe_cli.main(args) == (0 if valid else 1)
    assert json.loads(transport.requests[-1].data or b"")["params"] == {
        "name": "downloadActivityFitFiles",
        "arguments": {"labelId": "111222333444555666", "sportType": 100},
    }
    if valid:
        assert output.read_bytes() == data
        assert coros_probe_cli.main(args) == 1
        assert len(transport.requests) == 3
        assert output.read_bytes() == data
    else:
        assert not output.exists()


def test_fit_sample_refuses_to_guess_an_activity() -> None:
    transport = Transport(
        rpc(1, {"protocolVersion": "2025-06-18"}),
        rpc(2, wrapped_activity(2)),
    )
    with pytest.raises(
        ConnectionFailure, match="requires exactly one identifiable activity"
    ):
        gateway(transport).download_sample(TOKEN, date(2020, 11, 8))


def identity_metadata(issuer: str = "https://mcpeu.coros.com") -> Response:
    return reply({"issuer": issuer, "userinfo_endpoint": issuer + "/connect/userinfo"})


def test_identity_is_stable_opaque_and_scoped_to_issuer() -> None:
    fingerprints = []
    for region in ("eu", "eu", "us"):
        issuer = "https://mcp" + region + ".coros.com"
        transport = Transport(identity_metadata(issuer), reply({"sub": "private-sub"}))
        access = CorosGateway(
            region, transport, now=lambda: 0, monotonic=lambda: 0, sleep=lambda _: None
        )
        result = access.probe_identity(TOKEN)
        fingerprints.append(result.fingerprint)
        assert "private-sub" not in repr(result)
        assert transport.requests[-1].get_header("Authorization") == "Bearer old-token"
    assert fingerprints[0] == fingerprints[1]
    assert fingerprints[0] != fingerprints[2]
    other = gateway(Transport(identity_metadata(), reply({"sub": "other-sub"})))
    assert other.probe_identity(TOKEN).fingerprint != fingerprints[0]


@pytest.mark.parametrize("subject", [None, "", "  ", 42, False, {}])
def test_identity_rejects_invalid_subject_without_profile_fallback(
    subject: object,
) -> None:
    transport = Transport(
        identity_metadata(), reply({"sub": subject, "name": "private-name"})
    )
    with pytest.raises(ConnectionFailure, match="field: sub") as error:
        gateway(transport).probe_identity(TOKEN)
    assert "private-name" not in str(error.value)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://mcpeu.coros.com/userinfo",
        "https://evil.example/userinfo",
        "https://mcpeu.coros.com@evil.example/userinfo",
        "https://mcpeu.coros.com/userinfo?private=1",
        "https://mcpeu.coros.com/userinfo#x",
        "https://[invalid/userinfo",
    ],
)
def test_identity_never_sends_token_to_untrusted_endpoint(endpoint: str) -> None:
    transport = Transport(
        reply({"issuer": "https://mcpeu.coros.com", "userinfo_endpoint": endpoint})
    )
    with pytest.raises(ConnectionFailure, match="Untrusted"):
        gateway(transport).probe_identity(TOKEN)
    assert len(transport.requests) == 1
    assert transport.requests[0].get_header("Authorization") is None


@pytest.mark.parametrize(
    "responses",
    [
        [reply({}, 503)],
        [identity_metadata("https://mcpus.coros.com")],
        [identity_metadata(), reply({"private": "secret"}, 401)],
        [identity_metadata(), reply({"private": "secret"}, 302)],
        [identity_metadata(), Response(200, {}, b"invalid secret")],
    ],
)
def test_identity_refuses_errors_without_retry_or_body_disclosure(
    responses: list[Response],
) -> None:
    transport = Transport(*responses)
    with pytest.raises(ConnectionFailure) as error:
        gateway(transport).probe_identity(TOKEN)
    assert "secret" not in str(error.value)
    assert len(transport.requests) == len(responses)


def test_identity_rotation_is_saved_before_userinfo() -> None:
    backend = Vault()
    store = VaultSessionStore("eu", backend)
    store.save(TOKEN)

    def userinfo(request: Request) -> Response:
        assert store.load() == NEW
        assert request.get_header("Authorization") == "Bearer new-token"
        return reply({"sub": "private-sub"})

    transport = Transport(token_reply(), identity_metadata(), userinfo)
    CorosConnection(gateway(transport), store, lambda: 1000).probe_identity()


@pytest.mark.parametrize("encoded", [False, True])
def test_date_observations_keep_missing_fields_with_their_activity(
    encoded: bool,
) -> None:
    text = (
        "Sport Records — 2020-11-08 to 2020-11-08 (2 records)\n"
        "1. Run\n   Time Window: endTimestamp=1604831800\n"
        "   LabelId: 1 | SportType: 100\n"
        "2. Run\n   Time Window: startTimestamp=1604830000 | endTimestamp=1604831800\n"
        "   LabelId: 2 | SportType: 100\n"
    )
    transport = Transport(
        rpc(1, {"protocolVersion": "2025-06-18"}),
        rpc(
            2,
            {
                "content": [
                    {"type": "text", "text": json.dumps(text) if encoded else text}
                ]
            },
        ),
    )
    result = gateway(transport).probe_activities(TOKEN, date(2020, 11, 8))
    first, second = result.dates
    assert first.start is None and first.problem
    assert second.start == datetime.fromtimestamp(1604830000, UTC)
    assert second.problem is None
    assert first.fingerprint != second.fingerprint
    assert "LabelId" not in repr(result.dates)


@pytest.mark.parametrize(
    "start,end",
    [
        ("1604830000000", "1604831800000"),
        ("invalid", "1604831800"),
        ("1604830000.5", "1604831800"),
        ("1604831800", "1604830000"),
        ("999999999999", "999999999999"),
    ],
)
def test_date_diagnostics_reject_invalid_or_reversed_seconds(
    start: str, end: str
) -> None:
    result = wrapped_activity()
    content = (
        json.dumps(result)
        .replace("1604830000", "START_VALUE")
        .replace("1604831800", end)
        .replace("START_VALUE", start)
    )
    transport = Transport(
        rpc(1, {"protocolVersion": "2025-06-18"}), rpc(2, json.loads(content))
    )
    observation = gateway(transport).probe_activities(TOKEN, date(2020, 11, 8)).dates[0]
    assert observation.problem


def configure_diagnostic_cli(
    monkeypatch: pytest.MonkeyPatch, transport: Transport
) -> Vault:
    backend = Vault()
    VaultSessionStore("eu", backend).save(
        Session("client", "access-secret", "refresh-secret", 10**12)
    )
    monkeypatch.setattr(coros_probe_cli, "system_backend", lambda: backend)
    monkeypatch.setattr(coros_probe_cli, "HttpsTransport", lambda: transport)
    return backend


def test_identity_cli_prints_only_fingerprint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    transport = Transport(
        identity_metadata(), reply({"sub": "private-sub", "name": "private-name"})
    )
    backend = configure_diagnostic_cli(monkeypatch, transport)
    before = dict(backend.values)
    assert coros_probe_cli.main(["identity"]) == 0
    output = capsys.readouterr()
    assert "COROS identity fingerprint:" in output.out
    assert all(
        value not in output.out + output.err
        for value in ("private-sub", "private-name", "access-secret", "refresh-secret")
    )
    assert backend.values == before
    assert len(transport.requests) == 2


@pytest.mark.parametrize(
    "zone,seconds,expected",
    [
        ("UTC", 0, "1970-01-01T00:00:00+00:00"),
        ("Europe/Paris", 1604789999, "2020-11-07T23:59:59+01:00"),
        ("Europe/Paris", 1604790000, "2020-11-08T00:00:00+01:00"),
        ("Europe/Paris", 1792888200, "2026-10-25T02:30:00+02:00"),
        ("Europe/Paris", 1792891800, "2026-10-25T02:30:00+01:00"),
        ("Europe/Paris", 1774744200, "2026-03-29T01:30:00+01:00"),
        ("Europe/Paris", 1774747800, "2026-03-29T03:30:00+02:00"),
        ("Pacific/Kiritimati", 1604830000, "2020-11-09T00:06:40+14:00"),
        ("Etc/GMT+12", 1604830000, "2020-11-07T22:06:40-12:00"),
    ],
)
def test_date_cli_formats_utc_and_frozen_local_zone(
    zone: str,
    seconds: int,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = json.loads(
        json.dumps(wrapped_activity())
        .replace("1604830000", str(seconds))
        .replace("1604831800", str(seconds + 1800))
    )
    transport = Transport(rpc(1, {"protocolVersion": "2025-06-18"}), rpc(2, result))
    backend = configure_diagnostic_cli(monkeypatch, transport)
    before = dict(backend.values)
    monkeypatch.setattr(coros_probe_cli, "_local_zone", lambda: ZoneInfo(zone))
    assert (
        coros_probe_cli.main(
            [
                "activities",
                "--on",
                "2020-11-08",
                "--through",
                "2020-11-09",
                "--inspect-dates",
            ]
        )
        == 0
    )
    output = capsys.readouterr()
    assert expected in output.out
    assert datetime.fromtimestamp(seconds, UTC).isoformat() in output.out
    assert "111222333444555666" not in output.out + output.err
    assert backend.values == before
    assert len(transport.requests) == 2


@pytest.mark.parametrize("module", [None, object(), ZoneInfo("Europe/Paris")])
def test_timezone_resolution_requires_zoneinfo(
    module: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = ModuleType("tzlocal")
    fake.get_localzone = lambda: module  # type: ignore[attr-defined]
    monkeypatch.setattr(local_time, "import_module", lambda _: fake)
    if isinstance(module, ZoneInfo):
        assert coros_probe_cli._local_zone() is module
    else:
        with pytest.raises(ConnectionFailure, match="Local timezone unavailable"):
            coros_probe_cli._local_zone()


def test_date_cli_fails_before_network_when_timezone_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(_: str) -> None:
        raise ImportError

    monkeypatch.setattr(local_time, "import_module", unavailable)
    assert (
        coros_probe_cli.main(["activities", "--on", "2020-11-08", "--inspect-dates"])
        == 1
    )


def test_date_cli_reports_incomplete_dates(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    transport = Transport(
        rpc(1, {"protocolVersion": "2025-06-18"}), rpc(2, wrapped_activity(2))
    )
    configure_diagnostic_cli(monkeypatch, transport)
    monkeypatch.setattr(coros_probe_cli, "_local_zone", lambda: ZoneInfo("UTC"))
    assert (
        coros_probe_cli.main(["activities", "--on", "2020-11-08", "--inspect-dates"])
        == 1
    )
    assert "Incomplete or invalid" in capsys.readouterr().err
    assert len(transport.requests) == 2


def test_timezone_warning_is_not_silently_replaced_by_utc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def ambiguous() -> ZoneInfo:
        warnings.warn("Timezone missing; assuming UTC", stacklevel=1)
        return ZoneInfo("UTC")

    fake = ModuleType("tzlocal")
    fake.get_localzone = ambiguous  # type: ignore[attr-defined]
    monkeypatch.setattr(local_time, "import_module", lambda _: fake)
    with pytest.raises(ConnectionFailure, match="Local timezone unavailable"):
        coros_probe_cli._local_zone()


@pytest.mark.parametrize("interrupt", [False, True])
def test_identity_cli_vault_failure_prevents_network(
    interrupt: bool, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    transport = Transport()
    backend = configure_diagnostic_cli(monkeypatch, transport)
    backend.available = False
    if interrupt:

        def stopped() -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(coros_probe_cli, "system_backend", stopped)
    assert coros_probe_cli.main(["identity"]) == (130 if interrupt else 1)
    assert not transport.requests
    output = capsys.readouterr()
    assert "private backend error" not in output.err


def test_date_cli_rejects_local_datetime_overflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = json.loads(
        json.dumps(wrapped_activity())
        .replace("1604830000", "253402300799")
        .replace("1604831800", "253402300799")
    )
    transport = Transport(rpc(1, {"protocolVersion": "2025-06-18"}), rpc(2, result))
    configure_diagnostic_cli(monkeypatch, transport)
    monkeypatch.setattr(
        coros_probe_cli, "_local_zone", lambda: ZoneInfo("Pacific/Kiritimati")
    )
    assert (
        coros_probe_cli.main(["activities", "--on", "2020-11-08", "--inspect-dates"])
        == 1
    )


def test_invalid_activity_day_is_rejected_before_vault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        coros_probe_cli, "system_backend", lambda: pytest.fail("Vault accessed")
    )
    with pytest.raises(SystemExit) as error:
        coros_probe_cli.main(["activities", "--on", "2026-02-30"])
    assert error.value.code == 2


def test_activity_probe_sends_explicit_range_and_limit() -> None:
    transport = Transport(
        rpc(1, {"protocolVersion": "2025-06-18"}),
        rpc(2, {"structuredContent": {"records": [{}]}}),
    )
    result = gateway(transport).probe_activities(
        TOKEN, date(2020, 11, 8), through=date(2020, 11, 15), limit=1
    )
    assert result.count == 1
    assert json.loads(transport.requests[-1].data or b"")["params"]["arguments"] == {
        "startDate": "20201108",
        "endDate": "20201115",
        "limit": 1,
    }


def test_reversed_activity_period_fails_before_vault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        coros_probe_cli, "system_backend", lambda: pytest.fail("Vault accessed")
    )
    with pytest.raises(SystemExit) as error:
        coros_probe_cli.main(
            ["activities", "--on", "2020-11-15", "--through", "2020-11-08"]
        )
    assert error.value.code == 2
