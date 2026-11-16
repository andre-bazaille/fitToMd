"""Build synthetic COROS-like FIT bytes without copying personal activity data."""

import struct
from datetime import UTC, datetime
from pathlib import Path

from fitdecode.utils import compute_crc

_START = int(
    (
        datetime(2020, 1, 1, 12, tzinfo=UTC) - datetime(1989, 12, 31, tzinfo=UTC)
    ).total_seconds()
)


def _definition(
    local: int, message: int, fields: tuple[tuple[int, int, int], ...]
) -> bytes:
    return (
        bytes((0x40 | local, 0, 0))
        + struct.pack("<H", message)
        + bytes((len(fields),))
        + b"".join(bytes(field) for field in fields)
    )


def write_coros_activity(path: Path) -> None:
    """Write a deterministic 2 km run with COROS export compatibility quirks."""
    name = b"Synthetic COROS\0"
    parts = [
        _definition(0, 0, ((0, 1, 0x00), (1, 2, 0x84), (8, 16, 0x07))),
        bytes((0, 4)) + struct.pack("<H", 294) + name,
        _definition(1, 23, ((253, 4, 0x86), (2, 2, 0x84), (27, 16, 0x07))),
        bytes((1,)) + struct.pack("<IH", _START, 294) + name,
        # Deliberately reproduce the recoverable COROS event.data definition:
        # uint32 declared with only one byte of storage.
        _definition(2, 21, ((253, 4, 0x86), (0, 1, 0), (1, 1, 0), (3, 1, 0x86))),
        bytes((2,)) + struct.pack("<IBBB", _START, 0, 0, 0),
        _definition(
            3,
            20,
            (
                (253, 4, 0x86),
                (0, 4, 0x85),
                (1, 4, 0x85),
                (5, 4, 0x86),
                (3, 1, 0x02),
                (4, 1, 0x02),
                (6, 2, 0x84),
                (2, 2, 0x84),
            ),
        ),
    ]
    for index in range(61):
        # The initial record has no GPS lock. Later coordinates are invented.
        latitude = round(48 * 2**31 / 180) if index else 0x7FFFFFFF
        longitude = (
            round((2 + (index - 1) * 0.0001) * 2**31 / 180) if index else 0x7FFFFFFF
        )
        parts.append(
            bytes((3,))
            + struct.pack(
                "<IiiIBBHH",
                _START + index * 10,
                latitude,
                longitude,
                round(index * 200_000 / 60),
                140,
                80,
                3333,
                3000,
            )
        )
    parts.extend(
        [
            bytes((2,)) + struct.pack("<IBBB", _START + 600, 0, 4, 0),
            _definition(
                4,
                19,
                (
                    (253, 4, 0x86),
                    (2, 4, 0x86),
                    (7, 4, 0x86),
                    (8, 4, 0x86),
                    (9, 4, 0x86),
                    (15, 1, 0x02),
                    (16, 1, 0x02),
                    (50, 1, 0x01),
                ),
            ),
        ]
    )
    for index, temperature in enumerate((23, 15)):
        parts.append(
            bytes((4,))
            + struct.pack(
                "<IIIIIBBb",
                _START + (index + 1) * 300,
                _START + index * 300,
                300_000,
                300_000,
                100_000,
                140,
                140,
                temperature,
            )
        )
    # No session start coordinates and no weather_conditions message, matching
    # the observed COROS exports. The 17C value is a device sensor average.
    parts.extend(
        [
            _definition(
                5,
                18,
                (
                    (253, 4, 0x86),
                    (2, 4, 0x86),
                    (7, 4, 0x86),
                    (8, 4, 0x86),
                    (9, 4, 0x86),
                    (5, 1, 0),
                    (16, 1, 0x02),
                    (17, 1, 0x02),
                    (57, 1, 0x01),
                ),
            ),
            bytes((5,))
            + struct.pack(
                "<IIIIIBBBb",
                _START + 600,
                _START,
                600_000,
                600_000,
                200_000,
                1,
                140,
                140,
                17,
            ),
        ]
    )
    body = b"".join(parts)
    header = struct.pack("<BBHI4s", 14, 0x10, 2196, len(body), b".FIT")
    header += struct.pack("<H", compute_crc(header))
    content = header + body
    path.write_bytes(content + struct.pack("<H", compute_crc(content)))
