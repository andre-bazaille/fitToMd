import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from fit_to_md.domain.coros_connection import ConnectionFailure
from fit_to_md.infrastructure import coros_lock
from fit_to_md.infrastructure.coros_lock import SessionFileLock


def test_session_lock_immediately_excludes_another_process(tmp_path: Path) -> None:
    pytest.importorskip("portalocker")
    path = tmp_path / "state" / "connection.lock"
    attempt = [
        sys.executable,
        "-c",
        """
import sys
from pathlib import Path
from fit_to_md.domain.coros_connection import ConnectionFailure
from fit_to_md.infrastructure.coros_lock import SessionFileLock

try:
    with SessionFileLock(Path(sys.argv[1])).hold():
        print('Acquired')
except ConnectionFailure as error:
    print(error, file=sys.stderr)
    sys.exit(1)
""",
        str(path),
    ]
    with SessionFileLock(path).hold():
        result = subprocess.run(attempt, capture_output=True, text=True, timeout=5)
        assert result.returncode == 1, result.stderr
        assert "Another command is using the COROS session" in result.stderr
    assert path.read_bytes() == b""

    result = subprocess.run(attempt, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Acquired"


def test_lock_releases_even_if_the_operation_fails(tmp_path: Path) -> None:
    pytest.importorskip("portalocker")
    path = tmp_path / "connection.lock"
    with (
        pytest.raises(ValueError, match="operation failed"),
        SessionFileLock(path).hold(),
    ):
        raise ValueError("operation failed")
    with SessionFileLock(path).hold():
        assert path.is_file()


def test_session_lock_reports_filesystem_errors(tmp_path: Path) -> None:
    pytest.importorskip("portalocker")
    parent = tmp_path / "not-a-directory"
    parent.touch()
    with (
        pytest.raises(ConnectionFailure, match="Could not acquire"),
        SessionFileLock(parent / "connection.lock").hold(),
    ):
        pytest.fail("lock acquired")


def test_missing_portalocker_reports_optional_extra(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def missing(module: str) -> None:
        assert module == "portalocker"
        raise ImportError

    monkeypatch.setattr(coros_lock, "import_module", missing)
    with (
        pytest.raises(ConnectionFailure, match=r"Install the \[coros\] extra"),
        SessionFileLock(tmp_path / "connection.lock").hold(),
    ):
        pytest.fail("lock acquired")


def test_session_lock_path_is_independent_of_output_and_xdg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(coros_lock, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(Path, "home", classmethod(lambda _: tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    assert (
        coros_lock._session_lock_path()
        == tmp_path / ".local/state/fit-to-md/coros/connection.lock"
    )


def test_windows_session_lock_uses_local_app_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(coros_lock, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert (
        coros_lock._session_lock_path() == tmp_path / "fit-to-md/coros/connection.lock"
    )
    monkeypatch.delenv("LOCALAPPDATA")
    with pytest.raises(ConnectionFailure, match="LocalAppData is unavailable"):
        coros_lock._session_lock_path()
