import struct
from datetime import UTC, datetime, timedelta
from pathlib import Path

import fitdecode
import pytest
from fitdecode.utils import compute_crc

from fit_to_md.domain.privacy import FitSanitizationPolicy
from fit_to_md.infrastructure.fitdecode.extractor import FitdecodeActivityExtractor
from fit_to_md.infrastructure.fitdecode.sanitizer import FitdecodeFixtureSanitizer

FIT_EPOCH = datetime(1989, 12, 31, tzinfo=UTC)


def _fit_timestamp(value: datetime) -> int:
    return int((value - FIT_EPOCH).total_seconds())


def _definition(
    local_message_number: int,
    global_message_number: int,
    fields: list[tuple[int, int, int]],
) -> bytes:
    return (
        bytes([0x40 | local_message_number, 0, 0])
        + struct.pack("<H", global_message_number)
        + bytes([len(fields)])
        + b"".join(bytes(field) for field in fields)
    )


def _write_compressed_timestamp_fit(
    path: Path,
    *,
    private_timestamp_offset: int | None = None,
) -> tuple[datetime, tuple[int, ...]]:
    start = datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)
    start_timestamp = _fit_timestamp(start)
    record_offsets = (5, 7) if private_timestamp_offset is None else (45, 46)

    session_definition = _definition(
        1,
        18,
        [(253, 4, 0x86), (2, 4, 0x86)],
    )
    session_data = bytes([1]) + struct.pack("<II", start_timestamp, start_timestamp)
    private_chunks = b""
    if private_timestamp_offset is not None:
        private_chunks = (
            _definition(2, 23, [(253, 4, 0x86)])
            + bytes([2])
            + struct.pack("<I", start_timestamp + private_timestamp_offset)
        )
    record_definition = _definition(
        0,
        20,
        [(0, 4, 0x85), (1, 4, 0x85)],
    )
    record_data = b"".join(
        bytes([0x80 | ((start_timestamp + offset) & 0x1F)])
        + struct.pack("<ii", 123, 456)
        for offset in record_offsets
    )
    body = (
        session_definition
        + session_data
        + private_chunks
        + record_definition
        + record_data
    )
    header_without_crc = struct.pack("<BBHI4s", 14, 0x10, 2196, len(body), b".FIT")
    header = header_without_crc + struct.pack("<H", compute_crc(header_without_crc))
    contents = header + body
    path.write_bytes(contents + struct.pack("<H", compute_crc(contents)))
    return start, record_offsets


def _write_developer_field_fit(path: Path) -> tuple[datetime, tuple[int, ...]]:
    start = datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)
    start_timestamp = _fit_timestamp(start)
    record_offsets = (45, 46)

    developer_data_id = _definition(2, 207, [(3, 1, 0x02)]) + bytes([2, 0])
    field_name = b"custom_metric\0".ljust(16, b"\0")
    field_description = (
        _definition(
            3,
            206,
            [(0, 1, 0x02), (1, 1, 0x02), (2, 1, 0x00), (3, 16, 0x07)],
        )
        + bytes([3, 0, 1, 0x84])
        + field_name
    )
    session_definition = _definition(
        1,
        18,
        [(253, 4, 0x86), (2, 4, 0x86)],
    )
    session_data = bytes([1]) + struct.pack("<II", start_timestamp, start_timestamp)
    private_device_info = (
        _definition(4, 23, [(253, 4, 0x86)])
        + bytes([4])
        + struct.pack("<I", start_timestamp + 40)
    )
    record_definition = (
        bytes([0x60, 0, 0])
        + struct.pack("<H", 20)
        + bytes([2, 0, 4, 0x85, 1, 4, 0x85])
        + bytes([1, 1, 2, 0])
    )
    developer_values = (321, 654)
    record_data = b"".join(
        bytes([0x80 | ((start_timestamp + offset) & 0x1F)])
        + struct.pack("<iiH", 123, 456, developer_value)
        for offset, developer_value in zip(
            record_offsets,
            developer_values,
            strict=True,
        )
    )
    body = (
        developer_data_id
        + field_description
        + session_definition
        + session_data
        + private_device_info
        + record_definition
        + record_data
    )
    header_without_crc = struct.pack("<BBHI4s", 14, 0x10, 2196, len(body), b".FIT")
    header = header_without_crc + struct.pack("<H", compute_crc(header_without_crc))
    contents = header + body
    path.write_bytes(contents + struct.pack("<H", compute_crc(contents)))
    return start, record_offsets


def _write_private_activity_fit(path: Path) -> None:
    start = datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)
    start_timestamp = _fit_timestamp(start)
    end_timestamp = start_timestamp + 60

    file_id = (
        _definition(
            0,
            0,
            [
                (0, 1, 0x02),
                (1, 2, 0x84),
                (2, 2, 0x84),
                (3, 4, 0x86),
                (4, 4, 0x86),
            ],
        )
        + bytes([0, 4])
        + struct.pack("<HHII", 1, 1, 123456789, start_timestamp)
    )
    sport = _definition(1, 12, [(0, 1, 0x00)]) + bytes([1, 1])
    event = (
        _definition(
            2,
            21,
            [(253, 4, 0x86), (0, 1, 0x00), (1, 1, 0x00)],
        )
        + bytes([2])
        + struct.pack("<IBB", start_timestamp, 0, 0)
    )
    lap = (
        _definition(
            3,
            19,
            [
                (253, 4, 0x86),
                (2, 4, 0x86),
                (7, 4, 0x86),
                (8, 4, 0x86),
                (9, 4, 0x86),
                (15, 1, 0x02),
            ],
        )
        + bytes([3])
        + struct.pack(
            "<IIIIIB", end_timestamp, start_timestamp, 60_000, 60_000, 100_000, 140
        )
    )
    record_definition = _definition(
        4,
        20,
        [
            (253, 4, 0x86),
            (0, 4, 0x85),
            (1, 4, 0x85),
            (5, 4, 0x86),
            (3, 1, 0x02),
            (6, 2, 0x84),
            (2, 2, 0x84),
        ],
    )
    records = b"".join(
        bytes([4]) + struct.pack("<IiiIBHH", *values)
        for values in (
            (start_timestamp, 596_523_236, 29_826_162, 0, 135, 3_000, 3_000),
            (end_timestamp, 596_530_000, 29_835_000, 100_000, 145, 3_000, 3_005),
        )
    )
    session = (
        _definition(
            5,
            18,
            [
                (253, 4, 0x86),
                (2, 4, 0x86),
                (7, 4, 0x86),
                (8, 4, 0x86),
                (9, 4, 0x86),
                (5, 1, 0x00),
                (16, 1, 0x02),
            ],
        )
        + bytes([5])
        + struct.pack(
            "<IIIIIBB",
            end_timestamp,
            start_timestamp,
            60_000,
            60_000,
            100_000,
            1,
            140,
        )
    )
    activity = (
        _definition(
            6,
            34,
            [
                (253, 4, 0x86),
                (0, 4, 0x86),
                (1, 2, 0x84),
                (2, 1, 0x00),
                (3, 1, 0x00),
                (4, 1, 0x00),
            ],
        )
        + bytes([6])
        + struct.pack("<IIHBBB", end_timestamp, 60_000, 1, 0, 26, 1)
    )
    device_info = (
        _definition(
            7,
            23,
            [(253, 4, 0x86), (3, 4, 0x86)],
        )
        + bytes([7])
        + struct.pack("<II", start_timestamp, 987654321)
    )

    body = (
        file_id
        + sport
        + event
        + lap
        + record_definition
        + records
        + session
        + activity
        + device_info
    )
    header_without_crc = struct.pack("<BBHI4s", 14, 0x10, 2196, len(body), b".FIT")
    header = header_without_crc + struct.pack("<H", compute_crc(header_without_crc))
    contents = header + body
    path.write_bytes(contents + struct.pack("<H", compute_crc(contents)))


def _data_messages(path: Path) -> list[object]:
    with fitdecode.FitReader(path, check_crc=fitdecode.CrcCheck.RAISE) as reader:
        return [
            frame for frame in reader if frame.frame_type == fitdecode.FIT_FRAME_DATA
        ]


def _raw_gps_track(messages: list[object]) -> list[tuple[int, int]]:
    return [
        (message.get_value("position_lat"), message.get_value("position_long"))
        for message in messages
        if message.name == "record"
    ]


def _timestamps_by_field(
    messages: list[object], message_names: set[str]
) -> dict[tuple[str, str], list[datetime]]:
    result: dict[tuple[str, str], list[datetime]] = {}
    for message in messages:
        if message.name not in message_names:
            continue
        for field in message.fields:
            if isinstance(field.value, datetime) and not field.name.startswith(
                "unknown_"
            ):
                result.setdefault((message.name, field.name), []).append(field.value)
    return result


def test_sanitizer_removes_private_metadata_and_preserves_activity(
    tmp_path: Path,
) -> None:
    source = tmp_path / "private.fit"
    destination = tmp_path / "public.fit"
    target = datetime(2020, 1, 1, 12, tzinfo=UTC)
    _write_private_activity_fit(source)

    summary = FitdecodeFixtureSanitizer().sanitize(
        source, destination, target, FitSanitizationPolicy()
    )

    original_messages = _data_messages(source)
    public_messages = _data_messages(destination)
    public_names = {message.name for message in public_messages}
    public_fields = [field for message in public_messages for field in message.fields]
    policy = FitSanitizationPolicy()
    assert summary.removed_messages > 0
    assert summary.removed_fields > 0
    assert summary.shifted_timestamps > 0
    assert public_names == policy.allowed_message_names
    assert not any(field.name.startswith("unknown_") for field in public_fields)
    assert not any(field.name == "serial_number" for field in public_fields)
    assert not any(
        field.field_def is not None and field.field_def.is_dev
        for field in public_fields
    )
    assert _raw_gps_track(public_messages) == _raw_gps_track(original_messages)

    original_timestamps = _timestamps_by_field(original_messages, public_names)
    public_timestamps = _timestamps_by_field(public_messages, public_names)
    expected_delta = target - original_timestamps[("session", "start_time")][0]
    assert public_timestamps.keys() == original_timestamps.keys()
    for key, original_values in original_timestamps.items():
        assert public_timestamps[key] == [
            timestamp + expected_delta for timestamp in original_values
        ]

    original = FitdecodeActivityExtractor().extract(source).summary
    sanitized = FitdecodeActivityExtractor().extract(destination).summary
    assert sanitized.start_time == target
    assert sanitized.total_distance_km == original.total_distance_km
    assert sanitized.total_timer_time_s == original.total_timer_time_s
    assert sanitized.avg_heart_rate_bpm == original.avg_heart_rate_bpm


def test_sanitizer_rejects_fit_without_an_activity_timestamp(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.fit"
    invalid.write_bytes(b"not FIT")

    with pytest.raises(fitdecode.FitError):
        FitdecodeFixtureSanitizer().sanitize(
            invalid,
            tmp_path / "public.fit",
            datetime(2020, 1, 1, tzinfo=UTC),
            FitSanitizationPolicy(),
        )


def test_sanitizer_recalculates_compressed_timestamp_headers(tmp_path: Path) -> None:
    source = tmp_path / "private.fit"
    destination = tmp_path / "public.fit"
    _, record_offsets = _write_compressed_timestamp_fit(source)
    target = datetime(2020, 1, 1, 12, tzinfo=UTC)

    summary = FitdecodeFixtureSanitizer().sanitize(
        source,
        destination,
        target,
        FitSanitizationPolicy(),
    )

    records = [
        message for message in _data_messages(destination) if message.name == "record"
    ]
    assert [message.get_value("timestamp") for message in records] == [
        target + timedelta(seconds=offset) for offset in record_offsets
    ]
    assert [message.time_offset for message in records] == [
        _fit_timestamp(target + timedelta(seconds=offset)) & 0x1F
        for offset in record_offsets
    ]
    assert summary.shifted_timestamps == 4


def test_sanitizer_expands_compressed_timestamp_after_removed_anchor(
    tmp_path: Path,
) -> None:
    source = tmp_path / "private.fit"
    destination = tmp_path / "public.fit"
    _, record_offsets = _write_compressed_timestamp_fit(
        source,
        private_timestamp_offset=40,
    )
    target = datetime(2020, 1, 1, 12, tzinfo=UTC)

    summary = FitdecodeFixtureSanitizer().sanitize(
        source,
        destination,
        target,
        FitSanitizationPolicy(),
    )

    public_messages = _data_messages(destination)
    assert {message.name for message in public_messages} == {"record", "session"}
    records = [message for message in public_messages if message.name == "record"]
    timestamps = [
        next(field for field in record.fields if field.name == "timestamp")
        for record in records
    ]
    assert [record.time_offset for record in records] == [
        None,
        _fit_timestamp(target + timedelta(seconds=record_offsets[1])) & 0x1F,
    ]
    assert timestamps[0].field_def is not None
    assert timestamps[1].field_def is None
    assert [timestamp.value for timestamp in timestamps] == [
        target + timedelta(seconds=offset) for offset in record_offsets
    ]
    assert summary.removed_messages == 1
    assert summary.shifted_timestamps == 4


def test_sanitizer_preserves_allowed_developer_fields_and_definitions(
    tmp_path: Path,
) -> None:
    source = tmp_path / "private.fit"
    destination = tmp_path / "public.fit"
    _, record_offsets = _write_developer_field_fit(source)
    target = datetime(2020, 1, 1, 12, tzinfo=UTC)
    default_allowed_messages = FitSanitizationPolicy().allowed_message_names
    policy = FitSanitizationPolicy(
        allowed_message_names=default_allowed_messages
        | {"developer_data_id", "field_description"},
        remove_developer_fields=False,
    )

    FitdecodeFixtureSanitizer().sanitize(source, destination, target, policy)

    public_messages = _data_messages(destination)
    assert "device_info" not in {message.name for message in public_messages}
    records = [message for message in public_messages if message.name == "record"]
    timestamps = [
        next(field for field in record.fields if field.name == "timestamp")
        for record in records
    ]
    developer_fields = [
        next(field for field in record.fields if field.name == "custom_metric")
        for record in records
    ]
    assert [record.time_offset for record in records] == [
        None,
        _fit_timestamp(target + timedelta(seconds=record_offsets[1])) & 0x1F,
    ]
    assert [timestamp.value for timestamp in timestamps] == [
        target + timedelta(seconds=offset) for offset in record_offsets
    ]
    assert [field.value for field in developer_fields] == [321, 654]
    assert all(field.field_def.is_dev for field in developer_fields)
