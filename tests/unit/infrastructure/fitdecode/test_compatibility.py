import struct
import warnings
from pathlib import Path

import fitdecode
import pytest
from fitdecode.utils import compute_crc

from fit_to_md.domain.activity import Activity, ActivityLap, ActivityRecord
from fit_to_md.domain.reporting import (
    SessionSummaryBuilder,
    SplitBuilder,
    TransitionBuilder,
)
from fit_to_md.infrastructure.fitdecode.builders import (
    SessionSummaryBuilder as LegacySessionSummaryBuilder,
)
from fit_to_md.infrastructure.fitdecode.builders import (
    SplitBuilder as LegacySplitBuilder,
)
from fit_to_md.infrastructure.fitdecode.builders import (
    TransitionBuilder as LegacyTransitionBuilder,
)
from fit_to_md.infrastructure.fitdecode.models import (
    ParsedActivityData,
    ParsedLap,
    ParsedRecord,
)
from fit_to_md.infrastructure.fitdecode.reader import FitdecodeActivityReader


def _write_short_field_fit(
    path: Path, message: int, field: int, *, bad_crc: bool = False
) -> None:
    # A genuine FIT definition incorrectly declares a one-byte uint32 payload.
    definition = (
        bytes((0x40, 0, 0)) + struct.pack("<H", message) + bytes((1, field, 1, 0x86))
    )
    body = definition + bytes((0, 1))
    header = struct.pack("<BBHI4s", 14, 0x10, 2196, len(body), b".FIT")
    header += struct.pack("<H", compute_crc(header))
    content = header + body
    crc = compute_crc(content) ^ int(bad_crc)
    path.write_bytes(content + struct.pack("<H", crc))


def test_reader_recovers_short_coros_event_data_without_warning(tmp_path: Path) -> None:
    source = tmp_path / "short-event.fit"
    _write_short_field_fit(source, 21, 3)
    with pytest.warns(UserWarning, match="invalid field size 1"):
        with fitdecode.FitReader(source) as reader:
            list(reader)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        activity = FitdecodeActivityReader().read(source)

    assert caught == []
    assert activity.records == ()


def test_reader_preserves_warnings_for_other_short_fields(tmp_path: Path) -> None:
    source = tmp_path / "short-record.fit"
    _write_short_field_fit(source, 20, 5)

    with pytest.warns(UserWarning, match="invalid field size 1"):
        FitdecodeActivityReader().read(source)


def test_reader_preserves_crc_warning_with_short_event_data(tmp_path: Path) -> None:
    source = tmp_path / "bad-crc.fit"
    _write_short_field_fit(source, 21, 3, bad_crc=True)

    with pytest.warns(UserWarning, match="CRC") as caught:
        FitdecodeActivityReader().read(source)

    assert len(caught) == 1


def test_legacy_fitdecode_imports_reexport_domain_types() -> None:
    assert LegacySessionSummaryBuilder is SessionSummaryBuilder
    assert LegacySplitBuilder is SplitBuilder
    assert LegacyTransitionBuilder is TransitionBuilder
    assert ParsedActivityData is Activity
    assert ParsedLap is ActivityLap
    assert ParsedRecord is ActivityRecord
