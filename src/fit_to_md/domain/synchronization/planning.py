from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from .model import (
    AccountIdentity,
    AcquisitionState,
    ActivityIdentity,
    ConversionState,
    Reference,
    RemoteActivity,
    TrackedActivity,
)
from .periods import DiscoveryExecution


class AccountMismatch(ValueError):
    """The planner was given activity data for a different account."""


@dataclass(frozen=True)
class CatalogDiscovery:
    """Normalized, deduplicated catalog data supplied by the discovery layer."""

    activities: tuple[RemoteActivity, ...]
    complete: bool


@dataclass(frozen=True)
class MissingActivityStart:
    identity: ActivityIdentity


@dataclass(frozen=True)
class ActivityWork:
    activity: RemoteActivity
    acquire: bool
    convert: bool


@dataclass(frozen=True)
class SynchronizationPlan:
    """Selection and checkpoint decision for a catalog snapshot.

    When complete is false, new candidates are provisional and existing work
    can still be identified from the registry.
    """

    new: tuple[ActivityWork, ...]
    known: tuple[RemoteActivity, ...]
    resumes: tuple[ActivityWork, ...]
    anomalies: tuple[MissingActivityStart, ...]
    complete: bool
    reference_to_commit: Reference | None


def plan_synchronization(
    execution: DiscoveryExecution,
    account: AccountIdentity,
    catalog: CatalogDiscovery,
    tracked: Iterable[TrackedActivity],
) -> SynchronizationPlan:
    """Select work from catalog and registry snapshots for the confirmed account."""
    registry: dict[ActivityIdentity, TrackedActivity] = {}
    resumes: dict[ActivityIdentity, ActivityWork] = {}
    for entry in tracked:
        activity = entry.activity
        _require_account(account, activity.identity)
        registry[activity.identity] = entry

    for identity, entry in registry.items():
        if execution.includes_tracked(entry.activity.start_at):
            work = _resume_work(entry, download_only=execution.download_only)
            if work is not None:
                resumes[identity] = work

    new: dict[ActivityIdentity, ActivityWork] = {}
    known: dict[ActivityIdentity, RemoteActivity] = {}
    missing_starts: set[ActivityIdentity] = set()
    for activity in catalog.activities:
        identity = activity.identity
        _require_account(account, identity)
        if activity.start_at is None:
            missing_starts.add(identity)
        elif execution.period.contains(activity.start_at):
            if identity not in registry:
                new[identity] = ActivityWork(
                    activity, True, not execution.download_only
                )
            elif identity not in resumes:
                known[identity] = registry[identity].activity

    complete = catalog.complete and not missing_starts
    return SynchronizationPlan(
        new=tuple(sorted(new.values(), key=_work_order)),
        known=tuple(sorted(known.values(), key=activity_order)),
        resumes=tuple(sorted(resumes.values(), key=_work_order)),
        anomalies=tuple(
            MissingActivityStart(identity) for identity in sorted(missing_starts)
        ),
        complete=complete,
        reference_to_commit=execution.reference_after_discovery(complete=complete),
    )


def _require_account(account: AccountIdentity, identity: ActivityIdentity) -> None:
    if identity.account != account:
        raise AccountMismatch("Activity data belongs to a different account.")


def _resume_work(entry: TrackedActivity, *, download_only: bool) -> ActivityWork | None:
    acquire = entry.acquisition is not AcquisitionState.ACQUIRED
    convert = not download_only and entry.conversion in (
        ConversionState.PENDING,
        ConversionState.FAILED,
    )
    if acquire or convert:
        return ActivityWork(entry.activity, acquire, convert)
    return None


def activity_order(
    activity: RemoteActivity,
) -> tuple[bool, datetime | None, ActivityIdentity]:
    return activity.start_at is None, activity.start_at, activity.identity


def _work_order(work: ActivityWork) -> tuple[bool, datetime | None, ActivityIdentity]:
    return activity_order(work.activity)
