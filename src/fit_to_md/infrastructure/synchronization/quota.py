"""The account-wide quota ledger, independent of output databases."""

import hashlib
import json
import os
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from tempfile import mkstemp
from zoneinfo import ZoneInfo

from fit_to_md.application.synchronization import Clock
from fit_to_md.application.synchronization_quota import QuotaBinding, QuotaUnavailable
from fit_to_md.application.synchronization_storage import StorageFailure
from fit_to_md.domain.synchronization.model import AccountIdentity, as_utc
from fit_to_md.domain.synchronization.quota import (
    FIT_WINDOW,
    DailyCalls,
    require_fit_capacity,
)
from fit_to_md.infrastructure.synchronization.files import path_exists, rename_new

_VERSION = 1
_APPLICATION_ID = 0x46544D51
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MICROSECOND = timedelta(microseconds=1)
_DAY_US = timedelta(days=1) // _MICROSECOND
_UNAVAILABLE = (
    "Could not use the shared COROS quota registry. Restore reliable quota state "
    "if the registry is missing or damaged."
)
_SCHEMA = (
    """CREATE TABLE quota_account (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        provider TEXT NOT NULL, issuer TEXT NOT NULL, subject TEXT NOT NULL,
        registry_id TEXT NOT NULL
    ) STRICT""",
    """CREATE TABLE quota_calls (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL CHECK (kind IN ('catalogue', 'fit')),
        at_us INTEGER NOT NULL
    ) STRICT""",
    "CREATE INDEX quota_calls_by_kind_and_time ON quota_calls (kind, at_us)",
    "CREATE INDEX quota_calls_by_time ON quota_calls (at_us)",
)


def quota_account_key(account: AccountIdentity) -> str:
    parts = (account.provider, account.issuer, account.subject)
    return hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()


def _timestamp(instant: datetime) -> int:
    return (as_utc(instant) - _EPOCH) // _MICROSECOND


def _instant(value: int) -> datetime:
    return _EPOCH + timedelta(microseconds=value)


def _identity(binding: QuotaBinding) -> tuple[str, str, str, str]:
    account = binding.account
    return account.provider, account.issuer, account.subject, binding.registry_id


class SqliteQuotaStorage:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    def initial_path(self, account: AccountIdentity) -> Path:
        try:
            match sys.platform:
                case "linux":
                    state = Path(os.environ.get("XDG_STATE_HOME", ""))
                    if not state.is_absolute():
                        state = Path.home() / ".local" / "state"
                case "win32":
                    state = Path(os.environ.get("LOCALAPPDATA", ""))
                case _:
                    raise QuotaUnavailable(
                        "COROS quota storage supports Linux and Windows."
                    )
        except (OSError, RuntimeError, ValueError):
            raise QuotaUnavailable(
                "Could not resolve the COROS quota state directory."
            ) from None
        if not state.is_absolute():
            raise QuotaUnavailable("Could not resolve the COROS quota state directory.")
        return state / "fit-to-md" / "coros" / f"{quota_account_key(account)}.sqlite3"

    def exists(self, path: Path) -> bool:
        try:
            return path_exists(path)
        except (OSError, StorageFailure, ValueError):
            raise QuotaUnavailable(_UNAVAILABLE) from None

    def create(self, binding: QuotaBinding) -> None:
        path = binding.path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, name = mkstemp(dir=path.parent, prefix=".quota-", suffix=".tmp")
            temporary = Path(name)
            try:
                os.close(fd)
                with closing(sqlite3.connect(temporary)) as connection, connection:
                    connection.execute("PRAGMA synchronous = FULL")
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute(f"PRAGMA application_id = {_APPLICATION_ID}")
                    connection.execute(f"PRAGMA user_version = {_VERSION}")
                    for statement in _SCHEMA:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO quota_account VALUES (1, ?, ?, ?, ?)",
                        _identity(binding),
                    )
                rename_new(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        except (OSError, StorageFailure, sqlite3.Error, ValueError):
            raise QuotaUnavailable(
                "Could not create the shared COROS quota registry."
            ) from None

    def open(self, binding: QuotaBinding) -> "SqliteQuotaRegistry":
        registry = SqliteQuotaRegistry(binding, self._clock)
        registry.verify()
        return registry


class SqliteQuotaRegistry:
    def __init__(self, binding: QuotaBinding, clock: Clock) -> None:
        self._binding = binding
        self._clock = clock

    @property
    def account(self) -> AccountIdentity:
        return self._binding.account

    @contextmanager
    def _transaction(self, *, write: bool = True) -> Iterator[sqlite3.Connection]:
        path = self._binding.path
        try:
            if (
                not self._binding.active
                or not path.is_absolute()
                or not path_exists(path)
            ):
                raise QuotaUnavailable(_UNAVAILABLE)
            uri = f"{path.as_uri()}?mode=rw"
            with (
                closing(sqlite3.connect(uri, uri=True, timeout=1)) as connection,
                connection,
            ):
                if write:
                    connection.execute("PRAGMA synchronous = FULL")
                connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                if (
                    connection.execute("PRAGMA application_id").fetchone()
                    != (_APPLICATION_ID,)
                    or connection.execute("PRAGMA user_version").fetchone()
                    != (_VERSION,)
                    or connection.execute("PRAGMA journal_mode").fetchone()
                    != ("delete",)
                    or connection.execute(
                        "SELECT provider, issuer, subject, registry_id FROM quota_account "
                        "WHERE singleton = 1"
                    ).fetchone()
                    != _identity(self._binding)
                ):
                    raise QuotaUnavailable(_UNAVAILABLE)
                yield connection
        except QuotaUnavailable:
            raise
        except (OSError, StorageFailure, OverflowError, sqlite3.Error, ValueError):
            raise QuotaUnavailable(_UNAVAILABLE) from None

    def verify(self) -> None:
        with self._transaction(write=False) as connection:
            connection.execute("SELECT kind, at_us FROM quota_calls LIMIT 0")
            if connection.execute("PRAGMA quick_check(1)").fetchone() != ("ok",):
                raise QuotaUnavailable(_UNAVAILABLE)

    def reserve_fit(self) -> None:
        with self._transaction() as connection:
            now = _timestamp(self._clock.now())
            cutoff = now - FIT_WINDOW // _MICROSECOND
            active = connection.execute(
                "SELECT at_us FROM quota_calls WHERE kind = 'fit' AND at_us > ? ORDER BY at_us",
                (cutoff,),
            )
            require_fit_capacity([_instant(at_us) for (at_us,) in active])
            connection.execute(
                "INSERT INTO quota_calls (kind, at_us) VALUES ('fit', ?)", (now,)
            )

    def record_catalogue_call(self) -> None:
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO quota_calls (kind, at_us) VALUES ('catalogue', ?)",
                (_timestamp(self._clock.now()),),
            )

    def daily_calls(self, day: date, zone: ZoneInfo) -> DailyCalls:
        # A timezone offset is strictly smaller than a day. Filter a UTC superset
        # by local date to cover even transitions that cross local midnight.
        midnight = (day - _EPOCH.date()).days * _DAY_US
        counts = {"catalogue": 0, "fit": 0}
        with self._transaction(write=False) as connection:
            calls = connection.execute(
                "SELECT kind, at_us FROM quota_calls WHERE at_us >= ? AND at_us < ?",
                (midnight - _DAY_US, midnight + 2 * _DAY_US),
            )
            for kind, at_us in calls:
                if kind not in counts:
                    raise QuotaUnavailable(_UNAVAILABLE)
                if _instant(at_us).astimezone(zone).date() == day:
                    counts[kind] += 1
        return DailyCalls(**counts)
