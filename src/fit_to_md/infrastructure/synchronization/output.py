"""Open account-scoped output sessions and hold the interprocess lock."""

from collections.abc import Iterator
from contextlib import contextmanager
from importlib import import_module
from pathlib import Path, PurePosixPath

from fit_to_md.application.synchronization_storage import (
    FitIntegrity,
    OutputBusy,
    OutputLock,
    OutputStorage,
    RegistryReader,
    StorageFailure,
    VaultReference,
)
from fit_to_md.domain.synchronization.model import AccountIdentity, Reference
from fit_to_md.infrastructure.synchronization.files import LocalOutputFiles, path_exists
from fit_to_md.infrastructure.synchronization.registry import (
    RegistryRecoveryRequired,
    SqliteRegistry,
    connect,
    initialize,
    prepare_registry,
    verify_registry,
)

_METADATA = ".fit-to-md"
_DATABASE = "coros.sqlite3"
_LOCK = "output.lock"
_INITIALIZED = "registry.initialized"


class OutputFileLock:
    def __init__(self, path: Path) -> None:
        self._path = path

    @contextmanager
    def hold(self, *, read_only: bool = False) -> Iterator[None]:
        try:
            exists = path_exists(self._path)
        except OSError:
            raise StorageFailure("Could not inspect the COROS output lock.") from None
        if read_only and not exists:
            yield
            return

        try:
            locker = import_module("portalocker")
        except ImportError:
            raise StorageFailure(
                "COROS output locking is unavailable. Install the [coros] extra."
            ) from None
        mode = "rb" if exists else "ab"
        lock = locker.Lock(self._path, mode=mode, timeout=0, fail_when_locked=True)
        try:
            lock.acquire()
        except locker.exceptions.AlreadyLocked:
            raise OutputBusy(
                "Another synchronization is using this COROS output."
            ) from None
        except (OSError, RuntimeError, locker.exceptions.LockException):
            raise StorageFailure("Could not acquire the COROS output lock.") from None

        try:
            yield
        finally:
            try:
                lock.release()
            except (OSError, locker.exceptions.LockException):
                raise StorageFailure(
                    "Could not release the COROS output lock."
                ) from None


class SqliteOutput:
    """Open a new or existing registry under a per-output lock."""

    def __init__(self, output: Path) -> None:
        try:
            self._output = output.resolve(strict=True)
            if not self._output.is_dir():
                raise StorageFailure("The COROS output must be an existing directory.")
        except (OSError, RuntimeError):
            raise StorageFailure(
                "The COROS output must be an existing directory."
            ) from None
        self._metadata = self._output / _METADATA
        self._database = self._metadata / _DATABASE
        self._marker = self._metadata / _INITIALIZED
        self._lock_path = self._metadata / _LOCK
        self._lock: OutputLock = OutputFileLock(self._lock_path)

    @property
    def directory(self) -> Path:
        return self._output

    @property
    def registry_path(self) -> Path:
        return self._database

    def require_available(self, path: PurePosixPath) -> None:
        LocalOutputFiles(self._output).require_available(path)

    def has_fit(self, path: PurePosixPath, integrity: FitIntegrity) -> bool:
        return LocalOutputFiles(self._output).has_fit(path, integrity)

    def has_report(self, path: PurePosixPath) -> bool:
        return LocalOutputFiles(self._output).has_report(path)

    def matches_report_digest(self, path: PurePosixPath, digest: str) -> bool:
        return LocalOutputFiles(self._output).matches_report_digest(path, digest)

    def inspect_for_write(
        self, account: AccountIdentity
    ) -> tuple[bool, Reference | None]:
        """Return whether the preflight finished and the reference it observed."""
        self._prepare_metadata(create=False)
        with self._lock.hold(read_only=True):
            if not self._registry_exists():
                return True, None
            try:
                with connect(self._database, read_only=True) as connection:
                    verify_registry(connection, account)
                    return True, SqliteRegistry(connection, account).reference()
            except RegistryRecoveryRequired:
                # Let SQLite recover under the writable lock after authentication.
                return False, None

    @contextmanager
    def read(self, account: AccountIdentity) -> Iterator[RegistryReader | None]:
        """Read without creating files, even if the output has never been used."""
        self._prepare_metadata(create=False)
        with self._lock.hold(read_only=True):
            if not self._registry_exists():
                yield None
                return
            with connect(self._database, read_only=True) as connection:
                prepare_registry(connection, self._database, account, read_only=True)
                yield SqliteRegistry(connection, account)

    @contextmanager
    def write(
        self, account: AccountIdentity, vault: VaultReference
    ) -> Iterator[OutputStorage]:
        self._prepare_metadata(create=True)
        self._check_ownership_before_creating_lock(account)
        with self._lock.hold():
            if not self._registry_exists():
                initialize(self._database, account, vault)
            with connect(self._database) as connection:
                prepare_registry(connection, self._database, account, read_only=False)
                self._mark_initialized()
                yield OutputStorage(
                    SqliteRegistry(connection, account), LocalOutputFiles(self._output)
                )

    def _prepare_metadata(self, *, create: bool) -> None:
        try:
            if not path_exists(self._metadata, directory=True) and create:
                self._metadata.mkdir(exist_ok=True)
        except OSError:
            raise StorageFailure(
                "Could not access the COROS output metadata directory."
            ) from None

    def _check_ownership_before_creating_lock(self, account: AccountIdentity) -> None:
        # A restored database can exist without its original lock file.
        # Verify ownership before creating a lock in that situation.
        try:
            if path_exists(self._lock_path) or not self._registry_exists():
                return
        except OSError:
            raise StorageFailure("Could not inspect the COROS output lock.") from None
        with connect(self._database, read_only=True) as connection:
            verify_registry(connection, account)

    def _registry_exists(self) -> bool:
        try:
            exists = path_exists(self._database)
            marker = path_exists(self._marker)
            journal = path_exists(self._database.with_name(f"{_DATABASE}-journal"))
            if not exists and (
                marker or journal or any(self._metadata.glob(f"{_DATABASE}.*.bak"))
            ):
                raise StorageFailure(
                    "The COROS output registry is missing. Restore it from backup."
                )
            return exists
        except OSError:
            raise StorageFailure(
                "Could not inspect the COROS output registry."
            ) from None

    def _mark_initialized(self) -> None:
        try:
            if not path_exists(self._marker):
                self._marker.touch(exist_ok=False)
        except OSError:
            raise StorageFailure(
                "Could not mark the COROS output registry as initialized."
            ) from None
