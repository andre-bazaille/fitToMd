"""Read FIT frames with the known COROS compatibility rule."""

import re
import struct
import warnings
from collections.abc import Iterator
from io import BytesIO
from typing import Any

import fitdecode


def compatible_frames(fit_file: Any) -> Iterator[Any]:
    """Accept the known short COROS event payload, preserving other warnings."""
    frames = iter(fit_file)
    while True:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            frame = next(frames, None)
        short_event_data = (
            frame is not None
            and getattr(frame, "frame_type", None) == fitdecode.FIT_FRAME_DEFINITION
            and frame.name == "event"
            and any(
                field.def_num == 3
                and field.size == 1
                and field.base_type.name == "byte"
                for field in frame.field_defs
            )
        )
        for warning in caught:
            if (
                short_event_data
                and len(caught) == 1
                and warning.category is UserWarning
                and re.fullmatch(
                    r"invalid field size 1 in definition message @ \d+ "
                    r"for type uint32 \(expected a multiple of 4\)",
                    str(warning.message),
                )
            ):
                continue
            warnings.warn_explicit(
                warning.message, warning.category, warning.filename, warning.lineno
            )
        if frame is None:
            return
        yield frame


def check_fit_frames(contents: bytes) -> None:
    """Check the record stream without applying report or sport requirements."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            with fitdecode.FitReader(
                BytesIO(contents), check_crc=fitdecode.CrcCheck.RAISE, processor=None
            ) as reader:
                for _ in compatible_frames(reader):
                    pass
        except (
            fitdecode.FitError,
            AssertionError,
            ValueError,
            KeyError,
            IndexError,
            struct.error,
        ):
            raise ValueError("COROS returned a FIT with invalid records.") from None
    if caught:
        raise ValueError("COROS returned a FIT with invalid records.")
