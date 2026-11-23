import base64
import json
import multiprocessing
import sqlite3
import sys
from contextlib import closing, nullcontext
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from multiprocessing.queues import Queue
from pathlib import Path
from threading import Barrier
from time import monotonic
from zoneinfo import ZoneInfo

import pytest

from fit_to_md.application.synchronization import to_account_identity
from fit_to_md.application.synchronization_quota import (
    AccountQuotas,
    BudgetedFitRequests,
    QuotaBinding,
    QuotaUnavailable,
)
from fit_to_md.application.synchronization_storage import VaultReference
from fit_to_md.domain.coros_connection import ISSUERS, CorosAccount, Session
from fit_to_md.domain.synchronization.model import (
    AccountIdentity,
    ActivityIdentity,
    RemoteActivity,
)
from fit_to_md.domain.synchronization.quota import (
    DailyCalls,
    FitQuotaExceeded,
    ServerFitQuotaExceeded,
)
from fit_to_md.infrastructure.coros_gateway import CorosGateway, Response
from fit_to_md.infrastructure.coros_vault import VaultQuotaBindings
from fit_to_md.infrastructure.synchronization import quota
from fit_to_md.infrastructure.synchronization.output import SqliteOutput
from fit_to_md.infrastructure.synchronization.quota import SqliteQuotaStorage
from tests.support.coros import FakeTransport, FakeVault, reply

pytestmark = pytest.mark.skipif(
    sys.platform not in {"linux", "win32"}, reason="COROS supports Linux and Windows"
)
COROS_ACCOUNT = CorosAccount("eu", ISSUERS["eu"], "opaque-subject")
ACCOUNT = to_account_identity(COROS_ACCOUNT)
OTHER = AccountIdentity("coros", "other-issuer", ACCOUNT.subject)
NOW = datetime(2026, 10, 24, 21, 59, tzinfo=UTC)
PARIS = ZoneInfo("Europe/Paris")
UTC_ZONE = ZoneInfo("UTC")


@dataclass
class FixedClock:
    instant: datetime = NOW

    def now(self) -> datetime:
        return self.instant


@pytest.fixture
def quota_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state = tmp_path / "state # é"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.setenv("LOCALAPPDATA", str(state))
    return state


def test_window_daily_counters_restart_and_isolation(
    quota_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FixedClock()
    vault = FakeVault()
    bindings = VaultQuotaBindings(vault)
    storage = SqliteQuotaStorage(clock)
    quotas = AccountQuotas(bindings, storage, nullcontext)

    quotas.initialize(ACCOUNT)
    binding = bindings.load(ACCOUNT)
    assert binding is not None and binding.path.is_relative_to(quota_state)
    registry = quotas.open(ACCOUNT)
    for _ in range(49):
        registry.reserve_fit()
    registry.record_catalogue_call()
    registry.reserve_fit()

    # Cross midnight in Paris, then record both occurrences of 02:30 at DST end.
    for hour in (0, 1):
        clock.instant = datetime(2026, 10, 25, hour, 30, tzinfo=UTC)
        registry.record_catalogue_call()
    assert registry.daily_calls(NOW.date(), PARIS) == DailyCalls(catalogue=1, fit=50)
    assert registry.daily_calls(clock.instant.date(), PARIS) == DailyCalls(catalogue=2)
    with pytest.raises(FitQuotaExceeded) as error:
        registry.reserve_fit()
    assert error.value.retry_at == NOW + timedelta(days=1)

    new_state = quota_state.with_name("new-state")
    monkeypatch.setenv("XDG_STATE_HOME", str(new_state))
    monkeypatch.setenv("LOCALAPPDATA", str(new_state))
    reopened = AccountQuotas(
        VaultQuotaBindings(vault), SqliteQuotaStorage(clock), nullcontext
    )
    reopened.initialize(ACCOUNT)
    assert bindings.load(ACCOUNT) == binding and not new_state.exists()
    quotas.initialize(OTHER)
    other = quotas.open(OTHER)
    other.reserve_fit()
    assert other.daily_calls(clock.instant.date(), PARIS) == DailyCalls(fit=1)

    registry = reopened.open(ACCOUNT)
    clock.instant = NOW + timedelta(days=1, microseconds=-1)
    with pytest.raises(FitQuotaExceeded):
        registry.reserve_fit()
    clock.instant += timedelta(microseconds=1)
    registry.reserve_fit()
    assert registry.daily_calls(clock.instant.date(), PARIS) == DailyCalls(
        catalogue=2, fit=1
    )
    assert registry.daily_calls(NOW.date(), UTC_ZONE) == DailyCalls(catalogue=1, fit=50)

    clock.instant = NOW - timedelta(seconds=1)
    with pytest.raises(FitQuotaExceeded) as error:
        registry.reserve_fit()
    assert error.value.retry_at == NOW + timedelta(days=1)


@pytest.mark.parametrize(
    "failure",
    [
        "lost-db",
        "foreign-db",
        "same-account-db",
        "version",
        "lost-vault",
        "vault-offline",
    ],
)
def test_broken_quota_stays_closed_after_24_hours(
    quota_state: Path, failure: str
) -> None:
    vault = FakeVault()
    clock = FixedClock()
    storage = SqliteQuotaStorage(clock)
    bindings = VaultQuotaBindings(vault)
    quotas = AccountQuotas(bindings, storage, nullcontext)
    quotas.initialize(ACCOUNT)
    registry = quotas.open(ACCOUNT)
    registry.reserve_fit()
    path = storage.initial_path(ACCOUNT)

    match failure:
        case "lost-db":
            path.unlink()
            with pytest.raises(QuotaUnavailable):
                registry.reserve_fit()
        case "foreign-db":
            quotas.initialize(OTHER)
            storage.initial_path(OTHER).replace(path)
        case "same-account-db":
            current = bindings.load(ACCOUNT)
            assert current is not None
            replacement = replace(
                current, path=path.with_suffix(".new"), registry_id="f" * 32
            )
            storage.create(replacement)
            replacement.path.replace(path)
        case "version":
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("PRAGMA user_version = 2")
        case "lost-vault":
            vault.values.clear()
        case "vault-offline":
            vault.available = False

    clock.instant += timedelta(days=2)
    before = path.read_bytes() if path.exists() else None
    with pytest.raises(QuotaUnavailable):
        quotas.open(ACCOUNT)
    with pytest.raises(QuotaUnavailable):
        quotas.initialize(ACCOUNT)
    assert before == (path.read_bytes() if path.exists() else None)


@pytest.mark.parametrize("failure", ["unverified-pending", "db-publish", "activate"])
def test_initialization_failure_preserves_pending_binding(
    quota_state: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    class UnreliableVault(FakeVault):
        def get_password(self, service: str, username: str) -> str | None:
            value = super().get_password(service, username)
            return None if failure == "unverified-pending" and intercept else value

        def set_password(self, service: str, username: str, password: str) -> None:
            if intercept and failure == "activate" and json.loads(password)["active"]:
                raise OSError("private detail")
            super().set_password(service, username, password)

    intercept = True
    vault = UnreliableVault()
    storage = SqliteQuotaStorage(FixedClock())
    bindings = VaultQuotaBindings(vault)
    quotas = AccountQuotas(bindings, storage, nullcontext)
    path = storage.initial_path(ACCOUNT)

    def fail_publish(source: Path, destination: Path) -> None:
        assert bindings.load(ACCOUNT) is not None
        assert source.parent == destination.parent and destination == path
        raise OSError("private detail")

    if failure == "db-publish":
        monkeypatch.setattr(quota, "rename_new", fail_publish)

    with pytest.raises(QuotaUnavailable) as error:
        quotas.initialize(ACCOUNT)
    assert "private detail" not in str(error.value)
    intercept = False
    binding = bindings.load(ACCOUNT)
    assert binding is not None and not binding.active and binding.path == path
    assert path.exists() is (failure == "activate")
    assert not list(quota_state.rglob("*.tmp"))
    with pytest.raises(QuotaUnavailable, match="binding is incomplete"):
        quotas.open(ACCOUNT)
    if failure == "activate":
        registry = storage.open(replace(binding, active=True))
        registry.reserve_fit()
        registry.record_catalogue_call()
        before = path.read_bytes()
        quotas.initialize(ACCOUNT)
        assert bindings.load(ACCOUNT) == replace(binding, active=True)
        assert path.read_bytes() == before
        assert quotas.open(ACCOUNT).daily_calls(NOW.date(), UTC_ZONE) == DailyCalls(
            catalogue=1, fit=1
        )
    else:
        with pytest.raises(QuotaUnavailable):
            quotas.initialize(ACCOUNT)
        assert bindings.load(ACCOUNT) == binding and not path.exists()


@pytest.mark.parametrize("mismatch", ["account", "registry-id"])
def test_pending_binding_rejects_an_inconsistent_database(
    quota_state: Path, mismatch: str
) -> None:
    storage = SqliteQuotaStorage(FixedClock())
    bindings = VaultQuotaBindings(FakeVault())
    quotas = AccountQuotas(bindings, storage, nullcontext)
    path = storage.initial_path(ACCOUNT)
    pending = QuotaBinding(ACCOUNT, path, "1" * 32, active=False)
    bindings.save(pending)
    stored = (
        replace(pending, account=OTHER)
        if mismatch == "account"
        else replace(pending, registry_id="2" * 32)
    )
    storage.create(stored)
    before = path.read_bytes()

    with pytest.raises(QuotaUnavailable):
        quotas.initialize(ACCOUNT)
    assert bindings.load(ACCOUNT) == pending
    assert path.read_bytes() == before


def test_sqlite_timeout_and_failed_insert_do_not_consume_units(
    quota_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = SqliteQuotaStorage(FixedClock())
    quotas = AccountQuotas(VaultQuotaBindings(FakeVault()), storage, nullcontext)
    quotas.initialize(ACCOUNT)
    registry = quotas.open(ACCOUNT)
    path = storage.initial_path(ACCOUNT)
    with closing(sqlite3.connect(path)) as locked:
        locked.execute("BEGIN IMMEDIATE")
        start = monotonic()
        with pytest.raises(QuotaUnavailable):
            registry.reserve_fit()
        assert 0.8 <= monotonic() - start < 5
    assert registry.daily_calls(NOW.date(), UTC_ZONE) == DailyCalls()

    connect = sqlite3.connect

    class FailingConnection(sqlite3.Connection):
        def execute(
            self, sql: str, parameters: tuple[object, ...] = ()
        ) -> sqlite3.Cursor:
            cursor = super().execute(sql, parameters)
            if sql.startswith("INSERT INTO quota_calls"):
                raise sqlite3.OperationalError("Interrupted before commit")
            return cursor

    with monkeypatch.context() as patch:
        patch.setattr(
            quota.sqlite3,
            "connect",
            lambda *args, **kwargs: connect(*args, **kwargs, factory=FailingConnection),
        )
        with pytest.raises(QuotaUnavailable):
            registry.reserve_fit()
    assert registry.daily_calls(NOW.date(), UTC_ZONE) == DailyCalls()
    registry.reserve_fit()
    assert registry.daily_calls(NOW.date(), UTC_ZONE) == DailyCalls(fit=1)


def test_guard_records_committed_attempts_and_keeps_local_fits_available(
    quota_state: Path,
) -> None:
    pytest.importorskip("portalocker")
    clock = FixedClock()
    quotas = AccountQuotas(
        VaultQuotaBindings(FakeVault()), SqliteQuotaStorage(clock), nullcontext
    )
    quotas.initialize(ACCOUNT)
    ledger = quotas.open(ACCOUNT)
    fit = (
        Path(__file__).resolve().parents[3] / "fit_files" / "coros_synthetic.fit"
    ).read_bytes()
    output = quota_state / "output"
    output.mkdir()
    identities = [
        ActivityIdentity(ACCOUNT, label, 100)
        for label in ("local", "success", "timeout", "quota", "deferred")
    ]
    activities = tuple(
        RemoteActivity(identity, NOW + timedelta(minutes=index))
        for index, identity in enumerate(identities)
    )
    local, success, lost, limited, deferred = identities

    def timeout(_: object) -> Response:
        assert quotas.open(ACCOUNT).daily_calls(NOW.date(), UTC_ZONE) == DailyCalls(
            fit=2
        )
        raise TimeoutError

    transport = FakeTransport(
        reply({"id": 1, "result": {"protocolVersion": "2025-06-18"}}),
        reply(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "result": {
                    "content": [
                        {
                            "type": "resource",
                            "resource": {"blob": base64.b64encode(fit).decode()},
                        }
                    ]
                },
            }
        ),
        timeout,
        Response(429, {"Retry-After": "90"}, b""),
    )
    gateway = CorosGateway(
        "eu",
        transport,
        now=NOW.timestamp,
        monotonic=lambda: 0,
        sleep=lambda _: pytest.fail("Unexpected wait"),
    )
    request = gateway.prepare_fit_download(
        COROS_ACCOUNT, Session("client", "access", "refresh", NOW.timestamp() + 3600)
    )
    with SqliteOutput(output).write(
        ACCOUNT, VaultReference("COROS", "session")
    ) as opened:
        opened.registry.save_discovery(activities, discovered_at=NOW, complete=True)
        opened.reserve(local, UTC_ZONE)
        opened.publish_fit(local, fit)
        guard = BudgetedFitRequests(ledger, opened.registry, opened.files, request)
        assert guard.request(local) is None
        assert guard.request(success) == fit
        with pytest.raises(TimeoutError):
            guard.request(lost)
        with pytest.raises(ServerFitQuotaExceeded):
            guard.request(limited)
        with pytest.raises(ServerFitQuotaExceeded) as error:
            guard.request(deferred)
        assert error.value.retry_at == NOW + timedelta(seconds=90)
        assert guard.request(local) is None

    assert len(transport.requests) == 4
    assert quotas.open(ACCOUNT).daily_calls(NOW.date(), UTC_ZONE) == DailyCalls(fit=3)


def _reserve_from_output(
    binding: QuotaBinding, output: Path, start: Barrier, results: Queue
) -> None:
    vault = VaultQuotaBindings(FakeVault())
    vault.save(binding)
    quotas = AccountQuotas(vault, SqliteQuotaStorage(FixedClock()), nullcontext)
    with SqliteOutput(output).write(ACCOUNT, VaultReference("COROS", "session")):
        registry = quotas.open(ACCOUNT)
        start.wait(timeout=10)
        try:
            registry.reserve_fit()
        except FitQuotaExceeded:
            results.put("full")
        else:
            results.put("reserved")


def test_two_processes_and_outputs_compete_for_one_remaining_unit(
    quota_state: Path,
) -> None:
    pytest.importorskip("portalocker")
    bindings = VaultQuotaBindings(FakeVault())
    quotas = AccountQuotas(bindings, SqliteQuotaStorage(FixedClock()), nullcontext)
    quotas.initialize(ACCOUNT)
    binding = bindings.load(ACCOUNT)
    assert binding is not None
    registry = quotas.open(ACCOUNT)
    for _ in range(49):
        registry.reserve_fit()

    ctx = multiprocessing.get_context("spawn")
    start = ctx.Barrier(2)
    with closing(ctx.Queue()) as results:
        children = []
        try:
            for name in ("output-a", "output-b"):
                output = quota_state / name
                output.mkdir()
                child = ctx.Process(
                    target=_reserve_from_output, args=(binding, output, start, results)
                )
                child.start()
                children.append(child)
            assert {results.get(timeout=15), results.get(timeout=15)} == {
                "full",
                "reserved",
            }
            for child in children:
                child.join(timeout=5)
                assert child.exitcode == 0
        finally:
            for child in children:
                if child.is_alive():
                    child.terminate()
                    child.join(timeout=5)
    assert registry.daily_calls(NOW.date(), UTC_ZONE) == DailyCalls(fit=50)


def test_state_directory_fallback_and_windows_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = SqliteQuotaStorage(FixedClock())
    with monkeypatch.context() as patch:
        patch.setattr(quota.sys, "platform", "linux")
        patch.setenv("XDG_STATE_HOME", "relative")
        patch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        assert (
            storage.initial_path(ACCOUNT).parent
            == tmp_path / ".local" / "state" / "fit-to-md" / "coros"
        )
    with monkeypatch.context() as patch:
        patch.setattr(quota.sys, "platform", "win32")
        patch.delenv("LOCALAPPDATA", raising=False)
        with pytest.raises(QuotaUnavailable, match="state directory"):
            storage.initial_path(ACCOUNT)


@pytest.mark.parametrize(
    "fields",
    [
        {"active": 1},
        {"path": "relative"},
        {"registry_id": "bad"},
        {"version": 2},
        {"account": {}},
    ],
)
def test_invalid_vault_binding_is_refused(
    tmp_path: Path, fields: dict[str, object]
) -> None:
    vault = FakeVault()
    bindings = VaultQuotaBindings(vault)
    binding = QuotaBinding(ACCOUNT, tmp_path / "quota.sqlite3", "a" * 32, True)
    bindings.save(binding)
    assert bindings.load(ACCOUNT) == binding
    slot, value = next(iter(vault.values.items()))
    vault.values[slot] = json.dumps({**json.loads(value), **fields})
    with pytest.raises(QuotaUnavailable, match="Invalid COROS quota binding"):
        bindings.load(ACCOUNT)
