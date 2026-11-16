from pathlib import Path

import pytest

from tests.support.binary_coros import write_coros_activity


def test_coros_fixture_generation_is_reproducible(tmp_path: Path) -> None:
    generated = tmp_path / "coros.fit"

    write_coros_activity(generated)

    committed = (
        Path(__file__).resolve().parents[3] / "fit_files" / "coros_synthetic.fit"
    )
    assert generated.read_bytes() == committed.read_bytes()


def test_coros_fixture_requires_writable_destination(tmp_path: Path) -> None:
    with pytest.raises(IsADirectoryError):
        write_coros_activity(tmp_path)
