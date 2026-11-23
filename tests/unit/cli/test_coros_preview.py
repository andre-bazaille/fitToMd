import json
from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
from io import StringIO
from pathlib import Path
from urllib.request import Request
from zoneinfo import ZoneInfo

import pytest

from fit_to_md import cli, coros_cli
from fit_to_md.application.synchronization import to_account_identity
from fit_to_md.application.synchronization_storage import (
    ConversionOptions,
    VaultReference,
)
from fit_to_md.domain.coros_connection import (
    ISSUERS,
    ConnectionRecord,
    CorosAccount,
    Session,
)
from fit_to_md.domain.synchronization.model import (
    ActivityIdentity,
    Reference,
    RemoteActivity,
)
from fit_to_md.infrastructure.coros_gateway import Response
from fit_to_md.infrastructure.coros_vault import VaultConnectionStore
from fit_to_md.infrastructure.local_time import SystemLocalTimezone
from fit_to_md.infrastructure.synchronization.output import SqliteOutput
from tests.support.coros import (
    FakeTransport,
    FakeVault,
    catalogue_reply,
    reply,
    rpc_reply,
)

LAUNCH = datetime(2026, 11, 21, 12, tzinfo=UTC)
COROS_ACCOUNT = CorosAccount("eu", ISSUERS["eu"], "private-subject")
ACCOUNT = to_account_identity(COROS_ACCOUNT)
INIT = rpc_reply(1, {"protocolVersion": "2025-06-18"})
FIT = Path(__file__).resolve().parents[2] / "fit_files" / "coros_synthetic.fit"


class Uncontended:
    hold = staticmethod(nullcontext)


def setup(
    monkeypatch: pytest.MonkeyPatch,
    transport: FakeTransport,
    *,
    zone: str = "Europe/Paris",
    signed_in: bool = True,
    renew: bool = False,
) -> FakeVault:
    backend = FakeVault()
    if signed_in:
        expires = LAUNCH if renew else LAUNCH + timedelta(hours=1)
        VaultConnectionStore(backend).save(
            ConnectionRecord(
                COROS_ACCOUNT,
                Session("client", "old-token", "old-refresh", expires.timestamp()),
            )
        )
    monkeypatch.setattr(coros_cli, "system_backend", lambda **_: backend)
    monkeypatch.setattr(coros_cli, "SessionFileLock", Uncontended)
    monkeypatch.setattr(coros_cli, "HttpsTransport", lambda: transport)
    monkeypatch.setattr(coros_cli._UtcClock, "now", lambda _: LAUNCH)
    monkeypatch.setattr(coros_cli.time, "time", LAUNCH.timestamp)
    monkeypatch.setattr(SystemLocalTimezone, "resolve", lambda _: ZoneInfo(zone))
    monkeypatch.setattr(
        coros_cli.webbrowser, "open", lambda *_: pytest.fail("unexpected browser")
    )
    return backend


def run(path: Path, *args: str) -> tuple[int, str, str]:
    stdout, stderr = StringIO(), StringIO()
    code = cli.run(
        ["coros", "sync", "--dry-run", "--output", str(path), *args],
        stdout=stdout,
        stderr=stderr,
    )
    return code, stdout.getvalue(), stderr.getvalue()


def row(label: str, start: datetime, *, sport: int = 100) -> tuple[str, int, str]:
    timestamp = int(start.timestamp())
    return label, sport, f"startTimestamp={timestamp} | endTimestamp={timestamp + 3600}"


def tree(path: Path) -> dict[Path, bytes | None]:
    return {
        child.relative_to(path): child.read_bytes() if child.is_file() else None
        for child in path.rglob("*")
    }


def test_preview_empty_output_and_exact_filtering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    start = datetime(2026, 11, 20, 23, tzinfo=UTC)
    transport = FakeTransport(
        INIT,
        catalogue_reply(
            date(2026, 11, 19),
            date(2026, 11, 22),
            [
                row("734958132649", LAUNCH),
                row("734958132648", start + timedelta(minutes=1), sport=102),
                row("734958132647", start - timedelta(seconds=1)),
                row("734958132646", start),
            ],
            encoded=True,
        ),
    )
    backend = setup(monkeypatch, transport)
    before = backend.values.copy()
    code, out, err = run(tmp_path, "--since", "2026-11-21", "--download-only")
    assert code == 0 and err == ""
    assert "Period [2026-11-21T00:00:00+01:00, 2026-11-21T13:00:00+01:00)" in out
    assert "Europe/Paris" in out and "Catalogue coverage: complete" in out
    assert "Known: 0" in out and "New: 2" in out and "Estimated FIT requests: 2" in out
    assert out.index("sport 100") < out.index("sport 102")
    assert out.count("(acquire)") == 2 and "convert" not in out
    assert "7349581326" not in out and "Private activity title" not in out
    assert not tree(tmp_path)
    assert backend.values == before
    init, query = [json.loads(request.data or b"") for request in transport.requests]
    assert (
        init["method"] == "initialize"
        and query["params"]["name"] == "querySportRecords"
    )


@pytest.mark.parametrize("partial", [False, True])
def test_incremental_preview_preserves_real_registry_and_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, partial: bool
) -> None:
    pytest.importorskip("portalocker")
    ref = Reference(datetime(2026, 11, 21, 0, 30, tzinfo=UTC))
    old = RemoteActivity(
        ActivityIdentity(ACCOUNT, "11", 100), LAUNCH - timedelta(days=3)
    )
    known_at = datetime(2026, 11, 21, 6, tzinfo=UTC)
    known = RemoteActivity(ActivityIdentity(ACCOUNT, "12", 100), known_at)
    zone = ZoneInfo("Europe/Paris")
    output = SqliteOutput(tmp_path)
    with output.write(
        ACCOUNT, VaultReference("fit-to-md/coros/connection", "session")
    ) as opened:
        opened.registry.save_discovery(
            (old, known),
            discovered_at=ref.instant,
            complete=True,
            reference=ref,
            zone=zone,
        )
        paths = opened.reserve(known.identity, zone)
        opened.publish_fit(known.identity, FIT.read_bytes())
        opened.publish_report(known.identity, "# Preserved report", ConversionOptions())
        snapshot = opened.registry.snapshot()
    before = tree(tmp_path)

    def subrange(request: Request) -> Response:
        assert request.get_header("Authorization") == "Bearer old-token"
        return catalogue_reply(
            date(2026, 11, 21) if partial else date(2026, 11, 19),
            date(2026, 11, 22),
            [row("12", known_at)],
        )

    replies = (
        [
            catalogue_reply(
                date(2026, 11, 19),
                date(2026, 11, 22),
                [row(str(100 + n), known_at) for n in range(20)],
            ),
            reply({"message": "private server response"}, 400),
            subrange,
        ]
        if partial
        else [subrange]
    )
    transport = FakeTransport(INIT, *replies)
    backend = setup(monkeypatch, transport, zone="America/New_York")
    saved = backend.values.copy()
    code, out, err = run(tmp_path)
    assert code == int(partial)
    assert "Period [2026-11-20T00:00:00-05:00, 2026-11-21T07:00:00-05:00)" in out
    assert "Known: 1" in out and "New: 0" in out and "Resume: 1" in out
    assert "Estimated FIT requests: 1" in out
    assert out.index("2026-11-18T07:00:00-05:00") < out.index(
        "2026-11-21T01:00:00-05:00"
    )
    assert ("incomplete (provisional plan)" in out) is partial
    assert ("HTTP 400" in err) is partial and "private" not in err
    with output.read(ACCOUNT) as reader:
        assert reader is not None and reader.snapshot() == snapshot
    assert tree(tmp_path) == before
    assert (tmp_path / paths.report).read_text() == "# Preserved report"
    assert backend.values == saved
    calls = [json.loads(request.data or b"") for request in transport.requests][1:]
    assert len(calls) == (3 if partial else 1)
    assert all(call["params"]["name"] == "querySportRecords" for call in calls)


def test_preview_detects_conflicts_without_creating_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "fits").mkdir()
    occupied = tmp_path / "fits" / "2026-11-21 12-30.fit"
    occupied.write_bytes(b"manual copy")
    first = LAUNCH - timedelta(hours=1)
    competing = LAUNCH - timedelta(minutes=30)
    transport = FakeTransport(
        INIT,
        catalogue_reply(
            date(2026, 11, 19),
            date(2026, 11, 22),
            [row("1", first), row("2", first, sport=101), row("3", competing)],
        ),
    )
    setup(monkeypatch, transport)
    before = tree(tmp_path)
    code, out, err = run(tmp_path, "--last-days", "1")
    assert code == 0 and err == ""
    assert "Conflicts: 2" in out and "Estimated FIT requests: 1" in out
    assert "conflict at fits/2026-11-21 12-00.fit" in out
    assert "conflict at fits/2026-11-21 12-30.fit" in out
    assert tree(tmp_path) == before


def test_preview_renews_session_before_querying_empty_catalogue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    issuer = COROS_ACCOUNT.issuer

    def confirm(request: Request) -> Response:
        assert request.get_header("Authorization") == "Bearer new-token"
        stored = VaultConnectionStore(backend).load()
        assert stored is not None and stored.session.refresh_token == "new-refresh"
        return reply({"sub": COROS_ACCOUNT.subject})

    transport = FakeTransport(
        reply(
            {
                "token_type": "Bearer",
                "access_token": "new-token",
                "refresh_token": "new-refresh",
                "expires_in": 3600,
            }
        ),
        reply({"issuer": issuer, "userinfo_endpoint": issuer + "/connect/userinfo"}),
        confirm,
        INIT,
        catalogue_reply(date(2026, 11, 19), date(2026, 11, 22), []),
    )
    backend = setup(monkeypatch, transport, renew=True)
    code, out, err = run(tmp_path, "--last-days", "1")
    assert code == 0 and err == ""
    assert "New: 0" in out and "Estimated FIT requests: 0" in out
    assert not tree(tmp_path)
    assert transport.requests[-1].get_header("Authorization") == "Bearer new-token"


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--last-days", "0"], "positive integer"),
        (["--since", "2026-02-30"], "valid date"),
        (["--since", "2026-11-22"], "future"),
        (["--since", "0001-01-01"], "cannot be covered"),
    ],
)
def test_invalid_selections_never_load_vault_or_call_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: list[str], message: str
) -> None:
    setup(monkeypatch, FakeTransport(), zone="UTC")
    monkeypatch.setattr(
        coros_cli, "_build_session_manager", lambda: pytest.fail("access to vault")
    )
    code, out, err = run(tmp_path, *args)
    assert code == 2 and out == "" and message in err
    assert not tree(tmp_path)


def test_missing_output_and_reference_before_network_or_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = FakeTransport()
    backend = setup(monkeypatch, transport, renew=True)
    before = backend.values.copy()
    code, out, err = run(tmp_path / "missing", "--last-days", "1")
    assert code == 2 and not out and "existing directory" in err
    code, out, err = run(tmp_path)
    assert code == 2 and not out and "--last-days 7 --output DIR" in err
    assert not transport.requests and not tree(tmp_path)
    assert backend.values == before


def test_disconnected_preview_requires_explicit_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup(monkeypatch, FakeTransport(), signed_in=False)
    code, out, err = run(tmp_path, "--last-days", "1")
    assert code == 1 and not out and "coros login" in err
    assert not tree(tmp_path)


def test_preview_reports_unclassifiable_activities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = FakeTransport(
        INIT,
        catalogue_reply(
            date(2026, 11, 19),
            date(2026, 11, 22),
            [("1", 100, "startTimestamp=bad"), row("2", LAUNCH - timedelta(minutes=1))],
        ),
    )
    setup(monkeypatch, transport)
    code, out, err = run(tmp_path, "--last-days", "1")
    assert code == 1 and "New: 1" in out
    assert "provisional plan" in out and "contradictory timestamps: 1" in err
    assert not tree(tmp_path)


def test_preview_reports_retry_deadline_without_waiting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = FakeTransport(INIT, Response(429, {"Retry-After": "60"}, b""))
    setup(monkeypatch, transport)
    monkeypatch.setattr(
        coros_cli.time, "sleep", lambda _: pytest.fail("unexpected wait")
    )
    code, out, err = run(tmp_path, "--last-days", "1")
    assert code == 1 and "provisional plan" in out
    assert "HTTP 429" in err and "Retry after 2026-11-21T12:01:00+00:00." in err


@pytest.mark.parametrize(
    "args",
    [
        ["--dry-run"],
        ["--dry-run", "--output", ".", "--last-days", "one"],
        ["--dry-run", "--output", ".", "--last-days", "1", "--since", "2026-11-21"],
    ],
)
def test_preview_parser_rejects_unsupported_invocations(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], args: list[str]
) -> None:
    monkeypatch.setattr(
        coros_cli, "_build_session_manager", lambda: pytest.fail("access to vault")
    )
    with pytest.raises(SystemExit) as error:
        cli.run(["coros", "sync", *args])
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert not captured.out and "error" in captured.err


def test_preview_interrupt_returns_130(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupt(_request: Request) -> Response:
        raise KeyboardInterrupt

    setup(monkeypatch, FakeTransport(INIT, interrupt))
    code, out, err = run(tmp_path, "--last-days", "1")
    assert code == 130 and not out and "interrupted" in err
    assert not tree(tmp_path)
