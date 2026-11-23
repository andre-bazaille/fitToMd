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


def test_windows_production_overwrites_one_native_target_and_isolates_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
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
        native_writes.append(service)
        keyring.set_password(service, username, password)

    native = SimpleNamespace(
        CRED_TYPE_GENERIC=1, CRED_PERSIST_LOCAL_MACHINE=2, CredWrite=cred_write
    )
    win_module = SimpleNamespace(WinVaultKeyring=lambda: keyring, win32cred=native)
    monkeypatch.setattr(coros_vault, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(
        coros_vault,
        "import_module",
        {"keyring.backends.Windows": win_module}.__getitem__,
    )
    backend = coros_vault.system_backend(extra="coros")
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
