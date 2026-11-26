import json
import sys
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import BytesIO, StringIO
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request

import pytest

from fit_to_md import cli, coros_cli
from fit_to_md.application.coros_connection import CorosSessionManager
from fit_to_md.application.synchronization import to_account_identity
from fit_to_md.domain.coros_connection import (
    ISSUERS,
    ConnectionFailure,
    ConnectionRecord,
    CorosAccount,
    LocalSessionState,
    ReauthorizationRequired,
    Session,
    Verification,
)
from fit_to_md.domain.synchronization.model import ActivityIdentity
from fit_to_md.domain.synchronization.quota import ServerFitQuotaExceeded
from fit_to_md.infrastructure import coros_gateway
from fit_to_md.infrastructure.coros_gateway import (
    CorosGateway,
    HttpsTransport,
    Response,
)
from fit_to_md.infrastructure.coros_vault import (
    VaultConnectionStore,
    VaultQuotaBindings,
    VaultSessionStore,
)
from tests.support.coros import FakeTransport, FakeVault, reply

EU_ACCOUNT = CorosAccount("eu", ISSUERS["eu"], " opaque-subject-é ")
US_ACCOUNT = CorosAccount("us", ISSUERS["us"], EU_ACCOUNT.subject)
ORIGINAL = ConnectionRecord(
    EU_ACCOUNT, Session("a-client", "old-token", "old-refresh", 1000)
)
PRODUCTION_SLOT = ("fit-to-md/coros/connection", "session")


def token_reply() -> Response:
    return reply(
        {
            "token_type": "Bearer",
            "access_token": "new-token",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
        }
    )


def discovery(region: str = "eu") -> Response:
    issuer = ISSUERS[region]
    return reply({"issuer": issuer, "userinfo_endpoint": issuer + "/connect/userinfo"})


def callback(request: Request) -> Response:
    query = parse_qs(urlsplit(request.full_url).query)
    assert query["code_challenge_method"] == ["S256"]
    location = "http://127.0.0.1:43123/callback?" + urlencode(
        {"code": "private-code", "state": query["state"][0]}
    )
    return Response(303, {"Location": location}, b"")


def login_transport(
    region: str, userinfo: Response | Callable[[Request], Response]
) -> FakeTransport:
    return FakeTransport(
        reply({"client_id": "registered-client"}, 201),
        reply(
            {
                "sessionId": "private-session",
                "pollToken": "private-poll",
                "loginUrl": ISSUERS[region] + "/login?private-url",
            },
            201,
        ),
        reply({"status": "authorized", "loginTicket": "private-ticket"}),
        callback,
        token_reply(),
        discovery(region),
        userinfo,
    )


class NoContention:
    hold = staticmethod(nullcontext)


def manager(
    backend: FakeVault, transport: FakeTransport, now: float = 100
) -> CorosSessionManager:
    def access(region: str) -> CorosGateway:
        return CorosGateway(
            region,
            transport,
            now=lambda: now,
            monotonic=lambda: 0,
            sleep=lambda _: None,
        )

    return CorosSessionManager(
        access, VaultConnectionStore(backend), NoContention(), lambda: now
    )


@pytest.mark.parametrize("region", [None, "eu", "us", "cn"])
def test_login_confirms_account_and_can_resume_in_another_instance(
    region: str | None,
) -> None:
    backend = FakeVault()
    selected = region or "eu"

    def userinfo(request: Request) -> Response:
        assert not backend.values
        assert request.get_header("Authorization") == "Bearer new-token"
        return reply({"sub": EU_ACCOUNT.subject, "nickname": "private-nickname"})

    transport = login_transport(selected, userinfo)
    opened: list[str] = []
    account = manager(backend, transport).login(opened.append, region=region)
    assert account == CorosAccount(selected, ISSUERS[selected], EU_ACCOUNT.subject)
    assert len(opened) == 1 and opened[0].startswith(ISSUERS[selected])
    assert all(
        urlsplit(request.full_url).netloc == urlsplit(ISSUERS[selected]).netloc
        for request in transport.requests
    )
    saved = backend.values[PRODUCTION_SLOT]
    assert json.loads(saved)["version"] == 1
    assert "private-nickname" not in saved and "private-ticket" not in saved
    fresh = FakeTransport()
    identity, session = manager(backend, fresh).resume()
    assert identity == account and session.access_token == "new-token"
    sync_identity = to_account_identity(account)
    assert (sync_identity.provider, sync_identity.issuer, sync_identity.subject) == (
        "coros",
        ISSUERS[selected],
        EU_ACCOUNT.subject,
    )
    assert EU_ACCOUNT.subject not in repr(account) and EU_ACCOUNT.subject not in repr(
        sync_identity
    )
    assert "new-token" not in repr(session) and "new-refresh" not in repr(session)
    assert not fresh.requests


def test_login_reuses_saved_region_and_rejects_region_change_before_network() -> None:
    backend = FakeVault()
    store = VaultConnectionStore(backend)
    store.save(replace(ORIGINAL, account=US_ACCOUNT))
    transport = login_transport("us", reply({"sub": US_ACCOUNT.subject}))
    connection = manager(backend, transport)

    with pytest.raises(ConnectionFailure, match="coros logout"):
        connection.login(lambda _: pytest.fail("browser opened"), region="eu")
    assert not transport.requests
    assert store.load() == replace(ORIGINAL, account=US_ACCOUNT)
    assert connection.login(lambda _: None) == US_ACCOUNT
    assert store.load() != replace(ORIGINAL, account=US_ACCOUNT)


def test_different_subject_is_rejected_until_logout_and_new_identity_is_distinct() -> (
    None
):
    backend = FakeVault()
    store = VaultConnectionStore(backend)
    store.save(ORIGINAL)
    userinfo = reply({"sub": "different-account"})
    connection = manager(backend, login_transport("eu", userinfo))
    with pytest.raises(ConnectionFailure, match="coros logout") as error:
        connection.login(lambda _: None)
    assert "different-account" not in str(error.value)
    assert store.load() == ORIGINAL
    connection.logout()
    account = manager(backend, login_transport("eu", userinfo)).login(lambda _: None)
    assert to_account_identity(account) != to_account_identity(EU_ACCOUNT)
    assert to_account_identity(US_ACCOUNT) != to_account_identity(EU_ACCOUNT)


@pytest.mark.parametrize(
    "userinfo",
    [reply({"name": "private-name"}), reply({"detail": "private-name"}, 401)],
)
def test_login_identity_failure_preserves_existing_session(userinfo: Response) -> None:
    backend = FakeVault()
    store = VaultConnectionStore(backend)
    store.save(ORIGINAL)
    with pytest.raises(ConnectionFailure) as error:
        manager(backend, login_transport("eu", userinfo)).login(lambda _: None)
    assert "private-name" not in str(error.value)
    assert store.load() == ORIGINAL


def test_unavailable_vault_stops_login_before_browser_and_network() -> None:
    backend = FakeVault()
    backend.available = False
    transport = FakeTransport()
    with pytest.raises(ConnectionFailure, match="Could not read the system vault"):
        manager(backend, transport).login(lambda _: pytest.fail("browser opened"))
    assert not transport.requests


def test_contention_blocks_session_mutations_but_allows_local_status() -> None:
    class BusyLock:
        def hold(self) -> None:
            raise ConnectionFailure("Session lock busy.")

    backend = FakeVault()
    store = VaultConnectionStore(backend)
    store.save(ORIGINAL)
    backend.available = False
    connection = CorosSessionManager(
        lambda _: pytest.fail("network adapter created"),
        store,
        BusyLock(),
        lambda: 1000,
    )
    for operation in (
        lambda: connection.login(lambda _: pytest.fail("browser opened")),
        connection.resume,
        connection.logout,
    ):
        with pytest.raises(ConnectionFailure, match="Session lock busy"):
            operation()
    backend.available = True
    assert connection.status().state is LocalSessionState.RENEWAL_NEEDED
    assert store.load() == ORIGINAL


def test_rotation_is_saved_before_userinfo_and_confirmed_before_use() -> None:
    backend = FakeVault()
    store = VaultConnectionStore(backend)
    store.save(ORIGINAL)

    def check_rotation(request: Request) -> Response:
        assert parse_qs(request.data.decode()) == {
            "client_id": ["a-client"],
            "grant_type": ["refresh_token"],
            "refresh_token": ["old-refresh"],
        }
        assert store.load() == ORIGINAL
        return token_reply()

    def check_pending(request: Request) -> Response:
        pending = store.load()
        assert pending is not None and pending.verification is Verification.PENDING
        assert pending.session.refresh_token == "new-refresh"
        assert request.get_header("Authorization") == "Bearer new-token"
        return reply({"sub": EU_ACCOUNT.subject})

    connection = manager(
        backend, FakeTransport(check_rotation, discovery(), check_pending), now=940
    )
    account, tokens = connection.resume()
    assert store.load() == ConnectionRecord(account, tokens)
    assert account == EU_ACCOUNT and tokens.refresh_token == "new-refresh"


@pytest.mark.parametrize("interruption", [True, False])
def test_pending_verification_can_resume_without_second_refresh(
    interruption: bool,
) -> None:
    backend = FakeVault()
    store = VaultConnectionStore(backend)
    store.save(ORIGINAL)

    def fail(_request: Request) -> Response:
        if interruption:
            raise KeyboardInterrupt
        return reply({"private": "error"}, 503)

    connection = manager(
        backend, FakeTransport(token_reply(), discovery(), fail), now=1000
    )
    expected = KeyboardInterrupt if interruption else ConnectionFailure
    with pytest.raises(expected):
        connection.resume()
    assert connection.status().state is LocalSessionState.VERIFICATION_PENDING
    next_process = FakeTransport(discovery(), reply({"sub": EU_ACCOUNT.subject}))
    account, session = manager(backend, next_process, now=1000).resume()
    assert account == EU_ACCOUNT and session.refresh_token == "new-refresh"
    assert len(next_process.requests) == 2
    assert store.load() == ConnectionRecord(account, session)


def test_failed_rotation_write_stops_before_any_use_of_rotated_token() -> None:
    backend = FakeVault()
    store = VaultConnectionStore(backend)
    store.save(ORIGINAL)
    backend.writable = False
    transport = FakeTransport(token_reply())
    with pytest.raises(ConnectionFailure, match="Could not save") as error:
        manager(backend, transport, now=1000).resume()
    assert "private write error" not in str(error.value)
    assert len(transport.requests) == 1 and store.load() == ORIGINAL


@pytest.mark.parametrize(
    "failure", ["revoked-grant", "revoked-access", "different-account"]
)
def test_revocation_and_subject_mismatch_block_next_calls(failure: str) -> None:
    backend = FakeVault()
    store = VaultConnectionStore(backend)
    store.save(ORIGINAL)
    if failure == "revoked-grant":
        transport = FakeTransport(
            reply({"error": "invalid_grant", "error_description": "secret-detail"}, 400)
        )
    else:
        userinfo = (
            reply({"detail": "secret-detail"}, 401)
            if failure == "revoked-access"
            else reply({"sub": "secret-detail"})
        )
        transport = FakeTransport(token_reply(), discovery(), userinfo)

    with pytest.raises(ReauthorizationRequired, match="fit-to-md coros login") as error:
        manager(backend, transport, now=1000).resume()
    assert "secret-detail" not in str(error.value)
    saved = store.load()
    assert saved is not None and saved.account == EU_ACCOUNT
    assert saved.verification is Verification.RECONNECT
    empty = FakeTransport()
    connection = manager(backend, empty, now=1000)
    assert connection.status().state is LocalSessionState.RECONNECT
    with pytest.raises(ReauthorizationRequired, match="fit-to-md coros login"):
        connection.resume()
    assert not empty.requests


def test_transient_token_failure_keeps_previous_session_and_does_not_retry() -> None:
    backend = FakeVault()
    store = VaultConnectionStore(backend)
    store.save(ORIGINAL)
    transport = FakeTransport(
        reply({"error": "temporarily_unavailable", "error_description": "secret"}, 503)
    )
    with pytest.raises(ConnectionFailure, match="HTTP 503") as error:
        manager(backend, transport, now=1000).resume()
    assert not isinstance(error.value, ReauthorizationRequired)
    assert "secret" not in str(error.value)
    assert len(transport.requests) == 1 and store.load() == ORIGINAL


def test_status_and_logout_are_local_and_keep_other_entries() -> None:
    backend = FakeVault()
    prototype = VaultSessionStore("eu", backend)
    prototype.save(ORIGINAL.session)
    quota_slot = ("fit-to-md/coros/quota", "subject")
    backend.values[quota_slot] = "quota-metadata"
    transport = FakeTransport()
    connection = manager(backend, transport, now=1000)
    assert connection.status().state is LocalSessionState.DISCONNECTED
    with pytest.raises(ReauthorizationRequired, match="fit-to-md coros login"):
        connection.resume()
    store = VaultConnectionStore(backend)
    store.save(ORIGINAL)
    assert connection.status().state is LocalSessionState.RENEWAL_NEEDED
    assert connection.status().region == "eu"
    assert store.load() == ORIGINAL
    connection.logout()
    connection.logout()
    assert store.load() is None and prototype.load() == ORIGINAL.session
    assert backend.values[quota_slot] == "quota-metadata" and not transport.requests


@pytest.mark.parametrize(
    "corruption",
    [
        {"version": True},
        {"version": 2},
        {"verification": "private-value"},
        {
            "account": {
                "region": "us",
                "issuer": ISSUERS["eu"],
                "subject": "private-value",
            }
        },
        {"account": {"region": "eu", "issuer": ISSUERS["eu"], "subject": " "}},
        {
            "session": {
                "client_id": "valid",
                "access_token": "invalid access",
                "refresh_token": "private-value",
                "expires_at": 1000,
            }
        },
    ],
)
def test_invalid_vault_record_fails_closed_but_can_be_removed(
    corruption: dict[str, object],
) -> None:
    backend = FakeVault()
    store = VaultConnectionStore(backend)
    store.save(ORIGINAL)
    value = json.dumps({**json.loads(backend.values[PRODUCTION_SLOT]), **corruption})
    backend.values[PRODUCTION_SLOT] = value
    with pytest.raises(
        ConnectionFailure, match="Invalid or unsupported COROS session"
    ) as error:
        store.load()
    assert "private-value" not in str(error.value)
    assert backend.values[PRODUCTION_SLOT] == value
    store.clear()
    assert store.load() is None


def test_cli_dispatches_login_status_logout_to_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = FakeVault()
    transport = login_transport("us", reply({"sub": US_ACCOUNT.subject}))
    browser_urls: list[str] = []

    def open_browser(url: str) -> bool:
        browser_urls.append(url)
        return True

    def system_backend(*, extra: str) -> FakeVault:
        assert extra == "coros"
        return backend

    monkeypatch.setattr(coros_cli, "system_backend", system_backend)
    monkeypatch.setattr(coros_cli, "HttpsTransport", lambda: transport)
    monkeypatch.setattr(coros_cli, "SessionFileLock", NoContention)
    monkeypatch.setattr(coros_cli.webbrowser, "open", open_browser)

    def run(*argv: str) -> tuple[int, str, str]:
        stdout, stderr = StringIO(), StringIO()
        code = cli.run(["coros", *argv], stdout=stdout, stderr=stderr)
        return code, stdout.getvalue(), stderr.getvalue()

    assert run("status") == (0, "COROS local status: No saved session.\n", "")
    assert not transport.requests and not browser_urls
    code, stdout, stderr = run("login", "--region", "us")
    assert code == 0 and "account confirmed (region: us)" in stdout
    assert stderr == "Authorize FitToMd in your browser.\n"
    assert len(browser_urls) == 1
    registration = transport.requests[0]
    assert json.loads(registration.data)["client_name"] == "FitToMd"
    assert run("status") == (
        0,
        "COROS local status: Saved session available (region: us).\n",
        "",
    )
    assert run("logout") == (0, "COROS local session removed.\n", "")
    assert run("status") == (0, "COROS local status: No saved session.\n", "")
    assert len(transport.requests) == 7 and len(browser_urls) == 1


def test_cli_redacts_a_browser_failure_and_stops_before_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = FakeVault()
    transport = login_transport("eu", reply({"sub": EU_ACCOUNT.subject}))

    def fail(url: str) -> bool:
        raise OSError(url)

    monkeypatch.setattr(coros_cli, "system_backend", lambda **_: backend)
    monkeypatch.setattr(coros_cli, "HttpsTransport", lambda: transport)
    monkeypatch.setattr(coros_cli, "SessionFileLock", NoContention)
    monkeypatch.setattr(coros_cli.webbrowser, "open", fail)
    stdout, stderr = StringIO(), StringIO()
    assert cli.run(["coros", "login"], stdout=stdout, stderr=stderr) == 1
    assert not stdout.getvalue() and "Could not open the browser" in stderr.getvalue()
    assert "private-url" not in stderr.getvalue()
    assert not backend.values and len(transport.requests) == 2


@pytest.mark.skipif(sys.platform not in {"linux", "win32"}, reason="COROS platforms")
def test_cli_initializes_quota_explicitly_with_silent_renewal_and_keeps_it_at_logout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = FakeVault()
    transport = FakeTransport(
        token_reply(), discovery(), reply({"sub": EU_ACCOUNT.subject})
    )
    monkeypatch.setattr(coros_cli, "system_backend", lambda **_: backend)
    monkeypatch.setattr(coros_cli, "HttpsTransport", lambda: transport)
    monkeypatch.setattr(coros_cli, "SessionFileLock", NoContention)
    monkeypatch.setattr(coros_cli.time, "time", lambda: 1000)
    monkeypatch.setattr(
        coros_cli.webbrowser, "open", lambda *_: pytest.fail("Browser opened")
    )
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    def run(command: str) -> tuple[int, str, str]:
        stdout, stderr = StringIO(), StringIO()
        status = cli.run(["coros", command], stdout=stdout, stderr=stderr)
        return status, stdout.getvalue(), stderr.getvalue()

    code, stdout, stderr = run("init-quota")
    assert code == 1 and not stdout and "coros login" in stderr
    assert not list(tmp_path.iterdir()) and not transport.requests
    VaultConnectionStore(backend).save(ORIGINAL)
    assert run("init-quota") == (0, "COROS quota registry is ready.\n", "")
    bindings = VaultQuotaBindings(backend)
    account = to_account_identity(EU_ACCOUNT)
    binding = bindings.load(account)
    assert binding is not None and binding.active and binding.path.is_file()
    original_db = binding.path.read_bytes()
    assert run("init-quota")[0] == 0
    assert len(transport.requests) == 3

    binding.path.unlink()
    code, stdout, stderr = run("init-quota")
    assert code == 1 and not stdout and "Restore reliable quota state" in stderr
    assert not binding.path.exists()
    binding.path.write_bytes(original_db)
    assert run("logout")[0] == 0
    assert not VaultConnectionStore(backend).load()
    assert (
        bindings.load(account) == binding and binding.path.read_bytes() == original_db
    )


@pytest.mark.parametrize(
    "header, seconds",
    [
        ({"rEtRy-AfTeR": "60"}, 60),
        ({"Retry-After": "Thu, 01 Jan 1970 00:02:00 GMT"}, 20),
        ({"Retry-After": "Thu, 01 Jan 1970 00:00:00 GMT"}, 0),
        ({"Retry-After": "invalid"}, None),
        ({"Retry-After": "99999999999999999999"}, None),
        ({}, None),
    ],
)
def test_fit_gateway_surfaces_server_quota_and_optional_retry_at(
    header: dict[str, str], seconds: int | None
) -> None:
    transport = FakeTransport(
        reply({"id": 1, "result": {"protocolVersion": "2025-06-18"}}),
        Response(429, header, b"untrusted provider detail"),
    )
    gateway = CorosGateway(
        "eu",
        transport,
        now=lambda: 100,
        monotonic=lambda: 0,
        sleep=lambda _: pytest.fail("Unexpected wait"),
    )
    request = gateway.prepare_fit_download(EU_ACCOUNT, ORIGINAL.session)
    with pytest.raises(ServerFitQuotaExceeded) as error:
        request(ActivityIdentity(to_account_identity(EU_ACCOUNT), "label", 100))
    expected = (
        None
        if seconds is None
        else datetime.fromtimestamp(100, UTC) + timedelta(seconds=seconds)
    )
    assert error.value.retry_at == expected
    assert "untrusted" not in str(error.value)
    assert len(transport.requests) == 2
    assert json.loads(transport.requests[1].data)["params"] == {
        "name": "downloadActivityFitFiles",
        "arguments": {"labelId": "label", "sportType": 100},
    }


def test_mcp_initialization_429_is_not_a_fit_quota_refusal() -> None:
    transport = FakeTransport(Response(429, {"Retry-After": "1"}, b""))
    gateway = CorosGateway(
        "eu", transport, now=lambda: 100, monotonic=lambda: 0, sleep=lambda _: None
    )
    with pytest.raises(ConnectionFailure, match="initialize failed"):
        gateway.prepare_fit_download(EU_ACCOUNT, ORIGINAL.session)
    assert len(transport.requests) == 1


def test_prepared_fit_download_checks_the_confirmed_account() -> None:
    transport = FakeTransport(
        reply({"id": 1, "result": {"protocolVersion": "2025-06-18"}})
    )
    gateway = CorosGateway(
        "eu", transport, now=lambda: 0, monotonic=lambda: 0, sleep=lambda _: None
    )
    with pytest.raises(ConnectionFailure, match="account does not match"):
        gateway.prepare_fit_download(US_ACCOUNT, ORIGINAL.session)
    assert not transport.requests
    request = gateway.prepare_fit_download(EU_ACCOUNT, ORIGINAL.session)
    other = replace(EU_ACCOUNT, subject="other")
    with pytest.raises(ConnectionFailure, match="activity does not match"):
        request(ActivityIdentity(to_account_identity(other), "label", 100))
    assert len(transport.requests) == 1


def test_https_transport_preserves_rate_limit_headers_without_reading_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnreadableBody(BytesIO):
        def read(self, size: int = -1) -> bytes:
            pytest.fail("429 body read")

    url = ISSUERS["eu"] + "/mcp"

    def open_request(request: Request, *, timeout: int) -> None:
        assert request.full_url == url and timeout == 10
        raise HTTPError(url, 429, "Limited", {"Retry-After": "42"}, UnreadableBody())

    monkeypatch.setattr(
        coros_gateway, "build_opener", lambda *_: SimpleNamespace(open=open_request)
    )
    assert HttpsTransport().send(Request(url)) == Response(
        429, {"Retry-After": "42"}, b""
    )


@pytest.mark.parametrize(
    "failure,code",
    [(ConnectionFailure("System vault unavailable."), 1), (KeyboardInterrupt(), 130)],
)
def test_cli_reports_operational_errors_and_interrupts_on_stderr(
    monkeypatch: pytest.MonkeyPatch, failure: BaseException, code: int
) -> None:
    def fail() -> CorosSessionManager:
        raise failure

    monkeypatch.setattr(coros_cli, "_build_session_manager", fail)
    stdout, stderr = StringIO(), StringIO()
    assert cli.run(["coros", "status"], stdout=stdout, stderr=stderr) == code
    assert not stdout.getvalue() and stderr.getvalue()


@pytest.mark.parametrize("argv", [["login", "--region", "bad"], [], ["sync"]])
def test_cli_validates_arguments_before_assembling_adapters(
    monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    monkeypatch.setattr(
        coros_cli,
        "_build_session_manager",
        lambda: pytest.fail("production adapters loaded"),
    )
    with pytest.raises(SystemExit) as error:
        cli.run(["coros", *argv])
    assert error.value.code == 2
