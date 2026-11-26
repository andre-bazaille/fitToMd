"""Store COROS credentials in explicitly selected OS vaults."""

import json
import math
import re
import sys
from contextlib import closing, suppress
from dataclasses import asdict
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast
from uuid import uuid4

from fit_to_md.application.synchronization_quota import QuotaBinding, QuotaUnavailable
from fit_to_md.application.synchronization_storage import VaultReference
from fit_to_md.domain.coros_connection import (
    ConnectionFailure,
    ConnectionRecord,
    CorosAccount,
    Session,
    Verification,
)
from fit_to_md.domain.synchronization.model import AccountIdentity
from fit_to_md.infrastructure.synchronization.quota import quota_account_key

_PROBE_SERVICE = "fit-to-md/coros-prototype"
_CONNECTION_SERVICE = "fit-to-md/coros/connection"
_QUOTA_SERVICE = "fit-to-md/coros/quota/"
_ENTRY = "session"
_SCHEMA_VERSION = 1
_WINDOWS_BLOB_LIMIT = 2560
_WINDOWS_PART_SIZE = _WINDOWS_BLOB_LIMIT // 2
_WINDOWS_MAX_PARTS = 64
_WINDOWS_PART_PREFIX = _CONNECTION_SERVICE + "/parts/"
_WINDOWS_MANIFEST = re.compile(r"parts:([0-9a-f]{32}):([1-9][0-9]?)")
_SECRET_SERVICE_INTERFACE = "org.freedesktop.Secret."


def _production_service(service: str) -> bool:
    return service == _CONNECTION_SERVICE or service.startswith(_QUOTA_SERVICE)


class VaultBackend(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...
    def set_password(self, service: str, username: str, password: str) -> None: ...
    def delete_password(self, service: str, username: str) -> None:
        """Delete the credential if present."""
        ...


class _SecretService:
    """Require an existing unlocked collection; never request an unlock."""

    def __init__(self, module: ModuleType) -> None:
        self._module = module

    def _operate(
        self, service: str, username: str, operation: str, password: str = ""
    ) -> str | None:
        with closing(self._module.dbus_init()) as bus:
            collection = self._module.Collection(bus)
            if collection.is_locked():
                raise ConnectionFailure(
                    "Unlock the system vault before running COROS commands."
                )
            attributes = {"service": service, "username": username}
            item = next(collection.search_items(attributes), None)
            if item is not None and item.is_locked():
                raise ConnectionFailure(
                    "The COROS entry in the system vault is locked."
                )
            if operation == "get":
                return None if item is None else cast(bytes, item.get_secret()).decode()
            if operation == "set":
                if item is None:
                    if _production_service(service):
                        self._create_without_prompt(
                            bus, collection.collection_path, attributes, password
                        )
                    else:
                        collection.create_item(
                            service, attributes, password.encode(), replace=True
                        )
                else:
                    item.set_secret(password.encode())
            elif item is not None:
                if service == _CONNECTION_SERVICE:
                    util = import_module("secretstorage.util")
                    address = util.DBusAddressWrapper(
                        item.item_path, _SECRET_SERVICE_INTERFACE + "Item", bus
                    )
                    (prompt,) = address.call("Delete", "")
                    if prompt != "/":
                        raise ConnectionFailure(
                            "The system vault requires interaction to remove the COROS session."
                        )
                else:
                    item.delete()
            return None

    @staticmethod
    def _create_without_prompt(
        bus: object, collection_path: str, attributes: dict[str, str], password: str
    ) -> None:
        # The high-level SecretStorage method starts the returned prompt itself.
        util = import_module("secretstorage.util")
        address = util.DBusAddressWrapper(
            collection_path, _SECRET_SERVICE_INTERFACE + "Collection", bus
        )
        secret = util.format_secret(
            util.open_session(bus), password.encode(), "text/plain"
        )
        item_path, prompt = address.call(
            "CreateItem",
            "a{sv}(oayays)b",
            {
                _SECRET_SERVICE_INTERFACE + "Item.Label": ("s", attributes["service"]),
                _SECRET_SERVICE_INTERFACE + "Item.Attributes": ("a{ss}", attributes),
            },
            secret,
            True,
        )
        if item_path == "/" or prompt != "/":
            raise ConnectionFailure(
                "The system vault requires interaction to save the COROS entry."
            )

    def get_password(self, service: str, username: str) -> str | None:
        return self._operate(service, username, "get")

    def set_password(self, service: str, username: str, password: str) -> None:
        self._operate(service, username, "set", password)

    def delete_password(self, service: str, username: str) -> None:
        self._operate(service, username, "delete")


def system_backend(*, extra: str = "coros-probe") -> VaultBackend:
    try:
        match sys.platform:
            case "linux":
                return _SecretService(import_module("secretstorage"))
            case "win32":
                module = import_module("keyring.backends.Windows")
                backend = module.WinVaultKeyring()
                backend.persist = "local machine"
            case _:
                raise ConnectionFailure("COROS commands support Linux and Windows.")
        if backend.priority <= 0:
            raise ConnectionFailure("No supported system vault is available.")
        return _WindowsVault(cast(VaultBackend, backend), module.win32cred)
    except ConnectionFailure:
        raise
    except Exception:
        raise ConnectionFailure(
            f"System vault unavailable. Install the [{extra}] extra and unlock "
            "Secret Service (Linux) or Credential Manager (Windows)."
        ) from None


class _WindowsVault:
    def __init__(self, backend: VaultBackend, credentials: ModuleType) -> None:
        self._backend = backend
        self._credentials = credentials

    def get_password(self, service: str, username: str) -> str | None:
        value = self._backend.get_password(service, username)
        if service != _CONNECTION_SERVICE:
            return value

        # Status and account inspection can read while a writer replaces the session.
        for _ in range(3):
            if value is None or not value.startswith("parts:"):
                return value
            manifest = _WINDOWS_MANIFEST.fullmatch(value)
            if manifest is None or int(manifest[2]) > _WINDOWS_MAX_PARTS:
                break
            parts = [
                self._backend.get_password(target, username)
                for target in self._part_targets(manifest[1], int(manifest[2]))
            ]
            if all(parts):
                return "".join(part for part in parts if part is not None)
            updated = self._backend.get_password(service, username)
            if updated == value:
                break
            value = updated
        raise ConnectionFailure(
            "Could not read the complete COROS session in Windows Credential Manager. "
            "If the error persists, run 'fit-to-md coros logout' and log in again."
        )

    def set_password(self, service: str, username: str, password: str) -> None:
        if not _production_service(service):
            self._backend.set_password(service, username, password)
            return

        if service == _CONNECTION_SERVICE:
            self._save_connection(username, password)
        else:
            self._write(service, username, password)

    def _save_connection(self, username: str, value: str) -> None:
        # Connection JSON is ASCII, so each character takes two bytes in CredWrite.
        targets: list[str] = []
        if len(value) > _WINDOWS_PART_SIZE:
            parts = [
                value[start : start + _WINDOWS_PART_SIZE]
                for start in range(0, len(value), _WINDOWS_PART_SIZE)
            ]
            if len(parts) > _WINDOWS_MAX_PARTS:
                raise ConnectionFailure("The COROS session is too large to store.")
            generation = uuid4().hex
            targets = self._part_targets(generation, len(parts))
            for target, part in zip(targets, parts, strict=True):
                self._write(target, username, part)
            value = f"parts:{generation}:{len(parts)}"

        # Publish only after every part is saved. Writers hold the connection lock.
        self._write(_CONNECTION_SERVICE, username, value)
        # Cleanup can be retried on the next save or logout. The new record is committed.
        with suppress(Exception):
            self._delete_parts(username, keep=set(targets))

    @staticmethod
    def _part_targets(generation: str, count: int) -> list[str]:
        return [f"{_WINDOWS_PART_PREFIX}{generation}/{index}" for index in range(count)]

    def _delete_parts(self, username: str, *, keep: set[str]) -> None:
        credentials = self._credentials
        try:
            entries = credentials.CredEnumerate(_WINDOWS_PART_PREFIX + "*", 0)
        except Exception as error:
            if getattr(error, "winerror", None) == 1168:  # ERROR_NOT_FOUND
                return
            raise
        for entry in entries or ():
            target = entry["TargetName"]
            if (
                entry["Type"] == credentials.CRED_TYPE_GENERIC
                and entry["UserName"] == username
                and target.startswith(_WINDOWS_PART_PREFIX)
                and target not in keep
            ):
                self._backend.delete_password(target, username)

    def _write(self, service: str, username: str, password: str) -> None:
        # Keyring's multi-user collision handling can back up the old password
        # under a second target. Each production entry has its own target.
        credentials = self._credentials
        credentials.CredWrite(
            {
                "Type": credentials.CRED_TYPE_GENERIC,
                "TargetName": service,
                "UserName": username,
                "CredentialBlob": password,
                "Persist": credentials.CRED_PERSIST_LOCAL_MACHINE,
            },
            0,
        )

    def delete_password(self, service: str, username: str) -> None:
        if self._backend.get_password(service, username) is not None:
            self._backend.delete_password(service, username)
        if service == _CONNECTION_SERVICE:
            self._delete_parts(username, keep=set())


class _VaultEntry:
    def __init__(self, service: str, backend: VaultBackend) -> None:
        self._service = service
        self._backend = backend

    def read(self) -> str | None:
        try:
            return self._backend.get_password(self._service, _ENTRY)
        except ConnectionFailure:
            raise
        except Exception:
            raise ConnectionFailure("Could not read the system vault.") from None

    def write(self, value: str) -> None:
        try:
            self._backend.set_password(self._service, _ENTRY, value)
        except ConnectionFailure:
            raise
        except Exception:
            raise ConnectionFailure(
                "Could not save the COROS session in the system vault."
            ) from None

    def clear(self) -> None:
        try:
            self._backend.delete_password(self._service, _ENTRY)
        except ConnectionFailure:
            raise
        except Exception:
            raise ConnectionFailure(
                "Could not remove the COROS session from the system vault."
            ) from None


def _decode_session(data: object) -> Session:
    if not isinstance(data, dict):
        raise ValueError
    client_id = data["client_id"]
    access = data["access_token"]
    refresh = data["refresh_token"]
    expires = data["expires_at"]
    if not isinstance(client_id, str) or not client_id:
        raise ValueError
    if (
        not isinstance(access, str)
        or not access
        or any(not 33 <= ord(character) <= 126 for character in access)
    ):
        raise ValueError
    if not isinstance(refresh, str) or not refresh:
        raise ValueError
    if type(expires) not in (int, float) or not math.isfinite(expires):
        raise ValueError
    return Session(client_id, access, refresh, expires)


class VaultSessionStore:
    def __init__(self, region: str, backend: VaultBackend) -> None:
        self._entry = _VaultEntry(f"{_PROBE_SERVICE}/{region}", backend)

    def load(self) -> Session | None:
        value = self._entry.read()
        if value is None:
            return None

        try:
            return _decode_session(json.loads(value))
        except (ValueError, OverflowError, TypeError, KeyError, RecursionError):
            raise ConnectionFailure(
                "Invalid COROS session in the system vault; run logout."
            ) from None

    def save(self, session: Session) -> None:
        self._entry.write(json.dumps(asdict(session)))

    def clear(self) -> None:
        self._entry.clear()


class VaultConnectionStore:
    """Keep the active account and credentials together in a versioned entry."""

    reference = VaultReference(_CONNECTION_SERVICE, _ENTRY)

    def __init__(self, backend: VaultBackend) -> None:
        self._entry = _VaultEntry(_CONNECTION_SERVICE, backend)

    def load(self) -> ConnectionRecord | None:
        value = self._entry.read()
        if value is None:
            return None

        try:
            data = json.loads(value)
            version = data["version"]
            if type(version) is not int or version != _SCHEMA_VERSION:
                raise ValueError
            identity = data["account"]
            region, issuer, subject = (
                identity["region"],
                identity["issuer"],
                identity["subject"],
            )
            if (
                not isinstance(region, str)
                or not isinstance(issuer, str)
                or not isinstance(subject, str)
            ):
                raise ValueError
            return ConnectionRecord(
                CorosAccount(region, issuer, subject),
                _decode_session(data["session"]),
                Verification(data["verification"]),
            )
        except (ValueError, OverflowError, TypeError, KeyError, RecursionError):
            raise ConnectionFailure(
                "Invalid or unsupported COROS session in the system vault. "
                "Run 'fit-to-md coros logout' to remove it."
            ) from None

    def save(self, record: ConnectionRecord) -> None:
        value = json.dumps(
            {"version": _SCHEMA_VERSION, **asdict(record)}, separators=(",", ":")
        )
        self._entry.write(value)

    def clear(self) -> None:
        self._entry.clear()


class VaultQuotaBindings:
    """Keep quota identity and location independently of the COROS session."""

    def __init__(self, backend: VaultBackend) -> None:
        self._backend = backend

    def load(self, account: AccountIdentity) -> QuotaBinding | None:
        try:
            value = self._backend.get_password(self._service(account), "binding")
        except Exception:
            raise QuotaUnavailable(
                "Could not read the COROS quota binding from the system vault."
            ) from None
        if value is None:
            return None
        try:
            data = json.loads(value)
            path, registry_id, active = (
                data["path"],
                data["registry_id"],
                data["active"],
            )
            if (
                type(data["version"]) is not int
                or data["version"] != _SCHEMA_VERSION
                or data["account"] != asdict(account)
                or not isinstance(path, str)
                or "\x00" in path
                or not Path(path).is_absolute()
                or not isinstance(registry_id, str)
                or re.fullmatch(r"[0-9a-f]{32}", registry_id) is None
                or type(active) is not bool
            ):
                raise ValueError
            return QuotaBinding(account, Path(path), registry_id, active)
        except (ValueError, TypeError, KeyError, RecursionError):
            raise QuotaUnavailable(
                "Invalid COROS quota binding in the system vault. Restore reliable quota state."
            ) from None

    def save(self, binding: QuotaBinding) -> None:
        value = json.dumps(
            {"version": _SCHEMA_VERSION, **asdict(binding), "path": str(binding.path)},
            separators=(",", ":"),
        )
        if (
            sys.platform == "win32"
            and len(value.encode("utf-16-le")) > _WINDOWS_BLOB_LIMIT
        ):
            raise QuotaUnavailable(
                "The COROS quota binding exceeds the Windows Credential Manager limit."
            )
        try:
            self._backend.set_password(self._service(binding.account), "binding", value)
        except Exception:
            raise QuotaUnavailable(
                "Could not save the COROS quota binding in the system vault."
            ) from None

    @staticmethod
    def _service(account: AccountIdentity) -> str:
        return _QUOTA_SERVICE + quota_account_key(account)
