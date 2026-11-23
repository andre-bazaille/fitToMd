import base64
import json
import struct
import subprocess
import sys
from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from io import BytesIO, StringIO
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError
from urllib.request import Request
from urllib.response import addinfourl
from zoneinfo import ZoneInfo

import pytest
from fitdecode.utils import compute_crc

from fit_to_md import cli, coros_cli
from fit_to_md.application.synchronization import to_account_identity
from fit_to_md.application.synchronization_quota import AccountQuotas, QuotaUnavailable
from fit_to_md.application.synchronization_storage import (
    ConversionOptions,
    FailureReason,
    RegistryEntry,
    RegistrySnapshot,
    Stage,
    StorageFailure,
)
from fit_to_md.application.use_cases.generate_markdown_report import (
    GeneratedMarkdownReport,
    GenerateMarkdownReport,
)
from fit_to_md.domain.activity.ports import UnsupportedActivityError
from fit_to_md.domain.coros_connection import (
    ISSUERS,
    ConnectionRecord,
    CorosAccount,
    Session,
)
from fit_to_md.domain.reporting.ports import ProviderDiagnostic, ProviderDiagnosticKind
from fit_to_md.domain.synchronization.model import (
    AcquisitionState,
    ActivityIdentity,
    ConversionState,
    Reference,
)
from fit_to_md.domain.synchronization.quota import DailyCalls
from fit_to_md.infrastructure import coros_gateway
from fit_to_md.infrastructure.coros_gateway import Response
from fit_to_md.infrastructure.coros_vault import (
    VaultConnectionStore,
    VaultQuotaBindings,
)
from fit_to_md.infrastructure.fitdecode.reader import FitdecodeActivityReader
from fit_to_md.infrastructure.local_time import SystemLocalTimezone
from fit_to_md.infrastructure.synchronization import files
from fit_to_md.infrastructure.synchronization.output import SqliteOutput
from fit_to_md.infrastructure.synchronization.quota import (
    SqliteQuotaRegistry,
    SqliteQuotaStorage,
)
from fit_to_md.infrastructure.synchronization.registry import SqliteRegistry
from tests.support.coros import (
    FakeTransport,
    FakeVault,
    catalogue_reply,
    reply,
    rpc_reply,
)

NOW = datetime(2026, 11, 21, 12, tzinfo=UTC)
ZONE = ZoneInfo("Europe/Paris")
FIRST = NOW - timedelta(hours=2)
SECOND = FIRST + timedelta(minutes=1)
THIRD = SECOND + timedelta(minutes=1)
COROS_ACCOUNT = CorosAccount("eu", ISSUERS["eu"], "private-subject")
ACCOUNT = to_account_identity(COROS_ACCOUNT)
INIT = rpc_reply(1, {"protocolVersion": "2025-06-18"})
FIT = (
    Path(__file__).resolve().parents[2] / "fit_files" / "coros_synthetic.fit"
).read_bytes()
START = date(2026, 11, 19)
END = date(2026, 11, 22)


type CorosFixture = tuple[FakeVault, dict[str, FakeTransport], AccountQuotas, Path]


class Uncontended:
    hold = staticmethod(nullcontext)


@pytest.fixture
def coros(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CorosFixture:
    pytest.importorskip("portalocker")
    backend = FakeVault()
    state = {"transport": FakeTransport()}
    monkeypatch.setattr(coros_cli, "system_backend", lambda **_: backend)
    monkeypatch.setattr(coros_cli, "SessionFileLock", Uncontended)
    monkeypatch.setattr(coros_cli, "HttpsTransport", lambda: state["transport"])
    monkeypatch.setattr(coros_cli._UtcClock, "now", lambda _: NOW)
    monkeypatch.setattr(coros_cli.time, "time", NOW.timestamp)
    monkeypatch.setattr(coros_cli.time, "sleep", lambda _: None)
    monkeypatch.setattr(SystemLocalTimezone, "resolve", lambda _: ZONE)
    monkeypatch.setattr(
        SqliteQuotaStorage, "initial_path", lambda *_: tmp_path / "quota.db"
    )
    monkeypatch.setattr(
        coros_cli.webbrowser, "open", lambda *_: pytest.fail("unexpected browser")
    )
    VaultConnectionStore(backend).save(
        ConnectionRecord(
            COROS_ACCOUNT,
            Session(
                "client", "private-token", "private-refresh", NOW.timestamp() + 3600
            ),
        )
    )
    quotas = AccountQuotas(
        VaultQuotaBindings(backend),
        SqliteQuotaStorage(coros_cli._UtcClock()),
        nullcontext,
    )
    output = tmp_path / "output"
    output.mkdir()
    return backend, state, quotas, output


def sync(output: Path, *args: str) -> tuple[int, str, str]:
    return run_sync(output, "--download-only", *(args or ("--last-days", "1")))


def run_sync(output: Path, *args: str) -> tuple[int, str, str]:
    return run_coros("sync", "--output", str(output), *args)


def run_coros(*args: str) -> tuple[int, str, str]:
    out, err = StringIO(), StringIO()
    code = cli.run(
        ["coros", *args],
        stdout=out,
        stderr=err,
    )
    return code, out.getvalue(), err.getvalue()


def catalogue(
    *rows: tuple[str, int, datetime], start: date = START, end: date = END
) -> Response:
    return catalogue_reply(
        start,
        end,
        [
            (
                label,
                sport,
                f"startTimestamp={int(at.timestamp())} | endTimestamp={int(at.timestamp()) + 3600}",
            )
            for label, sport, at in rows
        ],
    )


def fit_reply(request_id: int, data: bytes = FIT) -> Response:
    return rpc_reply(
        request_id,
        {
            "content": [
                {
                    "type": "resource",
                    "resource": {
                        "blob": base64.b64encode(data).decode(),
                        "uri": "https://private.example/access",
                    },
                },
            ]
        },
    )


def fit_with_body(body: bytes) -> bytes:
    contents = struct.pack("<BBHI4s", 12, 16, 0, len(body), b".FIT") + body
    return contents + struct.pack("<H", compute_crc(contents))


def payloads(transport: FakeTransport) -> list[dict]:
    return [
        json.loads(request.data or b"")
        for request in transport.requests
        if request.full_url.endswith("/mcp")
    ]


def snapshot(output: Path) -> RegistrySnapshot:
    with SqliteOutput(output).read(ACCOUNT) as reader:
        assert reader is not None
        return reader.snapshot()


def entries(output: Path) -> dict[str, RegistryEntry]:
    return {
        entry.tracked.activity.identity.remote_id: entry
        for entry in snapshot(output).entries
    }


def test_download_persists_candidates_before_request_and_reuses_fit(
    coros: CorosFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, state, quotas, output = coros
    quotas.initialize(ACCOUNT)
    original = SqliteRegistry.record_attempt

    def verify_attempt(
        registry: SqliteRegistry, identity: ActivityIdentity, stage: Stage, at: datetime
    ) -> None:
        checkpoint = registry.snapshot()
        assert len(checkpoint.entries) == 2
        assert checkpoint.reference == Reference(NOW)
        expected_calls = {"10": 1, "20": 2}
        assert (
            quotas.open(ACCOUNT).daily_calls(NOW.date(), ZONE).fit
            == expected_calls[identity.remote_id]
        )
        original(registry, identity, stage, at)

    monkeypatch.setattr(SqliteRegistry, "record_attempt", verify_attempt)
    transport = FakeTransport(
        INIT,
        catalogue(("20", 102, SECOND), ("10", 100, FIRST), ("30", 100, NOW)),
        fit_reply(3),
        fit_reply(4),
    )
    state["transport"] = transport
    code, out, err = sync(output, "--since", "2026-11-21")
    assert code == 0
    assert "Catalogue coverage: complete" in out and "Selected: 2" in out
    assert "FITs downloaded: 2" in out and "Europe/Paris" in out
    assert "sport 100" in err and err.index("sport 100") < err.index("sport 102")
    assert "private" not in out + err
    assert [call["params"]["arguments"] for call in payloads(transport)[2:]] == [
        {"labelId": "10", "sportType": 100},
        {"labelId": "20", "sportType": 102},
    ]
    acquired = entries(output)
    assert all(
        entry.tracked.acquisition is AcquisitionState.ACQUIRED
        for entry in acquired.values()
    )
    assert all(entry.acquisition_attempts.count == 1 for entry in acquired.values())
    assert all(
        (output / entry.paths.fit).read_bytes() == FIT for entry in acquired.values()
    )
    assert not list(output.glob("*.md")) and snapshot(output).reference == Reference(
        NOW
    )
    assert snapshot(output).last_complete_discovery == NOW

    monkeypatch.setattr(SqliteRegistry, "record_attempt", original)
    state["transport"] = FakeTransport(
        INIT, catalogue(("10", 100, FIRST), ("20", 102, SECOND))
    )
    code, out, err = sync(output)
    assert code == 0 and "FITs already available: 2" in out and not err
    assert entries(output) == acquired
    state["transport"] = FakeTransport(
        INIT,
        catalogue(("30", 100, THIRD), ("20", 102, SECOND), ("10", 100, FIRST)),
        fit_reply(3),
    )
    code, out, _ = sync(output)
    assert (
        code == 0 and "FITs downloaded: 1" in out and "FITs already available: 2" in out
    )
    assert payloads(state["transport"])[-1]["params"]["arguments"]["labelId"] == "30"
    assert quotas.open(ACCOUNT).daily_calls(NOW.date(), ZONE) == DailyCalls(
        catalogue=3, fit=3
    )


def test_partial_catalogue_persists_dated_candidates_without_fits(
    coros: CorosFixture,
) -> None:
    _, state, quotas, output = coros
    quotas.initialize(ACCOUNT)
    transport = FakeTransport(
        INIT,
        Response(503, {}, b"private"),
        catalogue_reply(
            START,
            END,
            [
                (
                    "10",
                    100,
                    f"startTimestamp={int(FIRST.timestamp())} | endTimestamp={int(SECOND.timestamp())}",
                ),
                ("20", 102, "startTimestamp=unknown"),
            ],
        ),
    )
    state["transport"] = transport
    code, out, err = sync(output)
    assert (
        code == 1 and "Catalogue coverage: incomplete" in out and "Selected: 1" in out
    )
    assert "timestamps: 1" in err and "private" not in err
    assert set(entries(output)) == {"10"}
    assert entries(output)["10"].paths is None
    assert snapshot(output).last_complete_discovery is None
    assert snapshot(output).reference == Reference(
        datetime(2026, 11, 20, 23, tzinfo=UTC)
    )
    assert not (output / "fits").exists()
    assert quotas.open(ACCOUNT).daily_calls(NOW.date(), ZONE) == DailyCalls(catalogue=2)


@pytest.mark.parametrize("final_attempt", [False, True])
def test_retry_after_stops_subdivided_catalogue_and_keeps_verified_candidates(
    coros: CorosFixture, monkeypatch: pytest.MonkeyPatch, final_attempt: bool
) -> None:
    _, state, quotas, output = coros
    quotas.initialize(ACCOUNT)
    split = START + timedelta(days=1)
    right = split + timedelta(days=1)
    selected = datetime(2026, 11, 20, 23, 30, tzinfo=UTC)
    saturated = tuple((str(100 + i), 100, FIRST) for i in range(20))
    retries = (Response(503, {}, b""),) * 2 if final_attempt else ()
    delay = 1 if final_attempt else 3600
    transport = FakeTransport(
        INIT,
        catalogue(*saturated),
        catalogue(("10", 100, selected), start=START, end=split),
        catalogue(*saturated, start=right, end=END),
        *retries,
        Response(429, {"Retry-After": str(delay)}, b"private"),
    )
    state["transport"] = transport
    pauses: list[float] = []
    monkeypatch.setattr(coros_cli.time, "sleep", pauses.append)

    code, out, err = sync(output)

    assert (
        code == 1 and "Catalogue coverage: incomplete" in out and "Selected: 1" in out
    )
    assert "HTTP 429" in err and "private" not in err
    assert f"Retry after {(NOW + timedelta(seconds=delay)).isoformat()}." in err
    assert pauses == ([1, 2] if final_attempt else [])
    calls = [item["params"]["arguments"] for item in payloads(transport)[1:]]
    assert [(call["startDate"], call["endDate"]) for call in calls] == [
        ("20261119", "20261122"),
        ("20261119", "20261120"),
        ("20261121", "20261122"),
        *([("20261121", "20261121")] * (3 if final_attempt else 1)),
    ]
    assert set(entries(output)) == {"10"} and entries(output)["10"].paths is None
    assert snapshot(output).last_complete_discovery is None
    assert quotas.open(ACCOUNT).daily_calls(NOW.date(), ZONE) == DailyCalls(
        catalogue=len(calls)
    )


@pytest.mark.parametrize("crosses_midnight", [False, True])
def test_quota_summary_counts_a_lost_fit_response_on_the_local_day(
    coros: CorosFixture, monkeypatch: pytest.MonkeyPatch, crosses_midnight: bool
) -> None:
    _, state, quotas, output = coros
    before_midnight = datetime(2026, 11, 21, 22, 59, tzinfo=UTC)
    instant = before_midnight
    monkeypatch.setattr(coros_cli._UtcClock, "now", lambda _: instant)
    quotas.initialize(ACCOUNT)

    def catalogue_reply_at_midnight(request: Request) -> Response:
        nonlocal instant
        if crosses_midnight:
            instant += timedelta(minutes=1)
        return catalogue(("10", 100, FIRST))

    def unreachable(*args: object, **kwargs: object) -> None:
        raise URLError("private host")

    monkeypatch.setattr(coros_gateway, "build_opener", unreachable)
    state["transport"] = FakeTransport(
        INIT, catalogue_reply_at_midnight, coros_gateway.HttpsTransport().send
    )
    code, out, err = sync(output)

    day = instant.astimezone(ZONE).date()
    assert code == 1 and "FITs downloaded: 0" in out
    assert f"Local quota day: {day} (Europe/Paris)" in out
    assert f"Catalogue calls: {0 if crosses_midnight else 1}" in out
    assert "FIT requests: 1" in out
    assert "Local FIT limit: 50 requests per rolling 24 hours" in out
    assert "private" not in err


@pytest.mark.parametrize("interrupted", [False, True])
def test_quota_summary_failure_preserves_acquisition_summary_and_interrupt_status(
    coros: CorosFixture, monkeypatch: pytest.MonkeyPatch, interrupted: bool
) -> None:
    _, state, quotas, output = coros
    quotas.initialize(ACCOUNT)

    def unavailable(*args: object) -> DailyCalls:
        raise QuotaUnavailable("Quota registry is unreadable.")

    def interrupt(request: Request) -> Response:
        raise KeyboardInterrupt

    monkeypatch.setattr(SqliteQuotaRegistry, "daily_calls", unavailable)
    state["transport"] = FakeTransport(INIT, interrupt if interrupted else catalogue())
    code, out, err = sync(output)

    assert code == (130 if interrupted else 1)
    assert "FITs downloaded: 0" in out
    assert "Could not read daily COROS quota counters." in err
    assert "Quota registry is unreadable." in err
    assert ("COROS command interrupted." in err) is interrupted


@pytest.mark.parametrize(
    "invalid", ["wrong-id", "ambiguous", "base64", "header", "records", "crc"]
)
def test_individual_bad_fit_response_does_not_block_next_activity(
    coros: CorosFixture, invalid: str
) -> None:
    _, state, quotas, output = coros
    quotas.initialize(ACCOUNT)
    failure = {
        "wrong-id": fit_reply(4),
        "ambiguous": rpc_reply(
            3,
            {
                "content": [
                    {
                        "type": "resource",
                        "resource": {"blob": base64.b64encode(FIT).decode()},
                    },
                    {
                        "type": "resource",
                        "resource": {"uri": "https://private.example"},
                    },
                ]
            },
        ),
        "base64": rpc_reply(
            3, {"content": [{"type": "resource", "resource": {"blob": "!!!!"}}]}
        ),
        "header": fit_reply(3, b"not a FIT"),
        "records": fit_reply(3, fit_with_body(b"\x00")),
        "crc": fit_reply(3, FIT[:-1] + bytes((FIT[-1] ^ 1,))),
    }[invalid]
    state["transport"] = FakeTransport(
        INIT, catalogue(("10", 100, FIRST), ("20", 100, SECOND)), failure, fit_reply(4)
    )
    code, out, err = sync(output)
    assert code == 1 and "Acquisition errors: 1" in out and "FITs downloaded: 1" in out
    assert "failed" in err and "succeeded" in err and "private" not in err
    rows = entries(output)
    assert rows["10"].tracked.acquisition is AcquisitionState.FAILED
    assert rows["10"].acquisition_attempts.count == 1
    assert rows["10"].acquisition_attempts.error is (
        FailureReason.UNAVAILABLE
        if invalid in {"wrong-id", "ambiguous"}
        else FailureReason.INVALID_FIT
    )
    assert not (output / rows["10"].paths.fit).exists()
    assert (output / rows["20"].paths.fit).read_bytes() == FIT
    assert quotas.open(ACCOUNT).daily_calls(NOW.date(), ZONE) == DailyCalls(
        catalogue=1, fit=2
    )


def test_valid_multisession_is_downloaded_without_conversion(
    coros: CorosFixture,
) -> None:
    _, state, quotas, output = coros
    quotas.initialize(ACCOUNT)
    # The synthetic fixture ends with one definition and one 25-byte session record.
    body = FIT[14:-2]
    data = fit_with_body(body + body[-25:])
    state["transport"] = FakeTransport(
        INIT, catalogue(("10", 100, FIRST)), fit_reply(3, data)
    )
    code, out, _ = sync(output)
    assert code == 0 and "FITs downloaded: 1" in out
    entry = entries(output)["10"]
    path = output / entry.paths.fit
    assert path.read_bytes() == data
    with pytest.raises(UnsupportedActivityError, match="multiple sessions"):
        FitdecodeActivityReader().read(path)
    assert not list(output.glob("*.md"))


@pytest.mark.parametrize("tool", ["large-fit", "oversized-fit", "oversized-catalogue"])
def test_https_response_limits_during_acquisition(
    coros: CorosFixture, monkeypatch: pytest.MonkeyPatch, tool: str
) -> None:
    _, _, quotas, output = coros
    quotas.initialize(ACCOUNT)
    catalogue_response = catalogue(("10", 100, FIRST))
    fit_response = fit_reply(3)
    data = FIT
    if tool == "large-fit":
        body = FIT[14:-2]
        data = fit_with_body(body * (1_501_000 // len(body) + 1))
        fit_response = fit_reply(3, data)
        assert len(fit_response.body) > 2_000_000
    elif tool == "oversized-fit":
        limit = 4 * ((32 * 1024 * 1024 + 2) // 3) + 1024 * 1024
        fit_response = replace(fit_response, body=fit_response.body.ljust(limit + 1))
    else:
        catalogue_response = replace(
            catalogue_response, body=catalogue_response.body.ljust(2_000_001)
        )
    replies = iter((INIT, catalogue_response, fit_response))

    def open_request(request: Request, *, timeout: int) -> addinfourl:
        assert request.full_url == COROS_ACCOUNT.issuer + "/mcp" and timeout == 10
        response = next(replies)
        return addinfourl(
            BytesIO(response.body),
            dict(response.headers),
            request.full_url,
            response.status,
        )

    monkeypatch.setattr(coros_cli, "HttpsTransport", coros_gateway.HttpsTransport)
    monkeypatch.setattr(
        coros_gateway, "build_opener", lambda *_: SimpleNamespace(open=open_request)
    )
    code, out, err = sync(output)
    if tool == "large-fit":
        assert code == 0 and "FITs downloaded: 1" in out
        entry = entries(output)["10"]
        assert entry.paths is not None
        assert (output / entry.paths.fit).read_bytes() == data
    else:
        assert code == 1 and "FITs downloaded: 0" in out
        if tool == "oversized-fit":
            assert "COROS response exceeds the size limit." in err
        else:
            assert "Catalogue coverage: incomplete" in out
            assert (
                "COROS returned an unrecognized or unusable catalogue response." in err
            )
        assert not list(output.rglob("*.fit"))


def test_collision_keeps_first_activity_and_uses_no_quota_for_second(
    coros: CorosFixture,
) -> None:
    _, state, quotas, output = coros
    quotas.initialize(ACCOUNT)
    same_catalogue = catalogue(("20", 102, FIRST), ("10", 100, FIRST))
    state["transport"] = FakeTransport(INIT, same_catalogue)
    code, out, err = sync(output, "--last-days", "1", "--dry-run")
    assert code == 0 and err == ""
    assert "Estimated FIT requests: 1" in out and "Conflicts: 1" in out
    assert not list(output.iterdir())
    assert quotas.open(ACCOUNT).daily_calls(NOW.date(), ZONE) == DailyCalls()
    state["transport"] = FakeTransport(
        INIT,
        same_catalogue,
        fit_reply(3),
    )
    code, out, err = sync(output)
    assert code == 1 and "Acquisition errors: 1" in out and "FITs downloaded: 1" in out
    assert "already exists" in err
    rows = entries(output)
    assert rows["10"].tracked.acquisition is AcquisitionState.ACQUIRED
    assert rows["20"].acquisition_attempts.error is FailureReason.CONFLICT
    assert rows["20"].acquisition_attempts.count == 0
    assert payloads(state["transport"])[-1]["params"]["arguments"]["labelId"] == "10"
    assert list((output / "fits").iterdir()) == [output / rows["10"].paths.fit]
    assert quotas.open(ACCOUNT).daily_calls(NOW.date(), ZONE).fit == 1


@pytest.mark.parametrize("server", [False, True])
def test_partial_quota_preserves_candidates_and_reports_retry_time(
    coros: CorosFixture, server: bool
) -> None:
    _, state, quotas, output = coros
    quotas.initialize(ACCOUNT)
    ledger = quotas.open(ACCOUNT)
    if not server:
        for _ in range(49):
            ledger.reserve_fit()
    replies = [Response(429, {"Retry-After": "60"}, b"secret")] if server else []
    state["transport"] = FakeTransport(
        INIT,
        catalogue(("10", 100, FIRST), ("20", 100, SECOND), ("30", 100, THIRD)),
        fit_reply(3),
        *replies,
    )
    code, out, err = sync(output)
    assert code == 1 and "Deferred for quota: 2" in out and "FITs downloaded: 1" in out
    assert (
        "Retry after "
        + (
            NOW + timedelta(seconds=60) if server else NOW + timedelta(days=1)
        ).isoformat()
        in err
    )
    rows = entries(output)
    assert rows["20"].acquisition_attempts.count == int(server)
    assert rows["30"].acquisition_attempts.count == 0
    assert ledger.daily_calls(NOW.date(), ZONE).fit == (2 if server else 50)
    if not server:
        other = output.parent / "another-output"
        other.mkdir()
        state["transport"] = FakeTransport(INIT, catalogue(("40", 100, FIRST)))
        code, out, _ = sync(other)
        assert code == 1 and "Deferred for quota: 1" in out
        assert len(state["transport"].requests) == 2
        assert entries(other)["40"].tracked.acquisition is AcquisitionState.PENDING


@pytest.mark.parametrize("status", [401, 403])
def test_revocation_stops_fit_calls_after_persisted_renewal(
    coros: CorosFixture, status: int
) -> None:
    backend, state, quotas, output = coros
    quotas.initialize(ACCOUNT)
    VaultConnectionStore(backend).save(
        ConnectionRecord(
            COROS_ACCOUNT,
            Session("client", "old-private-token", "private-refresh", NOW.timestamp()),
        )
    )

    def refused(request: Request) -> Response:
        assert request.get_header("Authorization") == "Bearer new-private-token"
        record = VaultConnectionStore(backend).load()
        assert (
            record is not None and record.session.refresh_token == "new-private-refresh"
        )
        return Response(status, {}, b"private provider detail")

    issuer = COROS_ACCOUNT.issuer
    state["transport"] = FakeTransport(
        reply(
            {
                "token_type": "Bearer",
                "access_token": "new-private-token",
                "refresh_token": "new-private-refresh",
                "expires_in": 3600,
            }
        ),
        reply({"issuer": issuer, "userinfo_endpoint": issuer + "/connect/userinfo"}),
        reply({"sub": COROS_ACCOUNT.subject}),
        INIT,
        catalogue(("10", 100, FIRST), ("20", 100, SECOND)),
        refused,
    )
    code, out, err = sync(output)
    assert code == 1 and "Acquisition errors: 1" in out
    assert "fit-to-md coros login" in err and "private" not in out + err
    assert entries(output)["20"].acquisition_attempts.count == 0
    assert quotas.open(ACCOUNT).daily_calls(NOW.date(), ZONE).fit == 1


@pytest.mark.parametrize("failure", ["interrupt", "stage", "registry", "timeout"])
def test_failure_preserves_success_and_stops_or_continues_as_required(
    coros: CorosFixture, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    _, state, quotas, output = coros
    quotas.initialize(ACCOUNT)
    publish = files._StagedFit.publish
    record_integrity = SqliteRegistry.record_fit_integrity

    def publish_fault(staged):
        if "11-01" in staged.destination.name:
            raise StorageFailure("Injected disk failure.")
        publish(staged)

    def registry_fault(registry, identity, integrity):
        if identity.remote_id == "20":
            raise StorageFailure("Injected registry failure.")
        record_integrity(registry, identity, integrity)

    def interrupt(request):
        raise KeyboardInterrupt

    next_reply = fit_reply(4)
    tail = []
    if failure == "interrupt":
        next_reply = interrupt
    elif failure == "stage":
        monkeypatch.setattr(files._StagedFit, "publish", publish_fault)
    elif failure == "registry":
        monkeypatch.setattr(SqliteRegistry, "record_fit_integrity", registry_fault)
    else:

        def unreachable(*args, **kwargs):
            raise URLError("private host")

        https = coros_gateway.HttpsTransport()
        monkeypatch.setattr(coros_gateway, "build_opener", unreachable)
        next_reply = https.send
        tail = [fit_reply(5)]

    state["transport"] = FakeTransport(
        INIT,
        catalogue(("10", 100, FIRST), ("20", 100, SECOND), ("30", 100, THIRD)),
        fit_reply(3),
        next_reply,
        *tail,
    )
    code, out, err = sync(output)
    assert code == (130 if failure == "interrupt" else 1)
    assert "private" not in err
    rows = entries(output)
    assert rows["10"].tracked.acquisition is AcquisitionState.ACQUIRED
    assert (output / rows["10"].paths.fit).read_bytes() == FIT
    assert len(list((output / "fits").glob("*.tmp"))) == 0
    assert rows["20"].tracked.acquisition is not AcquisitionState.ACQUIRED
    if failure == "timeout":
        assert rows["30"].tracked.acquisition is AcquisitionState.ACQUIRED
        assert quotas.open(ACCOUNT).daily_calls(NOW.date(), ZONE).fit == 3
        assert "FITs downloaded: 2" in out
    else:
        assert rows["30"].acquisition_attempts.count == 0
        assert quotas.open(ACCOUNT).daily_calls(NOW.date(), ZONE).fit == 2

    if failure == "interrupt":
        assert "FITs downloaded: 1" in out and "interrupted" in err
        state["transport"] = FakeTransport(
            INIT,
            catalogue(("10", 100, FIRST), ("20", 100, SECOND), ("30", 100, THIRD)),
            fit_reply(3),
            fit_reply(4),
        )
        code, out, _ = sync(output)
        assert (
            code == 0
            and "FITs already available: 1" in out
            and "FITs downloaded: 2" in out
        )


@pytest.mark.parametrize(
    ("args", "missing_root"),
    [
        (("--since", "2026-11-22"), False),
        (("--since", "2026-02-30"), False),
        (("--last-days", "0"), False),
        (("--last-days", "1"), True),
    ],
)
def test_invalid_options_and_output_precede_vault_access(
    coros: CorosFixture,
    monkeypatch: pytest.MonkeyPatch,
    args: tuple[str, ...],
    missing_root: bool,
) -> None:
    _, _, _, output = coros
    if missing_root:
        output = output / "absent"
    monkeypatch.setattr(
        coros_cli, "system_backend", lambda **_: pytest.fail("unexpected vault")
    )
    assert sync(output, *args)[0] == 2
    assert not list(output.glob("*"))
    assert output.exists() is not missing_root


@pytest.mark.parametrize("problem", ["no-quota", "no-session", "no-vault"])
def test_missing_prerequisite_stops_before_network_or_output_changes(
    coros: CorosFixture, problem: str
) -> None:
    backend, state, quotas, output = coros
    if problem == "no-session":
        VaultConnectionStore(backend).clear()
    if problem == "no-vault":
        backend.available = False
    code, _, err = sync(output)
    assert code == 1 and not state["transport"].requests and not list(output.iterdir())
    assert {"no-quota": "init-quota", "no-session": "coros login", "no-vault": "vault"}[
        problem
    ] in err
    assert "private backend error" not in err


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_repairs_tracked_files_outside_window_and_preserves_conversion(
    coros: CorosFixture, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    _, state, quotas, output = coros
    quotas.initialize(ACCOUNT)
    old = FIRST - timedelta(days=1)
    state["transport"] = FakeTransport(
        INIT, catalogue(("10", 100, old), start=date(2026, 11, 18)), fit_reply(3)
    )
    assert sync(output, "--last-days", "2")[0] == 0
    entry = entries(output)["10"]
    assert entry.paths is not None
    identity = entry.tracked.activity.identity
    with SqliteOutput(output).write(ACCOUNT, VaultConnectionStore.reference) as storage:
        storage.registry.record_failure(
            identity, Stage.CONVERSION, FailureReason.UNSUPPORTED
        )
    path = output / entry.paths.fit
    damaged = FIT[:-1] + bytes([FIT[-1] ^ 1])
    if damage == "corrupt":
        path.write_bytes(damaged)
    else:
        path.unlink()

    state["transport"] = FakeTransport(INIT, catalogue())
    code, out, _ = run_sync(output, "--download-only", "--last-days", "1")
    assert code == 0 and "FITs downloaded: 0" in out

    later = NOW + timedelta(hours=1)
    monkeypatch.setattr(coros_cli._UtcClock, "now", lambda _: later)
    state["transport"] = FakeTransport(INIT, catalogue())
    before = snapshot(output)
    code, out, err = run_sync(output, "--download-only", "--dry-run")
    assert code == 0 and "Estimated FIT requests: 1" in out and not err
    assert snapshot(output) == before

    state["transport"] = FakeTransport(INIT, catalogue(), fit_reply(3))
    code, out, err = run_sync(output, "--download-only")
    assert code == 0 and "FITs downloaded: 1" in out and "Downloading" in err
    assert path.read_bytes() == FIT
    archived = list((output / ".fit-to-md" / "damaged-fits").glob("*.fit"))
    assert [item.read_bytes() for item in archived] == (
        [damaged] if damage == "corrupt" else []
    )
    repaired = entries(output)["10"]
    assert repaired.paths == entry.paths
    assert repaired.tracked.conversion is ConversionState.UNSUPPORTED
    assert repaired.acquisition_attempts.count == 2
    assert quotas.open(ACCOUNT).daily_calls(NOW.date(), ZONE).fit == 2


def test_empty_catalogue_has_successful_zero_summary(coros: CorosFixture) -> None:
    _, state, quotas, output = coros
    quotas.initialize(ACCOUNT)
    state["transport"] = FakeTransport(INIT, catalogue())
    code, out, err = sync(output)
    assert (
        code == 0 and "Selected: 0" in out and "FITs downloaded: 0" in out and not err
    )
    assert not (output / "fits").exists()
    assert quotas.open(ACCOUNT).daily_calls(NOW.date(), ZONE) == DailyCalls(catalogue=1)


def test_output_ownership_is_checked_before_session_renewal(
    coros: CorosFixture,
) -> None:
    backend, state, quotas, output = coros
    quotas.initialize(ACCOUNT)
    other = to_account_identity(
        CorosAccount("eu", COROS_ACCOUNT.issuer, "other-subject")
    )
    with SqliteOutput(output).write(other, VaultConnectionStore.reference):
        pass
    VaultConnectionStore(backend).save(
        ConnectionRecord(
            COROS_ACCOUNT, Session("client", "access", "refresh", NOW.timestamp())
        )
    )

    code, _, err = sync(output)

    assert code == 1 and "belongs to a different account" in err
    assert state["transport"].requests == []
    assert quotas.open(ACCOUNT).daily_calls(NOW.date(), ZONE) == DailyCalls()


def test_interrupt_after_rename_preserves_fit_and_integrity_for_reconciliation(
    coros: CorosFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, state, quotas, output = coros
    quotas.initialize(ACCOUNT)

    def interrupt(registry: SqliteRegistry, identity: ActivityIdentity) -> None:
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(SqliteRegistry, "record_acquired", interrupt)
        state["transport"] = FakeTransport(
            INIT, catalogue(("10", 100, FIRST)), fit_reply(3)
        )
        assert sync(output)[0] == 130

    entry = entries(output)["10"]
    assert entry.tracked.acquisition is AcquisitionState.PENDING
    assert entry.paths is not None and entry.fit_integrity is not None
    assert (output / entry.paths.fit).read_bytes() == FIT

    state["transport"] = FakeTransport(INIT, catalogue(("10", 100, FIRST)))
    preview_code, preview, _ = run_sync(output, "--download-only", "--dry-run")
    assert preview_code == 0 and "Estimated FIT requests: 0" in preview
    assert entries(output)["10"] == entry

    state["transport"] = FakeTransport(INIT, catalogue())
    code, out, err = run_sync(output, "--download-only")
    assert code == 0 and "FITs already available: 1" in out and not err
    assert (output / entry.paths.fit).read_bytes() == FIT
    reconciled = entries(output)["10"]
    assert reconciled.tracked.acquisition is AcquisitionState.ACQUIRED
    assert reconciled.fit_integrity == entry.fit_integrity
    assert reconciled.acquisition_attempts == entry.acquisition_attempts
    assert quotas.open(ACCOUNT).daily_calls(NOW.date(), ZONE).fit == 1


def test_incremental_needs_initial_reference_before_renewal_or_quota(
    coros: CorosFixture,
) -> None:
    _, state, _, output = coros
    code, _, err = run_sync(output, "--download-only")
    assert code == 2 and "--last-days 7 --output DIR" in err
    assert not state["transport"].requests and not list(output.iterdir())


@pytest.mark.parametrize("date_text", ["2026-02-30", "2026-11-22", "21-11-2026"])
def test_invalid_reference_date_precedes_vault(
    coros: CorosFixture, monkeypatch: pytest.MonkeyPatch, date_text: str
) -> None:
    _, _, _, output = coros
    monkeypatch.setattr(
        coros_cli, "system_backend", lambda **_: pytest.fail("unexpected vault")
    )
    assert run_sync(output, "--init-coros-reference", date_text)[0] == 2
    assert not list(output.iterdir())


@pytest.mark.parametrize("option", ["--dry-run", "--download-only"])
def test_repositioning_does_not_accept_execution_flags(
    coros: CorosFixture, monkeypatch: pytest.MonkeyPatch, option: str
) -> None:
    _, _, _, output = coros
    monkeypatch.setattr(
        coros_cli, "system_backend", lambda **_: pytest.fail("unexpected vault")
    )
    code, _, err = run_sync(output, "--init-coros-reference", "2026-11-21", option)
    assert code == 2 and "cannot be combined" in err


@pytest.mark.parametrize("status", [401, 503])
def test_mcp_initialization_distinguishes_authentication_from_discovery_error(
    coros: CorosFixture, status: int
) -> None:
    _, state, quotas, output = coros
    quotas.initialize(ACCOUNT)
    state["transport"] = FakeTransport(*(Response(status, {}, b"private"),) * 3)

    code, out, err = sync(output)

    assert code == 1 and "private" not in err
    checkpoint = snapshot(output)
    assert not checkpoint.entries and checkpoint.last_complete_discovery is None
    if status == 401:
        assert checkpoint.reference is None
        assert "coros login" in err and len(state["transport"].requests) == 1
    else:
        assert checkpoint.reference == Reference(datetime(2026, 11, 20, 23, tzinfo=UTC))
        assert "Catalogue coverage: incomplete" in out
    assert quotas.open(ACCOUNT).daily_calls(NOW.date(), ZONE) == DailyCalls()


def test_incremental_resumes_old_work_and_explicit_recovers_a_late_import(
    coros: CorosFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, state, quotas, output = coros
    quotas.initialize(ACCOUNT)
    old = FIRST - timedelta(days=1)
    late = SECOND - timedelta(days=1)
    state["transport"] = FakeTransport(
        INIT,
        catalogue(("10", 100, old), start=date(2026, 11, 18)),
        Response(404, {}, b""),
    )
    assert sync(output, "--last-days", "2")[0] == 1
    assert snapshot(output).reference == Reference(NOW)
    assert entries(output)["10"].tracked.acquisition is AcquisitionState.FAILED

    later = NOW + timedelta(days=2)
    monkeypatch.setattr(coros_cli._UtcClock, "now", lambda _: later)
    end = date(2026, 11, 24)
    state["transport"] = FakeTransport(
        INIT, catalogue(start=date(2026, 11, 21), end=end)
    )
    assert run_sync(output, "--download-only", "--last-days", "1")[0] == 0
    assert snapshot(output).reference == Reference(NOW)
    assert entries(output)["10"].acquisition_attempts.count == 1

    state["transport"] = FakeTransport(
        INIT, catalogue(("20", 100, late), end=end), fit_reply(3)
    )
    code, out, _ = run_sync(output, "--download-only")
    assert code == 0 and "FITs downloaded: 1" in out
    assert payloads(state["transport"])[-1]["params"]["arguments"]["labelId"] == "10"
    assert set(entries(output)) == {"10"}
    assert snapshot(output).reference == Reference(later)

    state["transport"] = FakeTransport(
        INIT,
        catalogue(
            ("10", 100, old), ("20", 100, late), start=date(2026, 11, 18), end=end
        ),
        fit_reply(3),
    )
    assert sync(output, "--since", "2026-11-20")[0] == 0
    assert set(entries(output)) == {"10", "20"}
    assert snapshot(output).reference == Reference(later)


def test_repositioning_preserves_quota_and_reinterprets_day_in_current_zone(
    coros: CorosFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, state, quotas, output = coros
    code, out, err = run_sync(output, "--init-coros-reference", "2026-11-21")
    assert code == 0 and not err
    assert "Previous reference: unset" in out
    first_reference = Reference(datetime(2026, 11, 20, 23, tzinfo=UTC))
    assert snapshot(output).reference == first_reference
    assert not state["transport"].requests

    quotas.initialize(ACCOUNT)
    state["transport"] = FakeTransport(
        INIT, catalogue(("10", 100, FIRST)), fit_reply(3)
    )
    assert sync(output)[0] == 0
    before = snapshot(output)
    new_zone = ZoneInfo("America/New_York")
    monkeypatch.setattr(SystemLocalTimezone, "resolve", lambda _: new_zone)
    state["transport"] = FakeTransport(INIT, catalogue())
    code, out, _ = run_sync(output, "--download-only")
    assert code == 0 and "Period [2026-11-20T00:00:00-05:00" in out
    assert snapshot(output).reference == Reference(NOW)
    assert snapshot(output).reference_zone == new_zone.key
    assert snapshot(output).entries == before.entries

    calls = quotas.open(ACCOUNT).daily_calls(NOW.date(), ZONE)
    state["transport"] = FakeTransport()
    code, out, err = run_sync(output, "--init-coros-reference", "2026-11-19")
    assert code == 0 and not err and not state["transport"].requests
    assert "Previous reference: 2026-11-21T12:00:00+00:00" in out
    assert "New reference: 2026-11-19T05:00:00+00:00 (America/New_York)" in out
    assert snapshot(output).entries == before.entries
    assert quotas.open(ACCOUNT).daily_calls(NOW.date(), ZONE) == calls


def test_interrupt_inside_checkpoint_rolls_back_candidates_and_reference(
    coros: CorosFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, state, quotas, output = coros
    quotas.initialize(ACCOUNT)
    save = SqliteRegistry._save_reference

    def interrupt_at_advance(
        registry: SqliteRegistry, reference: Reference, zone: ZoneInfo
    ) -> None:
        save(registry, reference, zone)
        if reference == Reference(NOW):
            raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(SqliteRegistry, "_save_reference", interrupt_at_advance)
        state["transport"] = FakeTransport(INIT, catalogue(("10", 100, FIRST)))
        assert sync(output)[0] == 130

    checkpoint = snapshot(output)
    assert checkpoint.reference == Reference(datetime(2026, 11, 20, 23, tzinfo=UTC))
    assert not checkpoint.entries and checkpoint.last_complete_discovery is None
    assert quotas.open(ACCOUNT).daily_calls(NOW.date(), ZONE).fit == 0
    state["transport"] = FakeTransport(
        INIT, catalogue(("10", 100, FIRST)), fit_reply(3)
    )
    assert run_sync(output, "--download-only")[0] == 0
    assert snapshot(output).reference == Reference(NOW)


def test_interrupt_after_checkpoint_keeps_candidates_and_reference(
    coros: CorosFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, state, quotas, output = coros
    quotas.initialize(ACCOUNT)
    save_discovery = SqliteRegistry.save_discovery

    def interrupt_after_discovery(registry: SqliteRegistry, *args, **kwargs) -> None:
        save_discovery(registry, *args, **kwargs)
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(SqliteRegistry, "save_discovery", interrupt_after_discovery)
        state["transport"] = FakeTransport(INIT, catalogue(("10", 100, FIRST)))
        assert sync(output)[0] == 130

    checkpoint = snapshot(output)
    assert checkpoint.reference == Reference(NOW)
    assert checkpoint.last_complete_discovery == NOW
    (candidate,) = checkpoint.entries
    assert candidate.paths is None
    assert quotas.open(ACCOUNT).daily_calls(NOW.date(), ZONE).fit == 0

    state["transport"] = FakeTransport(INIT, catalogue(), fit_reply(3))
    assert run_sync(output, "--download-only")[0] == 0
    assert entries(output)["10"].tracked.acquisition is AcquisitionState.ACQUIRED


def test_restart_recovers_sqlite_hot_journal_under_output_lock(
    coros: CorosFixture,
) -> None:
    _, state, quotas, output = coros
    quotas.initialize(ACCOUNT)
    assert run_sync(output, "--init-coros-reference", "2026-11-21")[0] == 0
    original = snapshot(output)
    database = output / ".fit-to-md" / "coros.sqlite3"
    worker = """
import os
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1])
connection.execute("PRAGMA cache_size = 2")
connection.execute("BEGIN IMMEDIATE")
connection.execute("UPDATE output_account SET reference_utc = NULL, reference_zone = NULL")
connection.executemany(
    "INSERT INTO activities (remote_id, sport, discovered_utc) VALUES (?, ?, ?)",
    [(str(i) + "x" * 3000, 100, "2026-11-21T00:00:00+00:00") for i in range(100)],
)
os._exit(0)
"""
    subprocess.run(
        [sys.executable, "-c", worker, str(database)], check=True, timeout=10
    )
    journal = database.with_name(database.name + "-journal")
    assert journal.is_file()
    assert run_sync(output, "--dry-run", "--download-only")[0] == 1
    assert journal.is_file() and not state["transport"].requests

    def verify_baseline(request: Request) -> Response:
        assert not journal.exists()
        return INIT

    state["transport"] = FakeTransport(verify_baseline, catalogue())
    assert run_sync(output, "--download-only")[0] == 0
    result = snapshot(output)
    assert result.reference == Reference(NOW) and result.reference != original.reference
    assert result.entries == original.entries


def test_interrupt_after_archiving_damaged_fit_keeps_repair_possible(
    coros: CorosFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, state, quotas, output = coros
    quotas.initialize(ACCOUNT)
    state["transport"] = FakeTransport(
        INIT, catalogue(("10", 100, FIRST)), fit_reply(3)
    )
    assert sync(output)[0] == 0
    entry = entries(output)["10"]
    assert entry.paths is not None
    final = output / entry.paths.fit
    final.write_bytes(b"damaged")

    def interrupt(registry: SqliteRegistry, identity: ActivityIdentity) -> None:
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(SqliteRegistry, "reset_acquisition", interrupt)
        state["transport"] = FakeTransport(INIT, catalogue())
        assert run_sync(output, "--download-only")[0] == 130

    assert entries(output)["10"] == entry
    assert not final.exists()
    assert quotas.open(ACCOUNT).daily_calls(NOW.date(), ZONE).fit == 1
    state["transport"] = FakeTransport(INIT, catalogue(), fit_reply(3))
    assert run_sync(output, "--download-only")[0] == 0
    assert final.read_bytes() == FIT
    archives = (output / ".fit-to-md" / "damaged-fits").glob("*.fit")
    assert [path.read_bytes() for path in archives] == [b"damaged"]


@pytest.mark.parametrize("final_exists", [False, True])
def test_interrupted_publication_ignores_temporaries_and_preserves_foreign_fit(
    coros: CorosFixture, monkeypatch: pytest.MonkeyPatch, final_exists: bool
) -> None:
    _, state, quotas, output = coros
    quotas.initialize(ACCOUNT)

    def interrupt(staged: files._StagedFit) -> None:
        staged.temporary.with_name(".fit-to-md-stray.tmp").write_bytes(FIT)
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(files._StagedFit, "publish", interrupt)
        state["transport"] = FakeTransport(
            INIT, catalogue(("10", 100, FIRST)), fit_reply(3)
        )
        assert sync(output)[0] == 130

    entry = entries(output)["10"]
    assert entry.paths is not None and entry.fit_integrity is not None
    assert snapshot(output).reference == Reference(NOW)
    path = output / entry.paths.fit
    assert not path.exists()
    if final_exists:
        path.write_bytes(b"foreign")
        state["transport"] = FakeTransport(INIT, catalogue())
        code, out, err = run_sync(output, "--download-only")
        assert code == 1 and "Acquisition errors: 1" in out and "already exists" in err
        assert path.read_bytes() == b"foreign"
        assert quotas.open(ACCOUNT).daily_calls(NOW.date(), ZONE).fit == 1
    else:
        state["transport"] = FakeTransport(INIT, catalogue(), fit_reply(3))
        code, out, _ = run_sync(output, "--download-only")
        assert code == 0 and "FITs downloaded: 1" in out
        assert path.read_bytes() == FIT
        assert quotas.open(ACCOUNT).daily_calls(NOW.date(), ZONE).fit == 2
    assert path.with_name(".fit-to-md-stray.tmp").read_bytes() == FIT


def test_normal_sync_honors_config_and_preserves_edited_report(
    coros: CorosFixture,
) -> None:
    _, state, quotas, output = coros
    quotas.initialize(ACCOUNT)
    config = output.parent / "shared.config"
    config.write_text(
        f"output = {output.parent / 'missing'}\n"
        "output_by_activity_time = true\n"
        "workout_report = off\n"
        "hr_zone_boundaries = 100,130,150,170\n"
        "dynamics_step_size = 60\n"
        "dem_sample_distance = 35\n"
        "opentopodata_dataset = custom\n",
        encoding="utf-8",
    )
    state["transport"] = FakeTransport(
        INIT, catalogue(("10", 100, FIRST)), fit_reply(3)
    )
    code, out, err = run_sync(
        output, "--last-days", "1", "--config", str(config), "--workout-report", "laps"
    )
    assert code == 0 and "FITs downloaded: 1" in out and "Reports created: 1" in out
    assert "Conversion errors: 0" in out and "Heart-Rate Zones" not in out
    assert "Converting local FIT" in err and "Report created." in err
    first = entries(output)["10"]
    assert first.conversion_attempts.count == 1
    assert first.conversion_options == ConversionOptions(
        workout_report="laps",
        hr_zone_boundaries=(100, 130, 150, 170),
        dynamics_step_size=60,
        dem_sample_distance=35,
        opentopodata_dataset="custom",
    )
    target = output / first.paths.report
    markdown = target.read_text(encoding="utf-8")
    assert "Heart-Rate Zones" in markdown and "Workout Breakdown" in markdown
    target.write_text("# Edited\n", encoding="utf-8")

    # COROS returns a revision of the original activity and one new activity.
    config.write_text(f"output = {output}\nworkout_report = off\n", encoding="utf-8")
    state["transport"] = FakeTransport(
        INIT,
        catalogue(("10", 100, FIRST + timedelta(seconds=20)), ("20", 100, SECOND)),
        fit_reply(3),
    )
    code, out, err = run_coros("sync", "--config", str(config))
    assert code == 0 and "FITs already available: 1" in out
    assert "Reports already available: 1" in out and "Reports created: 1" in out
    assert len(state["transport"].requests) == 3
    assert payloads(state["transport"])[-1]["params"]["arguments"]["labelId"] == "20"
    assert target.read_text(encoding="utf-8") == "# Edited\n"
    assert entries(output)["10"] == first
    assert "private-" not in out + err

    state["transport"] = FakeTransport(
        INIT, catalogue(("10", 100, FIRST), ("20", 100, SECOND))
    )
    assert run_coros("sync", "--config", str(config))[0] == 0
    assert len(state["transport"].requests) == 2
    assert len(list(output.glob("*.md"))) == 2


def test_download_only_then_repair_missing_report_outside_incremental_window(
    coros: CorosFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, state, quotas, output = coros
    quotas.initialize(ACCOUNT)
    old = FIRST - timedelta(days=1)
    state["transport"] = FakeTransport(
        INIT, catalogue(("10", 100, old), start=date(2026, 11, 18)), fit_reply(3)
    )
    assert sync(output, "--last-days", "2")[0] == 0
    original = entries(output)["10"]
    assert original.tracked.conversion is ConversionState.PENDING

    state["transport"] = FakeTransport(INIT, catalogue())
    code, out, _ = run_sync(output)
    assert (
        code == 0 and "FITs already available: 1" in out and "Reports created: 1" in out
    )
    target = output / original.paths.report
    previous = target.read_bytes()
    target.unlink()

    later = NOW + timedelta(days=2)
    monkeypatch.setattr(coros_cli._UtcClock, "now", lambda _: later)
    end = date(2026, 11, 24)
    state["transport"] = FakeTransport(
        INIT, catalogue(start=date(2026, 11, 21), end=end)
    )
    assert run_sync(output, "--last-days", "1")[0] == 0
    assert not target.exists()

    state["transport"] = FakeTransport(INIT, catalogue(end=end))
    code, out, _ = run_sync(output, "--dry-run")
    assert code == 0 and "Resume: 1" in out and "(convert)" in out
    assert "Estimated FIT requests: 0" in out and not target.exists()

    state["transport"] = FakeTransport(INIT, catalogue(end=end))
    code, out, _ = run_sync(output)
    assert code == 0 and "Reports created: 1" in out
    assert target.read_bytes() == previous
    assert len(state["transport"].requests) == 2
    repaired = entries(output)["10"]
    assert repaired.conversion_attempts.count == 2
    assert repaired.acquisition_attempts == original.acquisition_attempts


@pytest.mark.parametrize("server", [False, True])
def test_normal_sync_converts_existing_fit_after_quota_stop(
    coros: CorosFixture, server: bool
) -> None:
    _, state, quotas, output = coros
    quotas.initialize(ACCOUNT)
    state["transport"] = FakeTransport(
        INIT, catalogue(("30", 100, THIRD)), fit_reply(3)
    )
    assert sync(output)[0] == 0
    ledger = quotas.open(ACCOUNT)
    if not server:
        for _ in range(49):
            ledger.reserve_fit()
    replies = (
        (Response(429, {"Retry-After": "60"}, b"private error"),) if server else ()
    )
    state["transport"] = FakeTransport(
        INIT, catalogue(("10", 100, FIRST), ("20", 100, SECOND)), *replies
    )
    code, out, err = run_sync(output)
    assert code == 1 and "Deferred for quota: 2" in out and "Reports created: 1" in out
    assert "Acquisition errors: 0" in out and "Conversion errors: 0" in out
    assert "Retry after " in err and "private error" not in err
    assert len(state["transport"].requests) == 2 + server
    rows = entries(output)
    assert rows["30"].tracked.conversion is ConversionState.CONVERTED
    assert rows["10"].tracked.acquisition is AcquisitionState.PENDING
    assert rows["20"].tracked.acquisition is AcquisitionState.PENDING


def test_conversion_failure_retries_and_enrichment_warning_is_successful(
    coros: CorosFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, state, quotas, output = coros
    quotas.initialize(ACCOUNT)
    original = GenerateMarkdownReport.execute_detailed
    attempted: list[Path] = []

    def generate(self: GenerateMarkdownReport, source: Path) -> GeneratedMarkdownReport:
        attempted.append(source)
        if len(attempted) == 1:
            raise OSError("private source detail")
        result = original(self, source)
        warning = ProviderDiagnostic(
            "Fake weather",
            ProviderDiagnosticKind.PROVIDER_UNAVAILABLE,
            "Weather unavailable.",
        )
        return replace(result, diagnostics=(warning,))

    monkeypatch.setattr(GenerateMarkdownReport, "execute_detailed", generate)
    state["transport"] = FakeTransport(
        INIT,
        catalogue(("10", 100, FIRST), ("20", 100, SECOND)),
        fit_reply(3),
        fit_reply(4),
    )
    code, out, err = run_sync(output, "--last-days", "1", "--dynamics-step-size", "65")
    assert code == 1 and "FITs downloaded: 2" in out and "Reports created: 1" in out
    assert "Acquisition errors: 0" in out and "Conversion errors: 1" in out
    assert "Weather unavailable." in err and "private source detail" not in err
    rows = entries(output)
    first = rows["10"]
    assert first.tracked.acquisition is AcquisitionState.ACQUIRED
    assert first.tracked.conversion is ConversionState.FAILED
    assert first.conversion_attempts.error is FailureReason.CONVERSION_FAILED
    assert first.conversion_options.dynamics_step_size == 65
    assert rows["20"].tracked.conversion is ConversionState.CONVERTED

    state["transport"] = FakeTransport(INIT, catalogue())
    code, out, err = run_sync(output)
    assert code == 0 and "Reports created: 1" in out and "Conversion errors: 0" in out
    assert "Weather unavailable." in err and len(state["transport"].requests) == 2
    final = entries(output)["10"]
    assert (
        final.conversion_attempts.count == 2 and final.conversion_attempts.error is None
    )
    assert final.conversion_options.dynamics_step_size == 30
    assert attempted == [
        output / first.paths.fit,
        output / rows["20"].paths.fit,
        output / first.paths.fit,
    ]


def test_unsupported_conversion_is_terminal_even_with_manual_report(
    coros: CorosFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, state, quotas, output = coros
    quotas.initialize(ACCOUNT)
    body = FIT[14:-2]
    data = fit_with_body(body + body[-25:])
    row = ("10", 100, FIRST)
    state["transport"] = FakeTransport(INIT, catalogue(row), fit_reply(3, data))
    code, out, err = run_sync(output, "--last-days", "1")
    assert code == 1 and "Conversion errors: 1" in out and "FITs downloaded: 1" in out
    assert "Use local conversion" in err
    first = entries(output)["10"]
    assert first.tracked.conversion is ConversionState.UNSUPPORTED
    assert first.conversion_attempts.error is FailureReason.UNSUPPORTED
    assert (output / first.paths.fit).read_bytes() == data
    target = output / first.paths.report
    target.write_text("# Manually created\n", encoding="utf-8")
    monkeypatch.setattr(
        GenerateMarkdownReport,
        "execute_detailed",
        lambda *_: pytest.fail("retried terminal conversion"),
    )

    state["transport"] = FakeTransport(INIT, catalogue(row))
    code, out, err = run_sync(output)
    assert code == 1 and "Reports created: 0" in out and "Conversion errors: 1" in out
    assert "Unsupported FIT" in err and len(state["transport"].requests) == 2
    assert entries(output)["10"] == first and first.conversion_attempts.count == 1
    assert target.read_text(encoding="utf-8") == "# Manually created\n"


def test_report_collision_preserves_foreign_file_and_can_resume(
    coros: CorosFixture,
) -> None:
    _, state, quotas, output = coros
    quotas.initialize(ACCOUNT)
    occupied = output / f"{FIRST.astimezone(ZONE):%Y-%m-%d %H-%M}.md"
    occupied.write_text("# Foreign\n", encoding="utf-8")
    row = ("10", 100, FIRST)
    state["transport"] = FakeTransport(INIT, catalogue(row))
    code, out, _ = run_sync(output, "--last-days", "1", "--dry-run")
    assert code == 0 and "Conflicts: 1" in out and "Estimated FIT requests: 1" in out

    state["transport"] = FakeTransport(INIT, catalogue(row), fit_reply(3))
    code, out, err = run_sync(output, "--last-days", "1")
    assert code == 1 and "FITs downloaded: 1" in out and "Conversion errors: 1" in out
    assert (
        occupied.name in err and occupied.read_text(encoding="utf-8") == "# Foreign\n"
    )
    assert list(output.glob("*.md")) == [occupied]
    assert entries(output)["10"].conversion_attempts.error is FailureReason.CONFLICT

    state["transport"] = FakeTransport(INIT, catalogue())
    code, out, _ = run_sync(output, "--dry-run")
    assert code == 0 and "Conflicts: 1" in out and "Estimated FIT requests: 0" in out
    occupied.unlink()
    state["transport"] = FakeTransport(INIT, catalogue())
    assert run_sync(output)[0] == 0
    assert len(state["transport"].requests) == 2 and occupied.exists()


@pytest.mark.parametrize("after_publication", [False, True])
def test_conversion_interrupt_preserves_fit_and_any_published_report(
    coros: CorosFixture, monkeypatch: pytest.MonkeyPatch, after_publication: bool
) -> None:
    _, state, quotas, output = coros
    quotas.initialize(ACCOUNT)

    def interrupt(*_):
        raise KeyboardInterrupt

    row = ("10", 100, FIRST)
    state["transport"] = FakeTransport(INIT, catalogue(row), fit_reply(3))
    with monkeypatch.context() as patch:
        if after_publication:
            patch.setattr(SqliteRegistry, "record_converted", interrupt)
        else:
            patch.setattr(GenerateMarkdownReport, "execute_detailed", interrupt)
        code, out, err = run_sync(output, "--last-days", "1")
    assert code == 130 and "FITs downloaded: 1" in out and "command interrupted" in err
    first = entries(output)["10"]
    target = output / first.paths.report
    assert target.exists() is after_publication
    assert first.tracked.conversion is ConversionState.PENDING
    assert (output / first.paths.fit).read_bytes() == FIT
    previous = target.read_bytes() if after_publication else None

    state["transport"] = FakeTransport(INIT, catalogue())
    code, out, err = run_sync(output)
    assert len(state["transport"].requests) == 2 and "FITs already available: 1" in out
    if after_publication:
        assert code == 1 and "already exists" in err
        assert target.read_bytes() == previous
    else:
        assert code == 0 and "Reports created: 1" in out and target.exists()


def test_status_reports_tracking_without_network_or_writes(
    coros: CorosFixture,
) -> None:
    backend, state, quotas, output = coros
    config = output.parent / "status.config"
    config.write_text(f"output = {output}\nworkout-report = laps\n", encoding="utf-8")
    code, out, err = run_coros("status", "--config", str(config))
    assert code == 0 and not err and "Reference (UTC): unset" in out
    assert not list(output.iterdir()) and not state["transport"].requests
    assert "Activities with pending work: 0" in out

    quotas.initialize(ACCOUNT)
    state["transport"] = FakeTransport(
        INIT,
        catalogue(("10", 100, FIRST), ("20", 100, SECOND)),
        fit_reply(3),
        Response(404, {}, b""),
    )
    assert sync(output)[0] == 1
    checkpoint = snapshot(output)
    state["transport"] = FakeTransport()
    code, out, err = run_coros("status", "--config", str(config))
    assert (
        code == 0 and not err and "COROS local status: Saved session available" in out
    )
    assert f"Reports: {output}" in out and f"FITs: {output / 'fits'}" in out
    assert f"Registry: {output / '.fit-to-md' / 'coros.sqlite3'}" in out
    assert f"Reference (UTC): {NOW.isoformat()}" in out
    assert "Reference timezone: Europe/Paris" in out
    assert "Activities with pending work: 2" in out and "Reports pending: 2" in out
    assert "FITs pending: 1" in out and "Unsupported conversions: 0" in out
    assert snapshot(output) == checkpoint and not state["transport"].requests

    assert run_coros("logout")[0] == 0
    assert VaultConnectionStore(backend).load() is None
    code, out, err = run_coros("status", "--output", str(output))
    assert code == 0 and not err and "tracking unavailable" in out
    assert snapshot(output) == checkpoint and not state["transport"].requests


@pytest.mark.parametrize(
    "contents",
    [
        "since = 2026-11-21\n",
        "weather-mode = invalid\n",
        "output-by-activity-time = maybe\n",
        "invalid line\n",
    ],
)
def test_sync_rejects_invalid_configuration_before_vault(
    coros: CorosFixture, monkeypatch: pytest.MonkeyPatch, contents: str
) -> None:
    _, _, _, output = coros
    config = output.parent / "bad.config"
    config.write_text(contents, encoding="utf-8")
    monkeypatch.setattr(
        coros_cli, "system_backend", lambda **_: pytest.fail("unexpected vault")
    )
    with pytest.raises(SystemExit) as error:
        run_sync(output, "--last-days", "1", "--config", str(config))
    assert error.value.code == 2 and not list(output.iterdir())


def test_normal_sync_requires_reference_before_renewal_or_quota(
    coros: CorosFixture,
) -> None:
    _, state, _, output = coros
    code, out, err = run_sync(output)
    assert code == 2 and not out and "--last-days 7" in err
    assert not list(output.iterdir()) and not state["transport"].requests


def test_normal_sync_repairs_fit_without_regenerating_edited_report(
    coros: CorosFixture,
) -> None:
    _, state, quotas, output = coros
    quotas.initialize(ACCOUNT)
    row = ("10", 100, FIRST)
    state["transport"] = FakeTransport(INIT, catalogue(row), fit_reply(3))
    assert run_sync(output, "--last-days", "1")[0] == 0
    original = entries(output)["10"]
    fit_path, report_path = output / original.paths.fit, output / original.paths.report
    fit_path.write_bytes(b"damaged")
    report_path.write_text("# Kept edit\n", encoding="utf-8")

    state["transport"] = FakeTransport(INIT, catalogue(), fit_reply(3))
    code, out, err = run_sync(output)
    assert (
        code == 0
        and "FITs downloaded: 1" in out
        and "Reports already available: 1" in out
    )
    assert "Reports created: 0" in out and "Converting local FIT" not in err
    assert (
        fit_path.read_bytes() == FIT
        and report_path.read_text(encoding="utf-8") == "# Kept edit\n"
    )
    repaired = entries(output)["10"]
    assert repaired.acquisition_attempts.count == 2
    assert repaired.conversion_attempts == original.conversion_attempts


def test_configured_missing_output_precedes_vault_for_download_only(
    coros: CorosFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, _, output = coros
    config = output.parent / "config.txt"
    missing = output / "missing"
    config.write_text(f"output = {missing}\n", encoding="utf-8")
    monkeypatch.setattr(
        coros_cli, "system_backend", lambda **_: pytest.fail("unexpected vault")
    )
    code, out, err = run_coros(
        "sync", "--download-only", "--last-days", "1", "--config", str(config)
    )
    assert code == 2 and not out and "existing directory" in err
    assert not missing.exists()


def test_coros_folder_can_still_be_converted_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    local = tmp_path / "coros"
    local.mkdir()
    fit = local / "activity.fit"
    fit.write_bytes(FIT)
    monkeypatch.setattr(
        coros_cli, "system_backend", lambda **_: pytest.fail("unexpected vault")
    )
    out, err = StringIO(), StringIO()
    assert cli.run(["./coros/"], stdout=out, stderr=err) == 0
    assert "FIT Report" in out.getvalue() and not err.getvalue()
    assert fit.with_suffix(".md").is_file()


def test_report_storage_failure_stops_further_mutations(
    coros: CorosFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, state, quotas, output = coros
    quotas.initialize(ACCOUNT)

    def fail_publication(*_: object) -> None:
        raise StorageFailure("Could not write the COROS report.")

    monkeypatch.setattr(files.LocalOutputFiles, "publish_report", fail_publication)
    state["transport"] = FakeTransport(
        INIT, catalogue(("10", 100, FIRST), ("20", 100, SECOND)), fit_reply(3)
    )
    code, out, err = run_sync(output, "--last-days", "1")
    assert (
        code == 1 and "Conversion errors: 1" in out and "Acquisition errors: 0" in out
    )
    assert "Could not write the COROS report." in err and not list(output.glob("*.md"))
    rows = entries(output)
    assert rows["10"].tracked.acquisition is AcquisitionState.ACQUIRED
    assert rows["10"].tracked.conversion is ConversionState.PENDING
    assert rows["20"].acquisition_attempts.count == 0
    assert len(state["transport"].requests) == 3
