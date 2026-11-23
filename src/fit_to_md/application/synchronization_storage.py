"""Local storage contracts and publication order for synchronization."""

import re
from collections.abc import Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from math import isfinite
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

from fit_to_md.domain.synchronization.model import (
    AccountIdentity,
    AcquisitionState,
    ActivityIdentity,
    ConversionState,
    InvalidInstant,
    Reference,
    RemoteActivity,
    TrackedActivity,
    as_utc,
)


class StorageFailure(Exception):
    """A local storage error with a user-safe diagnostic."""


class OutputBusy(StorageFailure):
    """Another command holds the output lock."""


class OutputAccountMismatch(StorageFailure):
    """The output belongs to a different account."""


class OutputConflict(StorageFailure):
    """A reserved destination is already occupied."""


class InvalidFit(StorageFailure):
    """The FIT envelope or checksum is invalid."""


@dataclass(frozen=True)
class VaultReference:
    service: str
    entry: str


@dataclass(frozen=True)
class FilePaths:
    fit: PurePosixPath
    report: PurePosixPath

    def __post_init__(self) -> None:
        if not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}-[0-9]{2}\.md", self.report.as_posix()
        ) or self.fit != PurePosixPath("fits", self.report.with_suffix(".fit").name):
            raise ValueError("Invalid paths for a COROS activity.")

    @classmethod
    def for_start(cls, start: datetime, zone: ZoneInfo) -> "FilePaths":
        try:
            local = as_utc(start).astimezone(zone)
        except (InvalidInstant, OverflowError):
            raise StorageFailure(
                "This activity's start time cannot be used to name a COROS output."
            ) from None
        minute = f"{local.date().isoformat()} {local:%H-%M}"
        return cls(
            PurePosixPath("fits", f"{minute}.fit"), PurePosixPath(f"{minute}.md")
        )


@dataclass(frozen=True)
class FitIntegrity:
    size: int
    sha256: str

    def __post_init__(self) -> None:
        if self.size <= 0 or not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ValueError("Invalid COROS FIT integrity evidence.")


class Stage(StrEnum):
    ACQUISITION = "acquisition"
    CONVERSION = "conversion"


class FailureReason(StrEnum):
    UNAVAILABLE = "unavailable"
    INVALID_FIT = "invalid_fit"
    CONFLICT = "conflict"
    STORAGE = "storage"
    CONVERSION_FAILED = "conversion_failed"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class Attempts:
    count: int = 0
    last_at: datetime | None = None
    error: FailureReason | None = None


@dataclass(frozen=True)
class ConversionOptions:
    workout_report: Literal["off", "laps"] = "off"
    hr_zone_boundaries: tuple[int, int, int, int] | None = None
    weather_mode: Literal["fit", "auto"] = "fit"
    elevation_source: Literal["fit", "dem", "hybrid"] = "fit"
    elevation_smoothing_distance: float = 170.0
    elevation_min_change: float = 0.4
    dem_sample_distance: float = 30.0
    dynamics_step_size: int = 30
    opentopodata_dataset: str = "eudem25m"
    opentopodata_base_url: str = "https://api.opentopodata.org"

    def __post_init__(self) -> None:
        if (
            self.workout_report not in ("off", "laps")
            or self.weather_mode not in ("fit", "auto")
            or self.elevation_source not in ("fit", "dem", "hybrid")
        ):
            raise ValueError("Unknown COROS conversion option.")
        if (
            type(self.dynamics_step_size) is not int
            or self.dynamics_step_size <= 0
            or not self.opentopodata_dataset
            or not self.opentopodata_base_url
        ):
            raise ValueError("Invalid COROS conversion settings.")
        for value in (
            self.elevation_smoothing_distance,
            self.elevation_min_change,
            self.dem_sample_distance,
        ):
            if not isinstance(value, (int, float)) or not isfinite(value):
                raise ValueError("COROS conversion options must use finite distances.")
        zones = self.hr_zone_boundaries
        if zones is not None and (
            len(zones) != 4 or any(type(zone) is not int for zone in zones)
        ):
            raise ValueError(
                "COROS conversion options require four integer HR boundaries."
            )


@dataclass(frozen=True)
class RegistryEntry:
    tracked: TrackedActivity
    discovered_at: datetime
    paths: FilePaths | None
    fit_integrity: FitIntegrity | None
    conversion_options: ConversionOptions | None
    acquisition_attempts: Attempts
    conversion_attempts: Attempts


@dataclass(frozen=True)
class RegistrySnapshot:
    account: AccountIdentity
    vault: VaultReference
    reference: Reference | None
    reference_zone: str | None
    last_complete_discovery: datetime | None
    entries: tuple[RegistryEntry, ...]


class RegistryReader(Protocol):
    def snapshot(self) -> RegistrySnapshot: ...

    def reference(self) -> Reference | None: ...

    def entry(self, identity: ActivityIdentity) -> RegistryEntry: ...


class Registry(RegistryReader, Protocol):
    def save_discovery(
        self,
        activities: Iterable[RemoteActivity],
        *,
        discovered_at: datetime,
        complete: bool,
        reference: Reference | None = None,
        zone: ZoneInfo | None = None,
    ) -> None: ...

    def save_reference(self, reference: Reference, zone: ZoneInfo) -> None: ...

    def reserve(self, identity: ActivityIdentity, paths: FilePaths) -> None: ...

    def record_attempt(
        self, identity: ActivityIdentity, stage: Stage, at: datetime
    ) -> None: ...

    def record_failure(
        self, identity: ActivityIdentity, stage: Stage, reason: FailureReason
    ) -> None: ...

    def record_conversion_attempt(
        self, identity: ActivityIdentity, at: datetime, options: ConversionOptions
    ) -> None: ...

    def record_fit_integrity(
        self, identity: ActivityIdentity, integrity: FitIntegrity
    ) -> None: ...

    def discard_fit_integrity(self, identity: ActivityIdentity) -> None: ...

    def reset_acquisition(self, identity: ActivityIdentity) -> None: ...

    def record_acquired(self, identity: ActivityIdentity) -> None: ...

    def record_converted(
        self, identity: ActivityIdentity, options: ConversionOptions
    ) -> None: ...


class OutputLock(Protocol):
    def hold(self, *, read_only: bool = False) -> AbstractContextManager[None]: ...


class StagedFit(Protocol):
    @property
    def integrity(self) -> FitIntegrity: ...

    def publish(self) -> None: ...


class OutputFileReader(Protocol):
    def require_available(self, path: PurePosixPath) -> None: ...

    def has_fit(self, path: PurePosixPath, integrity: FitIntegrity) -> bool: ...

    def has_report(self, path: PurePosixPath) -> bool: ...


class OutputFiles(OutputFileReader, Protocol):
    def local_fit(self, path: PurePosixPath) -> Path: ...

    def stage_fit(
        self, path: PurePosixPath, contents: bytes
    ) -> AbstractContextManager[StagedFit]: ...

    def archive_damaged_fit(self, path: PurePosixPath) -> None: ...

    def publish_report(self, path: PurePosixPath, markdown: str) -> None: ...


def has_verified_fit(entry: RegistryEntry, files: OutputFileReader) -> bool:
    return (
        entry.paths is not None
        and entry.fit_integrity is not None
        and files.has_fit(entry.paths.fit, entry.fit_integrity)
    )


def has_registered_report(entry: RegistryEntry, files: OutputFileReader) -> bool:
    return (
        entry.tracked.conversion is ConversionState.CONVERTED
        and entry.paths is not None
        and files.has_report(entry.paths.report)
    )


@dataclass(frozen=True)
class PendingWork:
    activities: int
    fits: int
    reports: int
    unsupported: int


def inspect_pending_work(
    entries: Iterable[RegistryEntry], files: OutputFileReader
) -> PendingWork:
    activities = fits = reports = unsupported = 0
    for entry in entries:
        needs_fit = not has_verified_fit(entry, files)
        terminal = entry.tracked.conversion is ConversionState.UNSUPPORTED
        needs_report = not terminal and not has_registered_report(entry, files)
        fits += needs_fit
        reports += needs_report
        activities += needs_fit or needs_report
        unsupported += terminal
    return PendingWork(activities, fits, reports, unsupported)


class OutputStorage:
    """Coordinate registry writes and filesystem publication under the output lock."""

    def __init__(self, registry: Registry, files: OutputFiles) -> None:
        self.registry = registry
        self.files = files

    def reserve(self, identity: ActivityIdentity, zone: ZoneInfo) -> FilePaths:
        entry = self.registry.entry(identity)
        if entry.paths is not None:
            return entry.paths
        start = entry.tracked.activity.start_at
        if start is None:
            raise StorageFailure(
                "Cannot reserve a path without an activity start time."
            )
        paths = FilePaths.for_start(start, zone)
        self.files.require_available(paths.fit)
        self.registry.reserve(identity, paths)
        return paths

    def publish_fit(self, identity: ActivityIdentity, contents: bytes) -> FitIntegrity:
        paths = self._reserved_paths(self.registry.entry(identity))
        with self.files.stage_fit(paths.fit, contents) as staged:
            self.registry.record_fit_integrity(identity, staged.integrity)
            try:
                staged.publish()
            except OutputConflict:
                self.registry.discard_fit_integrity(identity)
                raise
            self.registry.record_acquired(identity)
            return staged.integrity

    def reset_damaged_acquisition(self, entry: RegistryEntry) -> None:
        paths = self._reserved_paths(entry)
        self.files.archive_damaged_fit(paths.fit)
        self.registry.reset_acquisition(entry.tracked.activity.identity)

    def publish_report(
        self, identity: ActivityIdentity, markdown: str, options: ConversionOptions
    ) -> None:
        entry = self.registry.entry(identity)
        if entry.tracked.conversion is ConversionState.UNSUPPORTED:
            raise StorageFailure("This activity's conversion is unsupported.")
        if entry.tracked.acquisition is not AcquisitionState.ACQUIRED:
            raise StorageFailure("Acquire the COROS FIT before publishing its report.")
        paths = self._reserved_paths(entry)
        self.files.publish_report(paths.report, markdown)
        self.registry.record_converted(identity, options)

    @staticmethod
    def _reserved_paths(entry: RegistryEntry) -> FilePaths:
        paths = entry.paths
        if paths is None:
            raise StorageFailure("Reserve the activity's output paths before writing.")
        return paths
