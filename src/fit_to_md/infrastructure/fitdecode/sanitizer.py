from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import fitdecode
from fitdecode.utils import compute_crc

from fit_to_md.domain.privacy import FitSanitizationPolicy, SanitizationSummary

_STANDARD_HEADER_SIZE = 14
_FIT_TIMESTAMP_FIELD_NUMBER = 253
_FIT_TIMESTAMP_FIELD_DEFINITION = bytes([_FIT_TIMESTAMP_FIELD_NUMBER, 4, 0x86])
_MAX_COMPRESSED_TIMESTAMP_DELTA = 31


@dataclass(frozen=True)
class _DefinitionPlan:
    keep_message: bool
    kept_fields: tuple[bool, ...]
    definition_chunk: bytes | None
    timestamped_definition_chunk: bytes | None


@dataclass(frozen=True)
class _SanitizedDataMessage:
    chunk: bytes
    shifted_timestamps: int
    explicit_timestamp: int | None
    compressed_timestamp: int | None
    timestamp_insertion_offset: int


class FitdecodeFixtureSanitizer:
    """Rewrite a FIT activity while retaining only public-fixture-safe metadata."""

    def sanitize(
        self,
        source: Path,
        destination: Path,
        target_start: datetime,
        policy: FitSanitizationPolicy,
    ) -> SanitizationSummary:
        frames = self._read_frames(source)
        timestamp_delta = _timestamp_delta(frames, target_start)
        body, summary = _sanitize_body(frames, timestamp_delta, policy)
        header = _build_header(frames[0], len(body))
        contents = header + body
        contents += struct.pack("<H", compute_crc(contents))
        destination.write_bytes(contents)
        return summary

    @staticmethod
    def _read_frames(source: Path) -> list[Any]:
        with fitdecode.FitReader(
            source,
            check_crc=fitdecode.CrcCheck.RAISE,
            keep_raw_chunks=True,
        ) as reader:
            return list(reader)


def _timestamp_delta(frames: list[Any], target_start: datetime) -> int:
    session_starts = [
        field.value
        for frame in frames
        if getattr(frame, "frame_type", None) == fitdecode.FIT_FRAME_DATA
        and frame.name == "session"
        for field in frame.fields
        if field.name == "start_time" and isinstance(field.value, datetime)
    ]
    timestamps = [
        field.value
        for frame in frames
        if getattr(frame, "frame_type", None) == fitdecode.FIT_FRAME_DATA
        and not frame.name.startswith("unknown_")
        for field in frame.fields
        if field.field_def is not None
        and isinstance(field.value, datetime)
        and field.name in {"start_time", "timestamp", "time_created"}
    ]
    if not timestamps:
        raise ValueError("The FIT file does not contain a usable activity timestamp.")
    anchor = min(session_starts) if session_starts else min(timestamps)
    normalized_target = _normalize_datetime(target_start, anchor)
    return round((normalized_target - anchor).total_seconds())


def _normalize_datetime(target: datetime, anchor: datetime) -> datetime:
    if anchor.tzinfo is None:
        return target.replace(tzinfo=None)
    if target.tzinfo is None:
        return target.replace(tzinfo=anchor.tzinfo)
    return target.astimezone(anchor.tzinfo)


def _sanitize_body(
    frames: list[Any],
    timestamp_delta: int,
    policy: FitSanitizationPolicy,
) -> tuple[bytes, SanitizationSummary]:
    chunks: list[bytes] = []
    plans: dict[int, _DefinitionPlan] = {}
    timestamped_definitions: set[int] = set()
    timestamp_accumulator: int | None = None
    removed_messages = 0
    removed_fields = 0
    shifted_timestamps = 0

    for frame in frames:
        if frame.frame_type == fitdecode.FIT_FRAME_DEFINITION:
            plan = _plan_definition(frame, policy)
            plans[frame.local_mesg_num] = plan
            timestamped_definitions.discard(frame.local_mesg_num)
            removed_fields += sum(not keep for keep in plan.kept_fields)
            if plan.definition_chunk is not None:
                chunks.append(plan.definition_chunk)
        elif frame.frame_type == fitdecode.FIT_FRAME_DATA:
            plan = plans[frame.local_mesg_num]
            if not plan.keep_message:
                removed_messages += 1
                continue
            sanitized = _sanitize_data_message(frame, plan, timestamp_delta)
            local_message_number = frame.local_mesg_num

            if sanitized.compressed_timestamp is not None:
                compressed_timestamp = sanitized.compressed_timestamp
                if _can_compress_timestamp(timestamp_accumulator, compressed_timestamp):
                    if local_message_number in timestamped_definitions:
                        assert plan.definition_chunk is not None
                        chunks.append(plan.definition_chunk)
                        timestamped_definitions.remove(local_message_number)
                    chunks.append(
                        _recalculate_compressed_header(
                            sanitized.chunk,
                            local_message_number,
                            compressed_timestamp,
                        )
                    )
                else:
                    if local_message_number not in timestamped_definitions:
                        if plan.timestamped_definition_chunk is None:
                            raise ValueError(
                                "Unable to expand a compressed FIT timestamp."
                            )
                        chunks.append(plan.timestamped_definition_chunk)
                        timestamped_definitions.add(local_message_number)
                    chunks.append(
                        _expand_compressed_message(
                            sanitized.chunk,
                            local_message_number,
                            frame.def_mesg.endian,
                            compressed_timestamp,
                            sanitized.timestamp_insertion_offset,
                        )
                    )
                timestamp_accumulator = compressed_timestamp
            else:
                if local_message_number in timestamped_definitions:
                    assert plan.definition_chunk is not None
                    chunks.append(plan.definition_chunk)
                    timestamped_definitions.remove(local_message_number)
                chunks.append(sanitized.chunk)
                if sanitized.explicit_timestamp is not None:
                    timestamp_accumulator = sanitized.explicit_timestamp

            shifted_timestamps += sanitized.shifted_timestamps

    return b"".join(chunks), SanitizationSummary(
        removed_messages=removed_messages,
        removed_fields=removed_fields,
        shifted_timestamps=shifted_timestamps,
    )


def _plan_definition(frame: Any, policy: FitSanitizationPolicy) -> _DefinitionPlan:
    keep_message = policy.keeps_message(frame.name)
    all_fields = tuple(frame.field_defs) + tuple(frame.dev_field_defs)
    kept_fields = tuple(
        keep_message and policy.keeps_field(field.name, is_developer=field.is_dev)
        for field in all_fields
    )
    if not keep_message:
        return _DefinitionPlan(False, kept_fields, None, None)

    raw = frame.chunk.bytes
    standard_count = len(frame.field_defs)
    standard_definition_bytes = raw[6 : 6 + standard_count * 3]
    kept_standard_definitions = b"".join(
        standard_definition_bytes[index * 3 : index * 3 + 3]
        for index, keep in enumerate(kept_fields[:standard_count])
        if keep
    )
    developer_count = len(frame.dev_field_defs)
    developer_definitions_start = 7 + standard_count * 3
    developer_definition_bytes = raw[
        developer_definitions_start : developer_definitions_start + developer_count * 3
    ]
    kept_developer_definitions = b"".join(
        developer_definition_bytes[index * 3 : index * 3 + 3]
        for index, keep in enumerate(kept_fields[standard_count:])
        if keep
    )
    definition_chunk = _build_definition_chunk(
        raw,
        kept_standard_definitions,
        kept_developer_definitions,
    )
    has_timestamp = any(
        keep and not field.is_dev and field.def_num == _FIT_TIMESTAMP_FIELD_NUMBER
        for field, keep in zip(all_fields, kept_fields, strict=True)
    )
    return _DefinitionPlan(
        keep_message=True,
        kept_fields=kept_fields,
        definition_chunk=definition_chunk,
        timestamped_definition_chunk=(
            None
            if has_timestamp
            else _build_definition_chunk(
                raw,
                kept_standard_definitions + _FIT_TIMESTAMP_FIELD_DEFINITION,
                kept_developer_definitions,
            )
        ),
    )


def _sanitize_data_message(
    frame: Any,
    plan: _DefinitionPlan,
    timestamp_delta: int,
) -> _SanitizedDataMessage:
    raw = frame.chunk.bytes
    output = bytearray(raw[:1])
    offset = 1
    shifted = 0
    standard_payload_size = 0
    explicit_timestamp: int | None = None
    field_data_by_definition = {
        id(field.field_def): field
        for field in frame.fields
        if field.field_def is not None
    }

    for field_definition, keep in zip(
        frame.def_mesg.all_field_defs, plan.kept_fields, strict=True
    ):
        end = offset + field_definition.size
        field_bytes = raw[offset:end]
        offset = end
        if not keep:
            continue
        field_data = field_data_by_definition.get(id(field_definition))
        if field_data is not None and isinstance(field_data.value, datetime):
            field_bytes = _shift_datetime_bytes(
                field_bytes,
                frame.def_mesg.endian,
                field_definition.base_type.fmt,
                timestamp_delta,
            )
            shifted += 1
            if (
                not field_definition.is_dev
                and field_definition.def_num == _FIT_TIMESTAMP_FIELD_NUMBER
            ):
                explicit_timestamp = _unpack_fit_timestamp(
                    field_bytes,
                    frame.def_mesg.endian,
                )
        output.extend(field_bytes)
        if not field_definition.is_dev:
            standard_payload_size += len(field_bytes)

    compressed_timestamp = _compressed_timestamp(frame, timestamp_delta)
    if compressed_timestamp is not None:
        shifted += 1

    return _SanitizedDataMessage(
        chunk=bytes(output),
        shifted_timestamps=shifted,
        explicit_timestamp=explicit_timestamp,
        compressed_timestamp=compressed_timestamp,
        timestamp_insertion_offset=1 + standard_payload_size,
    )


def _compressed_timestamp(frame: Any, timestamp_delta: int) -> int | None:
    if frame.time_offset is None:
        return None
    timestamp_fields = [
        field
        for field in frame.fields
        if field.field_def is None
        and field.name == "timestamp"
        and isinstance(field.raw_value, int)
    ]
    if len(timestamp_fields) != 1:
        raise ValueError("The compressed FIT timestamp could not be decoded.")
    return _shift_fit_timestamp(timestamp_fields[0].raw_value, timestamp_delta)


def _can_compress_timestamp(accumulator: int | None, timestamp: int) -> bool:
    if accumulator is None:
        return False
    delta = timestamp - accumulator
    return 0 <= delta <= _MAX_COMPRESSED_TIMESTAMP_DELTA


def _recalculate_compressed_header(
    chunk: bytes,
    local_message_number: int,
    timestamp: int,
) -> bytes:
    header = 0x80 | (local_message_number << 5) | (timestamp & 0x1F)
    return bytes([header]) + chunk[1:]


def _expand_compressed_message(
    chunk: bytes,
    local_message_number: int,
    endian: str,
    timestamp: int,
    timestamp_insertion_offset: int,
) -> bytes:
    return (
        bytes([local_message_number])
        + chunk[1:timestamp_insertion_offset]
        + struct.pack(f"{endian}I", timestamp)
        + chunk[timestamp_insertion_offset:]
    )


def _build_definition_chunk(
    original: bytes,
    standard_definitions: bytes,
    developer_definitions: bytes,
) -> bytes:
    standard_count = len(standard_definitions) // 3
    developer_count = len(developer_definitions) // 3
    if standard_count > 0xFF or developer_count > 0xFF:
        raise ValueError("The FIT definition contains too many fields.")
    header = original[0] & ~0x20
    if developer_definitions:
        header |= 0x20
    chunk = bytes([header]) + original[1:5] + bytes([standard_count])
    chunk += standard_definitions
    if developer_definitions:
        chunk += bytes([developer_count]) + developer_definitions
    return chunk


def _unpack_fit_timestamp(raw: bytes, endian: str) -> int:
    if len(raw) != 4:
        raise ValueError("A FIT timestamp must contain exactly four bytes.")
    return int(struct.unpack(f"{endian}I", raw)[0])


def _shift_fit_timestamp(value: int, timestamp_delta: int) -> int:
    shifted = value + timestamp_delta
    if shifted < 0 or shifted >= 0xFFFFFFFF:
        raise ValueError("The target timestamp is outside the FIT date range.")
    return shifted


def _shift_datetime_bytes(
    raw: bytes,
    endian: str,
    field_format: str,
    timestamp_delta: int,
) -> bytes:
    value_format = struct.Struct(f"{endian}{field_format}")
    if len(raw) != value_format.size:
        raise ValueError("Array-valued FIT timestamps are not supported.")
    (value,) = value_format.unpack(raw)
    shifted = int(value) + timestamp_delta
    if shifted < 0 or shifted >= 2 ** (8 * len(raw)) - 1:
        raise ValueError("The target timestamp is outside the FIT date range.")
    return value_format.pack(shifted)


def _build_header(original_header: Any, body_size: int) -> bytes:
    protocol_major, protocol_minor = original_header.proto_ver
    profile_major, profile_minor = original_header.profile_ver
    header_without_crc = struct.pack(
        "<BBHI4s",
        _STANDARD_HEADER_SIZE,
        (protocol_major << 4) | protocol_minor,
        profile_major * 100 + profile_minor,
        body_size,
        b".FIT",
    )
    return header_without_crc + struct.pack("<H", compute_crc(header_without_crc))
