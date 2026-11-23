import errno
import hashlib
import struct
import sys
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fitdecode.utils import compute_crc

from fit_to_md.application.synchronization_storage import (
    FilePaths,
    FitIntegrity,
    InvalidFit,
    OutputConflict,
    StorageFailure,
)
from fit_to_md.infrastructure.synchronization import files
from fit_to_md.infrastructure.synchronization.files import LocalOutputFiles

pytestmark = pytest.mark.skipif(
    sys.platform not in {"linux", "win32"}, reason="COROS supports Linux and Windows"
)
FIT_PATH = PurePosixPath("fits/2020-01-01 12-00.fit")
REPORT_PATH = PurePosixPath("2020-01-01 12-00.md")


def fit_bytes(header_size: int, *, header_crc: int | None = None) -> bytes:
    payload = b"dummy FIT payload"
    header = struct.pack("<BBHI4s", header_size, 16, 0, len(payload), b".FIT")
    if header_size == 14:
        header += struct.pack("<H", 0 if header_crc is None else header_crc)
    contents = header + payload
    return contents + struct.pack("<H", compute_crc(contents))


@pytest.mark.parametrize("header_size", [12, 14])
def test_fit_staged_then_published_with_exact_digest(
    tmp_path: Path, header_size: int
) -> None:
    contents = fit_bytes(header_size)
    storage = LocalOutputFiles(tmp_path)
    final = tmp_path / FIT_PATH
    with storage.stage_fit(FIT_PATH, contents) as staged:
        assert not final.exists()
        assert list(final.parent.glob(".fit-to-md-*.tmp"))
        assert staged.integrity == FitIntegrity(
            len(contents), hashlib.sha256(contents).hexdigest()
        )
        assert not storage.has_fit(FIT_PATH, staged.integrity)
        staged.publish()
        assert storage.has_fit(FIT_PATH, staged.integrity)
    assert list(final.parent.iterdir()) == [final]
    assert final.read_bytes() == contents


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        (b"<!doctype html>", "signature or header"),
        (fit_bytes(14)[:-1], "length"),
        (fit_bytes(14, header_crc=1), "header checksum"),
        (fit_bytes(14)[:-1] + bytes([fit_bytes(14)[-1] ^ 1]), "file checksum"),
    ],
)
def test_invalid_fit_is_never_staged(
    tmp_path: Path, contents: bytes, message: str
) -> None:
    with (
        pytest.raises(InvalidFit, match=message),
        LocalOutputFiles(tmp_path).stage_fit(FIT_PATH, contents),
    ):
        pytest.fail("invalid FIT staged")
    assert list(tmp_path.iterdir()) == []


def test_interruption_before_publication_and_stale_temporary_are_not_acquisition(
    tmp_path: Path,
) -> None:
    storage = LocalOutputFiles(tmp_path)
    with (
        pytest.raises(KeyboardInterrupt),
        storage.stage_fit(FIT_PATH, fit_bytes(14)) as staged,
    ):
        assert not (tmp_path / FIT_PATH).exists()
        raise KeyboardInterrupt
    fits = tmp_path / "fits"
    assert list(fits.iterdir()) == []
    (fits / ".fit-to-md-crashed.tmp").write_bytes(fit_bytes(14))
    assert not storage.has_fit(FIT_PATH, staged.integrity)


@pytest.mark.parametrize("error_code", [errno.ENOSPC, errno.EACCES])
def test_staging_write_failure_cleans_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error_code: int
) -> None:
    def fail(fd: int) -> None:
        raise OSError(error_code, "injected filesystem error")

    monkeypatch.setattr(files.os, "fsync", fail)
    storage = LocalOutputFiles(tmp_path)
    with (
        pytest.raises(StorageFailure, match="Could not stage"),
        storage.stage_fit(FIT_PATH, fit_bytes(14)),
    ):
        pytest.fail("FIT staged despite filesystem error")
    assert list((tmp_path / "fits").iterdir()) == []
    with pytest.raises(StorageFailure, match="Could not write"):
        storage.publish_report(REPORT_PATH, "example")
    assert list(tmp_path.iterdir()) == [tmp_path / "fits"]


def test_report_late_collision_cleans_temp_and_preserves_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rename = files.rename_new

    def late_file(source: Path, destination: Path) -> None:
        assert source.parent == destination.parent
        destination.write_bytes(b"manual report")
        rename(source, destination)

    monkeypatch.setattr(files, "rename_new", late_file)
    with pytest.raises(OutputConflict):
        LocalOutputFiles(tmp_path).publish_report(REPORT_PATH, "# new report")
    destination = tmp_path / REPORT_PATH
    assert destination.read_bytes() == b"manual report"
    assert list(tmp_path.iterdir()) == [destination]


def test_files_refuse_a_path_outside_output(tmp_path: Path) -> None:
    with pytest.raises(StorageFailure, match="Invalid path"):
        LocalOutputFiles(tmp_path).publish_report(
            PurePosixPath("../escape.md"), "report"
        )
    assert list(tmp_path.iterdir()) == []


def test_activity_path_rejects_start_outside_local_date_range() -> None:
    with pytest.raises(StorageFailure, match="start time cannot be used"):
        FilePaths.for_start(datetime.max.replace(tzinfo=UTC), ZoneInfo("Europe/Paris"))


@pytest.mark.skipif(sys.platform != "linux", reason="Linux renameat2 error mapping")
def test_unsupported_volume_fails_without_falling_back_to_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unsupported(*args: object) -> int:
        files.ctypes.set_errno(errno.EOPNOTSUPP)
        return -1

    monkeypatch.setattr(
        files.ctypes,
        "CDLL",
        lambda *args, **kwargs: SimpleNamespace(renameat2=unsupported),
    )
    with pytest.raises(StorageFailure, match="volume does not support atomic"):
        LocalOutputFiles(tmp_path).publish_report(REPORT_PATH, "report")
    assert list(tmp_path.iterdir()) == []
