"""Account-scoped synchronization registry backed by SQLite."""

import json
import sqlite3
from collections.abc import Callable, Iterable, Iterator
from contextlib import closing, contextmanager
from dataclasses import asdict
from datetime import datetime
from os import close
from pathlib import Path, PurePosixPath
from tempfile import mkstemp
from typing import NoReturn
from uuid import uuid4
from zoneinfo import ZoneInfo

from fit_to_md.application.synchronization_storage import (
    Attempts,
    ConversionOptions,
    FailureReason,
    FilePaths,
    FitIntegrity,
    OutputAccountMismatch,
    OutputConflict,
    RegistryEntry,
    RegistrySnapshot,
    Stage,
    StorageFailure,
    VaultReference,
)
from fit_to_md.domain.synchronization.model import (
    AccountIdentity,
    AcquisitionState,
    ActivityIdentity,
    ConversionState,
    Reference,
    RemoteActivity,
    TrackedActivity,
    as_utc,
)
from fit_to_md.infrastructure.synchronization.files import rename_new

_VERSION = 1
_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {}
_SCHEMA = (
    """CREATE TABLE schema_version (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        version INTEGER NOT NULL
    ) STRICT""",
    """CREATE TABLE output_account (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        provider TEXT NOT NULL,
        issuer TEXT NOT NULL,
        subject TEXT NOT NULL,
        vault_service TEXT NOT NULL,
        vault_entry TEXT NOT NULL,
        reference_utc TEXT,
        reference_zone TEXT,
        last_complete_discovery TEXT,
        CHECK ((reference_utc IS NULL) = (reference_zone IS NULL))
    ) STRICT""",
    """CREATE TABLE activities (
        remote_id TEXT NOT NULL,
        sport INTEGER NOT NULL,
        start_utc TEXT,
        discovered_utc TEXT NOT NULL,
        acquisition TEXT NOT NULL DEFAULT 'pending'
            CHECK (acquisition IN ('pending', 'acquired', 'failed')),
        conversion TEXT NOT NULL DEFAULT 'pending'
            CHECK (conversion IN ('pending', 'converted', 'failed', 'conversion_unsupported')),
        fit_path TEXT UNIQUE,
        report_path TEXT UNIQUE,
        fit_size INTEGER CHECK (fit_size > 0),
        fit_sha256 TEXT CHECK (length(fit_sha256) = 64),
        conversion_options TEXT,
        acquisition_count INTEGER NOT NULL DEFAULT 0 CHECK (acquisition_count >= 0),
        acquisition_at TEXT,
        acquisition_error TEXT,
        conversion_count INTEGER NOT NULL DEFAULT 0 CHECK (conversion_count >= 0),
        conversion_at TEXT,
        conversion_error TEXT,
        PRIMARY KEY (remote_id, sport),
        CHECK ((fit_path IS NULL) = (report_path IS NULL)),
        CHECK ((fit_size IS NULL) = (fit_sha256 IS NULL)),
        CHECK (fit_size IS NULL OR fit_path IS NOT NULL),
        CHECK (acquisition <> 'acquired' OR fit_size IS NOT NULL),
        CHECK (conversion <> 'converted' OR conversion_options IS NOT NULL)
    ) STRICT""",
)


class RegistryRecoveryRequired(StorageFailure):
    """SQLite must roll back an interrupted transaction in writable mode."""


def _unreadable() -> NoReturn:
    raise StorageFailure(
        "The COROS output registry is unreadable. Restore it from backup."
    )


def _utc_text(instant: datetime) -> str:
    return as_utc(instant).isoformat()


def _read_utc(value: str) -> datetime:
    return as_utc(datetime.fromisoformat(value))


def _optional_utc(value: str | None) -> datetime | None:
    return None if value is None else _read_utc(value)


def _zone_name(zone: ZoneInfo) -> str:
    if not zone.key:
        raise ValueError("A named timezone is required to store a reference.")
    return zone.key


@contextmanager
def _transaction(
    connection: sqlite3.Connection, *, write: bool = True
) -> Iterator[None]:
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield
    except (sqlite3.Error, OSError, ValueError, TypeError, LookupError, OverflowError):
        if write:
            raise StorageFailure(
                "Could not update the COROS output registry."
            ) from None
        _unreadable()


@contextmanager
def connect(path: Path, *, read_only: bool = False) -> Iterator[sqlite3.Connection]:
    try:
        # The registry uses rollback journals. A WAL database may need to create
        # shared-memory sidecars even from a read-only SQLite connection.
        with path.open("rb") as stream:
            header = stream.read(20)
        if header[18:20] != b"\x01\x01":
            _unreadable()
        mode = "ro" if read_only else "rw"
        connection = sqlite3.connect(
            f"{path.as_uri()}?mode={mode}", uri=True, timeout=0
        )
        connection.row_factory = sqlite3.Row
    except (OSError, sqlite3.Error):
        _unreadable()
    with closing(connection):
        yield connection


@contextmanager
def _temporary_database(directory: Path) -> Iterator[Path]:
    fd, name = mkstemp(dir=directory, prefix=".coros-sqlite-", suffix=".tmp")
    close(fd)
    temporary = Path(name)
    try:
        yield temporary
    finally:
        temporary.unlink(missing_ok=True)


def initialize(path: Path, account: AccountIdentity, vault: VaultReference) -> None:
    try:
        with _temporary_database(path.parent) as temporary:
            with closing(sqlite3.connect(temporary)) as connection:
                with _transaction(connection):
                    for statement in _SCHEMA:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_version VALUES (1, ?)", (_VERSION,)
                    )
                    connection.execute(
                        """INSERT INTO output_account
                           (singleton, provider, issuer, subject, vault_service, vault_entry)
                           VALUES (1, ?, ?, ?, ?, ?)""",
                        (
                            account.provider,
                            account.issuer,
                            account.subject,
                            vault.service,
                            vault.entry,
                        ),
                    )
            rename_new(temporary, path)
    except (OSError, sqlite3.Error):
        raise StorageFailure(
            "Could not initialize the COROS output registry."
        ) from None


def verify_registry(connection: sqlite3.Connection, account: AccountIdentity) -> int:
    """Check the version and ownership without changing the registry."""
    try:
        version = connection.execute(
            "SELECT version FROM schema_version WHERE singleton = 1"
        ).fetchone()
        if version is None:
            _unreadable()
        version = version[0]
        if type(version) is not int:
            _unreadable()
        if version != _VERSION and not (
            1 <= version < _VERSION
            and all(step in _MIGRATIONS for step in range(version, _VERSION))
        ):
            raise StorageFailure("Unsupported COROS output registry schema version.")
        row = connection.execute(
            "SELECT provider, issuer, subject FROM output_account WHERE singleton = 1"
        ).fetchone()
        if row is None:
            _unreadable()
        if tuple(row) != (account.provider, account.issuer, account.subject):
            raise OutputAccountMismatch(
                "This COROS output belongs to a different account."
            )
        check = connection.execute("PRAGMA quick_check").fetchone()
        if check is None or check[0] != "ok":
            _unreadable()
        return version
    except sqlite3.Error as error:
        if getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_READONLY_ROLLBACK:
            raise RegistryRecoveryRequired(
                "The COROS output registry requires rollback after an interruption."
            ) from None
        _unreadable()
    except OSError:
        _unreadable()


def prepare_registry(
    connection: sqlite3.Connection,
    path: Path,
    account: AccountIdentity,
    *,
    read_only: bool,
) -> None:
    version = verify_registry(connection, account)
    if version != _VERSION:
        if read_only:
            raise StorageFailure(
                "The COROS output registry requires a writable migration."
            )
        _migrate(connection, path, version)
    try:
        _check_tables(connection)
    except (sqlite3.Error, OSError):
        _unreadable()


def _check_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        """SELECT vault_service, vault_entry, reference_utc, reference_zone,
                  last_complete_discovery FROM output_account LIMIT 0"""
    )
    connection.execute(
        """SELECT remote_id, sport, start_utc, discovered_utc, acquisition,
                  conversion, fit_path, report_path, fit_size, fit_sha256,
                  conversion_options, acquisition_count, acquisition_at,
                  acquisition_error, conversion_count, conversion_at,
                  conversion_error FROM activities LIMIT 0"""
    )


def _migrate(connection: sqlite3.Connection, path: Path, version: int) -> None:
    try:
        with _temporary_database(path.parent) as temporary:
            with closing(sqlite3.connect(temporary)) as backup:
                connection.backup(backup)
            rename_new(temporary, path.with_name(f"{path.name}.{uuid4().hex}.bak"))
    except (sqlite3.Error, OSError):
        raise StorageFailure(
            "Could not back up the COROS output registry before migration."
        ) from None
    with _transaction(connection):
        for step in range(version, _VERSION):
            _MIGRATIONS[step](connection)
        _check_tables(connection)
        connection.execute("UPDATE schema_version SET version = ?", (_VERSION,))


class SqliteRegistry:
    """Typed account-bound operations on an already verified connection."""

    def __init__(
        self, connection: sqlite3.Connection, account: AccountIdentity
    ) -> None:
        self._connection = connection
        self._account = account

    def snapshot(self) -> RegistrySnapshot:
        with _transaction(self._connection, write=False):
            account = self._connection.execute(
                "SELECT * FROM output_account WHERE singleton = 1"
            ).fetchone()
            if account is None:
                _unreadable()
            reference = _optional_utc(account["reference_utc"])
            entries = tuple(
                self._decode_entry(row)
                for row in self._connection.execute(
                    "SELECT * FROM activities ORDER BY start_utc, remote_id, sport"
                )
            )
            return RegistrySnapshot(
                self._account,
                VaultReference(account["vault_service"], account["vault_entry"]),
                Reference(reference) if reference is not None else None,
                account["reference_zone"],
                _optional_utc(account["last_complete_discovery"]),
                entries,
            )

    def reference(self) -> Reference | None:
        with _transaction(self._connection, write=False):
            row = self._connection.execute(
                "SELECT reference_utc FROM output_account WHERE singleton = 1"
            ).fetchone()
            if row is None:
                _unreadable()
            instant = _optional_utc(row["reference_utc"])
            return Reference(instant) if instant is not None else None

    def entry(self, identity: ActivityIdentity) -> RegistryEntry:
        key = self._key(identity)
        with _transaction(self._connection, write=False):
            row = self._connection.execute(
                "SELECT * FROM activities WHERE remote_id = ? AND sport = ?", key
            ).fetchone()
            if row is None:
                raise StorageFailure(
                    "This activity is not registered in the COROS output."
                )
            return self._decode_entry(row)

    def save_discovery(
        self,
        activities: Iterable[RemoteActivity],
        *,
        discovered_at: datetime,
        complete: bool,
        reference: Reference | None = None,
        zone: ZoneInfo | None = None,
    ) -> None:
        if (reference is None) != (zone is None) or (
            reference is not None and not complete
        ):
            raise StorageFailure(
                "A discovery reference requires a complete discovery and timezone."
            )
        observed = _utc_text(discovered_at)
        rows = [
            (
                *self._key(activity.identity),
                _utc_text(activity.start_at) if activity.start_at is not None else None,
                observed,
            )
            for activity in activities
        ]
        with _transaction(self._connection):
            self._connection.executemany(
                """INSERT INTO activities (remote_id, sport, start_utc, discovered_utc)
                   VALUES (?, ?, ?, ?) ON CONFLICT (remote_id, sport) DO NOTHING""",
                rows,
            )
            if complete:
                self._connection.execute(
                    "UPDATE output_account SET last_complete_discovery = ? WHERE singleton = 1",
                    (observed,),
                )
            if reference is not None and zone is not None:
                self._save_reference(reference, zone)

    def save_reference(self, reference: Reference, zone: ZoneInfo) -> None:
        with _transaction(self._connection):
            self._save_reference(reference, zone)

    def _save_reference(self, reference: Reference, zone: ZoneInfo) -> None:
        self._connection.execute(
            """UPDATE output_account SET reference_utc = ?, reference_zone = ?
               WHERE singleton = 1""",
            (_utc_text(reference.instant), _zone_name(zone)),
        )

    def reserve(self, identity: ActivityIdentity, paths: FilePaths) -> None:
        key = self._key(identity)
        with _transaction(self._connection):
            row = self._connection.execute(
                "SELECT fit_path, report_path FROM activities WHERE remote_id = ? AND sport = ?",
                key,
            ).fetchone()
            if row is None:
                raise StorageFailure(
                    "This activity is not registered in the COROS output."
                )
            reserved = (paths.fit.as_posix(), paths.report.as_posix())
            if tuple(row) == reserved:
                return
            if row["fit_path"] is not None:
                raise StorageFailure(
                    "This activity already has different COROS output paths."
                )
            try:
                self._connection.execute(
                    """UPDATE activities SET fit_path = ?, report_path = ?
                       WHERE remote_id = ? AND sport = ?""",
                    (*reserved, *key),
                )
            except sqlite3.IntegrityError:
                raise OutputConflict(
                    f"COROS output is reserved by another activity: {paths.fit.name}"
                ) from None

    def record_attempt(
        self, identity: ActivityIdentity, stage: Stage, at: datetime
    ) -> None:
        prefix = stage.value
        condition = (
            "acquisition = 'acquired' AND conversion <> 'conversion_unsupported'"
            if stage is Stage.CONVERSION
            else "fit_path IS NOT NULL AND acquisition <> 'acquired'"
        )
        self._update(
            identity,
            f"{prefix}_count = {prefix}_count + 1, {prefix}_at = ?",
            (_utc_text(at),),
            condition,
        )

    def record_failure(
        self, identity: ActivityIdentity, stage: Stage, reason: FailureReason
    ) -> None:
        if stage is Stage.ACQUISITION:
            self._update(
                identity,
                "acquisition = 'failed', acquisition_error = ?",
                (reason.value,),
                "acquisition <> 'acquired'",
            )
        else:
            state = (
                ConversionState.UNSUPPORTED
                if reason is FailureReason.UNSUPPORTED
                else ConversionState.FAILED
            )
            self._update(
                identity,
                "conversion = ?, conversion_error = ?",
                (state, reason.value),
                "acquisition = 'acquired' AND conversion <> 'conversion_unsupported'",
            )

    def record_conversion_attempt(
        self, identity: ActivityIdentity, at: datetime, options: ConversionOptions
    ) -> None:
        self._update(
            identity,
            "conversion = 'pending', conversion_count = conversion_count + 1, "
            "conversion_at = ?, conversion_options = ?, conversion_error = NULL",
            (_utc_text(at), json.dumps(asdict(options), allow_nan=False)),
            "acquisition = 'acquired' AND conversion <> 'conversion_unsupported'",
        )

    def record_fit_integrity(
        self, identity: ActivityIdentity, integrity: FitIntegrity
    ) -> None:
        self._update(
            identity,
            "fit_size = ?, fit_sha256 = ?",
            (integrity.size, integrity.sha256),
            "fit_path IS NOT NULL AND acquisition <> 'acquired'",
        )

    def record_acquired(self, identity: ActivityIdentity) -> None:
        self._update(
            identity,
            "acquisition = 'acquired', acquisition_error = NULL",
            (),
            "fit_size IS NOT NULL",
        )

    def discard_fit_integrity(self, identity: ActivityIdentity) -> None:
        self._update(
            identity,
            "fit_size = NULL, fit_sha256 = NULL",
            (),
            "acquisition <> 'acquired'",
        )

    def reset_acquisition(self, identity: ActivityIdentity) -> None:
        self._update(
            identity,
            "acquisition = 'pending', fit_size = NULL, fit_sha256 = NULL, "
            "acquisition_error = NULL",
            (),
            "acquisition = 'acquired'",
        )

    def record_converted(
        self, identity: ActivityIdentity, options: ConversionOptions
    ) -> None:
        self._update(
            identity,
            "conversion = 'converted', conversion_options = ?, conversion_error = NULL",
            (json.dumps(asdict(options), allow_nan=False),),
            "acquisition = 'acquired' AND conversion <> 'conversion_unsupported'",
        )

    def _update(
        self,
        identity: ActivityIdentity,
        assignments: str,
        parameters: tuple[str | int, ...],
        condition: str,
    ) -> None:
        key = self._key(identity)
        with _transaction(self._connection):
            cursor = self._connection.execute(
                f"""UPDATE activities SET {assignments}
                    WHERE remote_id = ? AND sport = ? AND ({condition})""",
                (*parameters, *key),
            )
            if cursor.rowcount != 1:
                raise StorageFailure(
                    "Cannot update this activity in its current COROS registry state."
                )

    def _key(self, identity: ActivityIdentity) -> tuple[str, int]:
        if identity.account != self._account:
            raise OutputAccountMismatch(
                "This activity belongs to a different COROS account."
            )
        return identity.remote_id, identity.sport

    def _decode_entry(self, row: sqlite3.Row) -> RegistryEntry:
        identity = ActivityIdentity(self._account, row["remote_id"], row["sport"])
        tracked = TrackedActivity(
            RemoteActivity(identity, _optional_utc(row["start_utc"])),
            AcquisitionState(row["acquisition"]),
            ConversionState(row["conversion"]),
        )
        paths = (
            FilePaths(PurePosixPath(row["fit_path"]), PurePosixPath(row["report_path"]))
            if row["fit_path"] is not None
            else None
        )
        integrity = (
            FitIntegrity(row["fit_size"], row["fit_sha256"])
            if row["fit_size"] is not None
            else None
        )
        options = None
        if row["conversion_options"] is not None:
            values = json.loads(row["conversion_options"])
            if values["hr_zone_boundaries"] is not None:
                values["hr_zone_boundaries"] = tuple(values["hr_zone_boundaries"])
            options = ConversionOptions(**values)
        return RegistryEntry(
            tracked,
            _read_utc(row["discovered_utc"]),
            paths,
            integrity,
            options,
            _read_attempts(row, Stage.ACQUISITION),
            _read_attempts(row, Stage.CONVERSION),
        )


def _read_attempts(row: sqlite3.Row, stage: Stage) -> Attempts:
    prefix = stage.value
    error = row[f"{prefix}_error"]
    return Attempts(
        row[f"{prefix}_count"],
        _optional_utc(row[f"{prefix}_at"]),
        FailureReason(error) if error is not None else None,
    )
