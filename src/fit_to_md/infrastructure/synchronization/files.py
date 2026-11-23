"""Stage, validate and publish files within one output."""

import ctypes
import errno
import hashlib
import os
import stat
import struct
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from tempfile import mkstemp
from uuid import uuid4

from fitdecode.utils import compute_crc

from fit_to_md.application.synchronization_storage import (
    FitIntegrity,
    InvalidFit,
    OutputConflict,
    StorageFailure,
)


def path_exists(path: Path, *, directory: bool = False) -> bool:
    """Check managed paths without following symlinks or junctions."""
    try:
        mode = path.lstat().st_mode
        if path.is_junction() or not (
            stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)
        ):
            raise StorageFailure("A managed COROS path has an unexpected file type.")
    except FileNotFoundError:
        return False
    return True


def rename_new(source: Path, destination: Path) -> None:
    """Atomically publish a sibling path, with no replacement on either OS."""
    try:
        if sys.platform == "win32":
            os.rename(source, destination)
        elif sys.platform == "linux":
            _rename_linux(source, destination)
        else:
            raise StorageFailure("COROS output storage supports Linux and Windows.")
    except FileExistsError:
        raise OutputConflict(
            f"COROS output already exists: {destination.name}"
        ) from None


def _rename_linux(source: Path, destination: Path) -> None:
    try:
        renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
    except AttributeError:
        raise StorageFailure("Atomic COROS file publication is unavailable.") from None
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    at_fdcwd = -100
    rename_noreplace = 1
    if (
        renameat2(
            at_fdcwd,
            os.fsencode(source),
            at_fdcwd,
            os.fsencode(destination),
            rename_noreplace,
        )
        != 0
    ):
        code = ctypes.get_errno()
        if code in (errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP, errno.EXDEV):
            raise StorageFailure(
                "This volume does not support atomic COROS file publication."
            )
        raise OSError(code, os.strerror(code))


@contextmanager
def _stage(destination: Path, contents: bytes) -> Iterator[Path]:
    fd, name = mkstemp(dir=destination.parent, prefix=".fit-to-md-", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        yield temporary
    finally:
        temporary.unlink(missing_ok=True)


def _validate_fit(contents: bytes) -> FitIntegrity:
    if len(contents) < 14 or contents[8:12] != b".FIT":
        raise InvalidFit("FIT signature or header is invalid.")
    header_size = contents[0]
    body_size = int.from_bytes(contents[4:8], "little")
    if (
        header_size not in (12, 14)
        or body_size == 0
        or header_size + body_size + 2 != len(contents)
    ):
        raise InvalidFit("FIT length does not match its header.")
    if header_size >= 14:
        (header_crc,) = struct.unpack_from("<H", contents, header_size - 2)
        if header_crc and header_crc != compute_crc(contents, end=header_size - 2):
            raise InvalidFit("FIT header checksum is invalid.")
    (file_crc,) = struct.unpack_from("<H", contents, len(contents) - 2)
    if file_crc != compute_crc(contents, end=len(contents) - 2):
        raise InvalidFit("FIT file checksum is invalid.")
    return FitIntegrity(len(contents), hashlib.sha256(contents).hexdigest())


@dataclass(frozen=True)
class _StagedFit:
    temporary: Path
    destination: Path
    integrity: FitIntegrity

    def publish(self) -> None:
        try:
            rename_new(self.temporary, self.destination)
        except OSError:
            raise StorageFailure("Could not publish the staged COROS FIT.") from None


class LocalOutputFiles:
    def __init__(self, output: Path) -> None:
        self._output = output

    def local_fit(self, path: PurePosixPath) -> Path:
        try:
            return self._destination(path)
        except OSError:
            raise StorageFailure("Could not access the COROS FIT.") from None

    def has_report(self, path: PurePosixPath) -> bool:
        try:
            destination = self._destination(path)
            return (
                stat.S_ISREG(destination.lstat().st_mode)
                and not destination.is_junction()
            )
        except FileNotFoundError:
            return False
        except OSError:
            raise StorageFailure("Could not inspect the COROS report.") from None

    def require_available(self, path: PurePosixPath) -> None:
        try:
            destination = self._destination(path)
            try:
                destination.lstat()
            except FileNotFoundError:
                return
        except OSError:
            raise StorageFailure(
                "Could not inspect the COROS output destination."
            ) from None
        raise OutputConflict(f"COROS output already exists: {destination.name}")

    @contextmanager
    def stage_fit(self, path: PurePosixPath, contents: bytes) -> Iterator[_StagedFit]:
        integrity = _validate_fit(contents)
        try:
            destination = self._destination(path)
            destination.parent.mkdir(exist_ok=True)
            self.require_available(path)
            with _stage(destination, contents) as temporary:
                yield _StagedFit(temporary, destination, integrity)
        except OSError:
            raise StorageFailure("Could not stage the COROS FIT.") from None

    def has_fit(self, path: PurePosixPath, integrity: FitIntegrity) -> bool:
        try:
            destination = self._destination(path)
            if (
                not path_exists(destination)
                or destination.stat().st_size != integrity.size
            ):
                return False
            with destination.open("rb") as stream:
                return (
                    hashlib.file_digest(stream, "sha256").hexdigest()
                    == integrity.sha256
                )
        except FileNotFoundError:
            return False
        except OSError:
            raise StorageFailure("Could not verify the COROS FIT.") from None

    def archive_damaged_fit(self, path: PurePosixPath) -> None:
        try:
            source = self._destination(path)
            if not path_exists(source):
                return
            metadata = self._output / ".fit-to-md"
            path_exists(metadata, directory=True)
            archive = metadata / "damaged-fits"
            if not path_exists(archive, directory=True):
                archive.mkdir()
            rename_new(source, archive / f"{source.stem}.{uuid4().hex}.fit")
        except (OSError, OutputConflict):
            raise StorageFailure("Could not archive the damaged COROS FIT.") from None

    def publish_report(self, path: PurePosixPath, markdown: str) -> None:
        try:
            destination = self._destination(path)
            self.require_available(path)
            with _stage(destination, markdown.encode("utf-8")) as temporary:
                rename_new(temporary, destination)
        except (OSError, UnicodeError):
            raise StorageFailure("Could not write the COROS report.") from None

    def _destination(self, path: PurePosixPath) -> Path:
        match path.parts:
            case ("fits", filename) if filename.endswith(".fit"):
                path_exists(self._output / "fits", directory=True)
            case (filename,) if filename.endswith(".md"):
                pass
            case _:
                raise StorageFailure("Invalid path in the COROS output registry.")
        if any(c in filename for c in '\\:/<>|?*"\0') or filename.startswith("."):
            raise StorageFailure("Invalid filename in the COROS output registry.")
        return self._output.joinpath(*path.parts)
