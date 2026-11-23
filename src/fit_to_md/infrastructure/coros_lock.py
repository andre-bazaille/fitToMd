"""Interprocess exclusion for operations on the active COROS session."""

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import import_module
from pathlib import Path

from fit_to_md.domain.coros_connection import ConnectionFailure


def _session_lock_path() -> Path:
    if sys.platform == "win32":
        state = Path(os.environ.get("LOCALAPPDATA", ""))
        if not state.is_absolute():
            raise ConnectionFailure(
                "LocalAppData is unavailable for the COROS session lock."
            )
    else:
        # Use the same lock for the user even when outputs or XDG settings differ.
        state = Path.home() / ".local" / "state"
    return state / "fit-to-md" / "coros" / "connection.lock"


class SessionFileLock:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path

    @contextmanager
    def hold(self) -> Iterator[None]:
        try:
            locker = import_module("portalocker")
        except ImportError:
            raise ConnectionFailure(
                "COROS session locking is unavailable. Install the [coros] extra."
            ) from None

        try:
            path = self._path if self._path is not None else _session_lock_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            lock = locker.Lock(path, mode="a", timeout=0, fail_when_locked=True)
            lock.acquire()
        except locker.exceptions.AlreadyLocked:
            raise ConnectionFailure(
                "Another command is using the COROS session. Try again later."
            ) from None
        except (OSError, RuntimeError, locker.exceptions.LockException):
            raise ConnectionFailure(
                "Could not acquire the COROS session lock."
            ) from None

        try:
            yield
        finally:
            try:
                lock.release()
            except (OSError, locker.exceptions.LockException):
                raise ConnectionFailure(
                    "Could not release the COROS session lock."
                ) from None
