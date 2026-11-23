"""Resolve the local IANA timezone for COROS calendar operations."""

import warnings
from importlib import import_module
from zoneinfo import ZoneInfo

from fit_to_md.domain.coros_connection import ConnectionFailure


class SystemLocalTimezone:
    def __init__(self, *, extra: str) -> None:
        self._extra = extra

    def resolve(self) -> ZoneInfo:
        try:
            with warnings.catch_warnings(record=True) as reported:
                warnings.simplefilter("always")
                zone = import_module("tzlocal").get_localzone()
            if reported or not isinstance(zone, ZoneInfo):
                raise ValueError
            return zone
        except Exception:
            raise ConnectionFailure(
                f"Local timezone unavailable. Install [{self._extra}] and configure the OS timezone."
            ) from None
