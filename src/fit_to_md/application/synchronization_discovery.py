"""Discover catalogue coverage and build a read-only synchronization plan."""

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Protocol
from zoneinfo import ZoneInfo

from fit_to_md.application.synchronization_storage import (
    FilePaths,
    OutputConflict,
    OutputFileReader,
    RegistrySnapshot,
    has_registered_report,
    has_verified_fit,
)
from fit_to_md.domain.synchronization.model import (
    AccountIdentity,
    AcquisitionState,
    ActivityIdentity,
    ConversionState,
    RemoteActivity,
)
from fit_to_md.domain.synchronization.periods import (
    DiscoveryExecution,
    Period,
    SelectionError,
    SelectionErrorCode,
)
from fit_to_md.domain.synchronization.planning import (
    CatalogDiscovery,
    activity_order,
    plan_synchronization,
)

CATALOGUE_LIMIT = 20
_DAY = timedelta(days=1)


@dataclass(frozen=True)
class CatalogueDates:
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("Catalogue dates must be in chronological order.")

    @classmethod
    def covering(cls, period: Period) -> "CatalogueDates":
        try:
            return cls(period.start.date() - _DAY, period.end.date() + _DAY)
        except OverflowError:
            raise SelectionError(
                SelectionErrorCode.UNREPRESENTABLE_BOUND,
                "The period cannot be covered by COROS catalogue dates.",
            ) from None


@dataclass(frozen=True)
class CataloguePage:
    dates: CatalogueDates | None
    activities: tuple[RemoteActivity, ...]


class CatalogueFailureKind(StrEnum):
    NETWORK = "COROS could not be reached over HTTPS."
    HTTP = "COROS returned an HTTP error."
    AUTHENTICATION = "COROS refused the session. Run 'fit-to-md coros login'."
    RPC = "COROS returned a JSON-RPC error."
    TOOL = "COROS refused the catalogue query."
    RESPONSE = "COROS returned an unrecognized or unusable catalogue response."
    BOUNDS = "COROS did not confirm the requested dates."
    SATURATION = "The COROS catalogue is saturated for this day."


class CatalogueFailure(Exception):
    def __init__(
        self,
        kind: CatalogueFailureKind,
        *,
        status: int | None = None,
        retry_at: datetime | None = None,
        defer_remaining: bool = False,
    ) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.status = status
        self.retry_at = retry_at
        self.defer_remaining = defer_remaining


class CatalogueReader(Protocol):
    def __call__(self, dates: CatalogueDates) -> CataloguePage: ...


@dataclass(frozen=True)
class CatalogueIssue:
    dates: CatalogueDates
    failure: CatalogueFailure


@dataclass(frozen=True)
class DiscoveryResult:
    catalog: CatalogDiscovery
    issues: tuple[CatalogueIssue, ...]


def discover_catalogue(dates: CatalogueDates, read: CatalogueReader) -> DiscoveryResult:
    """Keep verified leaf pages and discard the rows of every subdivided parent."""
    pending = [dates]
    issues: list[CatalogueIssue] = []
    activities: dict[ActivityIdentity, RemoteActivity] = {}
    while pending:
        requested = pending.pop()
        try:
            page = read(requested)
            if page.dates != requested:
                raise CatalogueFailure(CatalogueFailureKind.BOUNDS)
            count = len(page.activities)
            if count > CATALOGUE_LIMIT:
                raise CatalogueFailure(CatalogueFailureKind.RESPONSE)
            if count == CATALOGUE_LIMIT:
                if requested.start == requested.end:
                    issues.append(
                        CatalogueIssue(
                            requested, CatalogueFailure(CatalogueFailureKind.SATURATION)
                        )
                    )
                else:
                    middle = requested.start + (requested.end - requested.start) // 2
                    pending.append(CatalogueDates(middle + _DAY, requested.end))
                    pending.append(CatalogueDates(requested.start, middle))
                    continue
        except CatalogueFailure as failure:
            issues.append(CatalogueIssue(requested, failure))
            if (
                failure.kind is CatalogueFailureKind.AUTHENTICATION
                or failure.defer_remaining
            ):
                break
            continue

        for activity in page.activities:
            previous = activities.get(activity.identity)
            if previous is not None and previous.start_at != activity.start_at:
                activity = RemoteActivity(activity.identity, None)
            activities[activity.identity] = activity

    return DiscoveryResult(
        CatalogDiscovery(
            tuple(activities.values()),
            not issues
            and all(item.start_at is not None for item in activities.values()),
        ),
        tuple(issues),
    )


class PreviewAction(StrEnum):
    KNOWN = "Known"
    NEW = "New"
    RESUME = "Resume"


@dataclass(frozen=True)
class PreviewEntry:
    activity: RemoteActivity
    action: PreviewAction
    acquire: bool = False
    convert: bool = False
    conflicts: tuple[PurePosixPath, ...] = ()


@dataclass(frozen=True)
class SynchronizationPreview:
    complete: bool
    entries: tuple[PreviewEntry, ...]
    issues: tuple[CatalogueIssue, ...]
    unclassifiable: int

    @property
    def estimated_acquisitions(self) -> int:
        return sum(
            entry.acquire and not any(path.suffix == ".fit" for path in entry.conflicts)
            for entry in self.entries
        )


def preview_synchronization(
    execution: DiscoveryExecution,
    account: AccountIdentity,
    read: CatalogueReader,
    snapshot: RegistrySnapshot | None,
    destinations: OutputFileReader,
) -> SynchronizationPreview:
    dates = CatalogueDates.covering(execution.period)
    discovery = discover_catalogue(dates, read)
    registry = snapshot.entries if snapshot is not None else ()
    unclassifiable = {
        row.identity for row in discovery.catalog.activities if row.start_at is None
    }
    tracked = []
    reusable = set()
    repairable = set()
    for row in registry:
        state = row.tracked
        activity = state.activity
        if activity.identity in unclassifiable:
            continue
        if (
            execution.includes_tracked(activity.start_at)
            and row.fit_integrity is not None
        ):
            verified = has_verified_fit(row, destinations)
            if state.acquisition is AcquisitionState.ACQUIRED and not verified:
                repairable.add(activity.identity)
                state = replace(state, acquisition=AcquisitionState.PENDING)
            elif verified:
                reusable.add(activity.identity)
        if (
            not execution.download_only
            and execution.includes_tracked(activity.start_at)
            and state.conversion is ConversionState.CONVERTED
            and not has_registered_report(row, destinations)
        ):
            state = replace(state, conversion=ConversionState.PENDING)
        tracked.append(state)
    plan = plan_synchronization(execution, account, discovery.catalog, tracked)
    paths = {
        row.tracked.activity.identity: row.paths
        for row in registry
        if row.paths is not None
    }
    entries = [PreviewEntry(activity, PreviewAction.KNOWN) for activity in plan.known]
    for action, work in (
        (PreviewAction.NEW, plan.new),
        (PreviewAction.RESUME, plan.resumes),
    ):
        for item in work:
            if item.activity.start_at is None and item.activity.identity not in paths:
                unclassifiable.add(item.activity.identity)
                continue
            entries.append(
                PreviewEntry(
                    item.activity,
                    action,
                    item.acquire and item.activity.identity not in reusable,
                    item.convert,
                )
            )
    entries.sort(key=lambda row: activity_order(row.activity))
    conflicts = _find_conflicts(
        entries, paths, execution.period.zone, destinations, repairable
    )
    return SynchronizationPreview(
        plan.complete,
        tuple(
            replace(row, conflicts=conflicts.get(row.activity.identity, ()))
            for row in entries
        ),
        discovery.issues,
        len(unclassifiable),
    )


def _find_conflicts(
    entries: list[PreviewEntry],
    reserved: dict[ActivityIdentity, FilePaths],
    zone: ZoneInfo,
    destinations: OutputFileReader,
    repairable: set[ActivityIdentity],
) -> dict[ActivityIdentity, tuple[PurePosixPath, ...]]:
    owners = {paths: identity for identity, paths in reserved.items()}
    conflicts: dict[ActivityIdentity, tuple[PurePosixPath, ...]] = {}
    for entry in entries:
        if not (entry.acquire or entry.convert):
            continue
        identity = entry.activity.identity
        paths = reserved.get(identity)
        if paths is None:
            assert entry.activity.start_at is not None
            paths = FilePaths.for_start(entry.activity.start_at, zone)
        if entry.acquire:
            owner = owners.get(paths)
            if owner is not None and owner != identity:
                conflicts[identity] = (paths.fit,)
                continue
            if identity not in repairable:
                try:
                    destinations.require_available(paths.fit)
                except OutputConflict:
                    conflicts[identity] = (paths.fit,)
                    continue
            owners[paths] = identity
        if entry.convert:
            try:
                destinations.require_available(paths.report)
            except OutputConflict:
                conflicts[identity] = (paths.report,)
    return conflicts
