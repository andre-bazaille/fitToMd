"""Encode the synthetic workout as a CRC-valid FIT file for decoder acceptance."""

import struct
from datetime import UTC, datetime
from pathlib import Path

from fitdecode.utils import compute_crc

from tests.support.workout import Variant, workout_scenario

_FIT_EPOCH = datetime(1989, 12, 31, tzinfo=UTC)
_INTENSITY = {"active": 0, "recovery": 4, "warmup": 2, "cooldown": 3}
_DURATION = {"time": 0, "distance": 1, "repeat_until_steps_cmplt": 6}


def _timestamp(value: datetime) -> int:
    return int((value - _FIT_EPOCH).total_seconds())


def _definition(
    local: int, global_number: int, fields: tuple[tuple[int, int, int], ...]
) -> bytes:
    return (
        bytes((0x40 | local, 0, 0))
        + struct.pack("<H", global_number)
        + bytes((len(fields),))
        + b"".join(bytes(field) for field in fields)
    )


def _name(value: object) -> bytes:
    if not isinstance(value, str):
        return bytes(16)
    return value.encode("utf-8")[:15].ljust(16, b"\0")


def write_binary_workout(path: Path, variant: Variant = "structured") -> None:
    """Write a 14-lap workout using actual FIT field IDs, scales, and CRCs."""
    if variant not in ("structured", "unlabeled"):
        raise ValueError("Binary acceptance fixture supports structured or unlabeled")
    scenario = workout_scenario(variant)
    definitions = b"".join(
        (
            _definition(0, 12, ((0, 1, 0x00),)),
            _definition(
                1,
                27,
                (
                    (254, 2, 0x84),
                    (0, 16, 0x07),
                    (1, 1, 0x00),
                    (2, 4, 0x86),
                    (3, 1, 0x00),
                    (7, 1, 0x00),
                ),
            ),
            _definition(
                2,
                20,
                (
                    (253, 4, 0x86),
                    (5, 4, 0x86),
                    (3, 1, 0x02),
                    (4, 1, 0x02),
                    (6, 2, 0x84),
                ),
            ),
            _definition(3, 21, ((253, 4, 0x86), (0, 1, 0x00), (1, 1, 0x00))),
            _definition(
                4,
                19,
                (
                    (254, 2, 0x84),
                    (253, 4, 0x86),
                    (2, 4, 0x86),
                    (7, 4, 0x86),
                    (8, 4, 0x86),
                    (9, 4, 0x86),
                    (15, 1, 0x02),
                    (23, 1, 0x00),
                    (71, 2, 0x84),
                ),
            ),
            _definition(
                5,
                18,
                (
                    (253, 4, 0x86),
                    (2, 4, 0x86),
                    (7, 4, 0x86),
                    (8, 4, 0x86),
                    (9, 4, 0x86),
                    (5, 1, 0x00),
                    (16, 1, 0x02),
                    (17, 1, 0x02),
                ),
            ),
        )
    )
    messages: list[bytes] = []
    for frame in scenario.frames:
        values = frame.values
        if frame.name == "sport":
            messages.append(bytes((0, 1)))
        elif frame.name == "workout_step":
            duration = values["duration_type"]
            raw_duration = (
                round(float(values["duration_distance"]) * 100)
                if duration == "distance"
                else round(float(values["duration_time"]) * 1000)
                if duration == "time"
                else int(values["duration_step"])
            )
            messages.append(
                bytes((1,))
                + struct.pack("<H", int(values["message_index"]))
                + _name(values.get("wkt_step_name"))
                + struct.pack(
                    "<BIBB",
                    _DURATION[str(duration)],
                    raw_duration,
                    2,
                    _INTENSITY.get(str(values.get("intensity")), 0xFF),
                )
            )
        elif frame.name == "record":
            messages.append(
                bytes((2,))
                + struct.pack(
                    "<IIBBH",
                    _timestamp(values["timestamp"]),
                    round(float(values["distance"]) * 100),
                    int(values["heart_rate"]),
                    int(values["cadence"]),
                    round(float(values["speed"]) * 1000),
                )
            )
        elif frame.name == "event":
            messages.append(
                bytes((3,))
                + struct.pack(
                    "<IBB",
                    _timestamp(values["timestamp"]),
                    0,
                    0 if values["event_type"] == "start" else 4,
                )
            )
        elif frame.name == "lap":
            messages.append(
                bytes((4,))
                + struct.pack(
                    "<HIIIIIBBH",
                    int(values["message_index"]),
                    _timestamp(values["timestamp"]),
                    _timestamp(values["start_time"]),
                    round(float(values["total_timer_time"]) * 1000),
                    round(float(values["total_timer_time"]) * 1000),
                    round(float(values["total_distance"]) * 100),
                    int(values["avg_heart_rate"]),
                    _INTENSITY.get(str(values.get("intensity")), 0xFF),
                    int(values.get("wkt_step_index", 0xFFFF)),
                )
            )
        elif frame.name == "session":
            messages.append(
                bytes((5,))
                + struct.pack(
                    "<IIIIIBBB",
                    _timestamp(values["timestamp"]),
                    _timestamp(values["start_time"]),
                    round(float(values["total_elapsed_time"]) * 1000),
                    round(float(values["total_timer_time"]) * 1000),
                    round(float(values["total_distance"]) * 100),
                    1,
                    int(values["avg_heart_rate"]),
                    int(values["max_heart_rate"]),
                )
            )
    body = definitions + b"".join(messages)
    header_without_crc = struct.pack("<BBHI4s", 14, 0x10, 2196, len(body), b".FIT")
    header = header_without_crc + struct.pack("<H", compute_crc(header_without_crc))
    content = header + body
    path.write_bytes(content + struct.pack("<H", compute_crc(content)))
