from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from fit_to_md.application.synchronization import to_account_identity
from fit_to_md.application.synchronization_quota import QuotaBinding
from fit_to_md.domain.coros_connection import (
    ISSUERS,
    ConnectionFailure,
    ConnectionRecord,
    CorosAccount,
    Session,
    Verification,
)
from fit_to_md.infrastructure import coros_vault
from fit_to_md.infrastructure.coros_vault import (
    VaultConnectionStore,
    VaultQuotaBindings,
    VaultSessionStore,
)
from tests.support.coros import FakeVault

RECORD = ConnectionRecord(
    CorosAccount("eu", ISSUERS["eu"], "subject"),
    Session("client", "access", "refresh", 1000),
)


@pytest.mark.parametrize("prompt_action", [None, "CreateItem", "Delete"])
def test_secret_service_uses_unprompted_create_and_delete(
    monkeypatch: pytest.MonkeyPatch, prompt_action: str | None
) -> None:
    bus = SimpleNamespace(close=lambda: None)
    entry: list[bytes] = []
    methods: list[str] = []

    def change(secret: bytes) -> None:
        entry[:] = [secret]

    item = SimpleNamespace(
        item_path="/item",
        is_locked=lambda: False,
        get_secret=lambda: entry[0],
        set_secret=change,
    )
    collection = SimpleNamespace(
        collection_path="/collection",
        is_locked=lambda: False,
        search_items=lambda _: iter((item,) if entry else ()),
        create_item=lambda *_: pytest.fail("prompt-capable create called"),
    )

    def call(method: str, signature: str, *args: object) -> tuple[str, ...]:
        methods.append(method)
        if method == "CreateItem":
            assert signature == "a{sv}(oayays)b"
            if method == prompt_action:
                return "/", "/prompt"
            assert isinstance(args[1], bytes)
            change(args[1])
            return "/item", "/"
        assert method == "Delete" and not signature
        if method == prompt_action:
            return ("/prompt",)
        entry.clear()
        return ("/",)

    secretstorage = SimpleNamespace(
        dbus_init=lambda: bus, Collection=lambda _: collection
    )
    util = SimpleNamespace(
        DBusAddressWrapper=lambda *_: SimpleNamespace(call=call),
        open_session=lambda _: "session",
        format_secret=lambda _session, secret, _kind: secret,
    )
    monkeypatch.setattr(coros_vault, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(
        coros_vault,
        "import_module",
        {"secretstorage": secretstorage, "secretstorage.util": util}.__getitem__,
    )
    store = VaultConnectionStore(coros_vault.system_backend(extra="coros"))
    assert store.load() is None

    if prompt_action == "CreateItem":
        with pytest.raises(ConnectionFailure, match="requires interaction to save"):
            store.save(RECORD)
        assert store.load() is None and methods == ["CreateItem"]
        return

    store.save(RECORD)
    store.save(RECORD)
    assert store.load() == RECORD and methods == ["CreateItem"]
    if prompt_action == "Delete":
        with pytest.raises(ConnectionFailure, match="requires interaction to remove"):
            store.clear()
        assert store.load() == RECORD
    else:
        store.clear()
        assert store.load() is None
    assert methods == ["CreateItem", "Delete"]


@pytest.fixture
def windows_vault(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[coros_vault.VaultBackend, FakeVault, list[str]]:
    class WinVault(FakeVault):
        priority = 1

    keyring = WinVault()
    native_writes: list[str] = []

    def cred_write(credential: dict[str, str | int], flags: int) -> None:
        assert flags == 0 and credential["Type"] == 1 and credential["Persist"] == 2
        service, username, password = (
            str(credential["TargetName"]),
            str(credential["UserName"]),
            str(credential["CredentialBlob"]),
        )
        assert len(password.encode("utf-16-le")) <= 2560
        native_writes.append(service)
        keyring.set_password(service, username, password)

    def cred_enumerate(pattern: str, flags: int) -> list[dict[str, str | int]]:
        assert flags == 0 and pattern.endswith("*")
        return [
            {"TargetName": service, "UserName": username, "Type": 1}
            for service, username in keyring.values
            if service.startswith(pattern[:-1])
        ]

    native = SimpleNamespace(
        CRED_TYPE_GENERIC=1,
        CRED_PERSIST_LOCAL_MACHINE=2,
        CredWrite=cred_write,
        CredEnumerate=cred_enumerate,
    )
    win_module = SimpleNamespace(WinVaultKeyring=lambda: keyring, win32cred=native)
    monkeypatch.setattr(coros_vault, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(
        coros_vault,
        "import_module",
        {"keyring.backends.Windows": win_module}.__getitem__,
    )
    return coros_vault.system_backend(extra="coros"), keyring, native_writes


def test_windows_production_overwrites_one_native_target_and_isolates_probe(
    windows_vault: tuple[coros_vault.VaultBackend, FakeVault, list[str]],
    tmp_path: Path,
) -> None:
    backend, keyring, native_writes = windows_vault
    store = VaultConnectionStore(backend)
    probe = VaultSessionStore("eu", backend)
    probe.save(RECORD.session)
    store.save(RECORD)
    store.save(RECORD)
    assert native_writes == ["fit-to-md/coros/connection"] * 2
    assert len(keyring.values) == 2 and store.load() == RECORD

    quotas = VaultQuotaBindings(backend)
    pending = QuotaBinding(
        to_account_identity(RECORD.account), tmp_path / "quota.sqlite3", "a" * 32, False
    )
    quotas.save(pending)
    active = replace(pending, active=True)
    quotas.save(active)
    assert native_writes[2] == native_writes[3] and native_writes[2].startswith(
        "fit-to-md/coros/quota/"
    )
    assert len(keyring.values) == 3 and quotas.load(active.account) == active
    store.clear()
    assert store.load() is None and probe.load() == RECORD.session
    assert quotas.load(active.account) == active


def test_windows_large_connection_reads_rotates_and_removes_parts(
    windows_vault: tuple[coros_vault.VaultBackend, FakeVault, list[str]],
) -> None:
    backend, keyring, writes = windows_vault
    store = VaultConnectionStore(backend)
    store.save(RECORD)
    slot = (store.reference.service, store.reference.entry)
    boundary_token = "s" * (1280 - len(keyring.values[slot]) + len("refresh"))
    boundary = replace(
        RECORD, session=replace(RECORD.session, refresh_token=boundary_token)
    )
    store.save(boundary)
    assert len(keyring.values[slot].encode("utf-16-le")) == 2560
    assert store.load() == boundary and len(keyring.values) == 1

    large = replace(
        boundary, session=replace(boundary.session, access_token="a" * 3000)
    )
    store.save(large)
    assert writes[-1] == slot[0]
    assert len(keyring.values) > 2 and VaultConnectionStore(backend).load() == large
    old_parts = set(keyring.values) - {slot}

    renewed = replace(
        large,
        session=replace(large.session, refresh_token="r" * 3000),
        verification=Verification.PENDING,
    )
    store.save(renewed)
    assert store.load() == renewed and old_parts.isdisjoint(keyring.values)
    store.save(RECORD)
    assert store.load() == RECORD and set(keyring.values) == {slot}
    store.save(large)
    store.clear()
    store.clear()
    assert store.load() is None and not keyring.values


@pytest.mark.parametrize("fail_at_manifest", [False, True])
def test_windows_interrupted_large_write_preserves_previous_session(
    windows_vault: tuple[coros_vault.VaultBackend, FakeVault, list[str]],
    monkeypatch: pytest.MonkeyPatch,
    fail_at_manifest: bool,
) -> None:
    backend, keyring, _ = windows_vault
    store = VaultConnectionStore(backend)
    large = replace(RECORD, session=replace(RECORD.session, access_token="a" * 3500))
    store.save(large)
    previous = keyring.values.copy()
    original_write = keyring.set_password

    def fail_write(service: str, username: str, password: str) -> None:
        failure_target = store.reference.service if fail_at_manifest else "/1"
        if service.endswith(failure_target):
            raise OSError("private write error")
        original_write(service, username, password)

    updated = replace(large, verification=Verification.PENDING)
    with monkeypatch.context() as patch:
        patch.setattr(keyring, "set_password", fail_write)
        with pytest.raises(ConnectionFailure, match="Could not save the COROS session"):
            store.save(updated)
    assert store.load() == large and previous.items() < keyring.values.items()
    store.save(updated)
    assert store.load() == updated and len(keyring.values) == len(previous)


def test_windows_missing_part_fails_closed_and_logout_cleans_remaining_parts(
    windows_vault: tuple[coros_vault.VaultBackend, FakeVault, list[str]],
) -> None:
    backend, keyring, _ = windows_vault
    store = VaultConnectionStore(backend)
    store.save(
        replace(RECORD, session=replace(RECORD.session, access_token="a" * 3500))
    )
    part = next(slot for slot in keyring.values if slot[0].endswith("/1"))
    del keyring.values[part]
    with pytest.raises(
        ConnectionFailure, match="Could not read the complete COROS session"
    ):
        store.load()
    store.clear()
    assert store.load() is None and not keyring.values


def test_windows_read_retries_when_session_is_replaced_during_read(
    windows_vault: tuple[coros_vault.VaultBackend, FakeVault, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, keyring, _ = windows_vault
    store = VaultConnectionStore(backend)
    store.save(
        replace(RECORD, session=replace(RECORD.session, access_token="a" * 3500))
    )
    first_part = next(
        service for service, _ in keyring.values if service.endswith("/0")
    )
    original_read = keyring.get_password

    def read_after_rotation(service: str, username: str) -> str | None:
        if service == first_part:
            store.save(RECORD)
        return original_read(service, username)

    monkeypatch.setattr(keyring, "get_password", read_after_rotation)
    assert store.load() == RECORD
