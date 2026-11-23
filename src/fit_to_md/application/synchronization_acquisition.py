"""Discover and acquire FIT files for a synchronization run."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from fit_to_md.application.synchronization import Clock
from fit_to_md.application.synchronization_discovery import (
    CatalogueDates,
    CatalogueIssue,
    CatalogueReader,
    discover_catalogue,
)
from fit_to_md.application.synchronization_quota import (
    BudgetedFitRequests,
    QuotaRegistry,
)
from fit_to_md.application.synchronization_storage import (
    FailureReason,
    InvalidFit,
    OutputConflict,
    OutputStorage,
    RegistryEntry,
    Stage,
    StorageFailure,
    has_verified_fit,
)
from fit_to_md.domain.coros_connection import ConnectionFailure, ReauthorizationRequired
from fit_to_md.domain.synchronization.model import (
    AcquisitionState,
    ActivityIdentity,
    RemoteActivity,
)
from fit_to_md.domain.synchronization.periods import DiscoveryExecution
from fit_to_md.domain.synchronization.planning import (
    activity_order,
    plan_synchronization,
)
from fit_to_md.domain.synchronization.quota import (
    FitQuotaExceeded,
    ServerFitQuotaExceeded,
)

type FitRequest = Callable[[ActivityIdentity], bytes]
type QuotaStop = FitQuotaExceeded | ServerFitQuotaExceeded


class AcquisitionProgress(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


type ProgressReporter = Callable[[RemoteActivity, AcquisitionProgress, str], None]


@dataclass
class AcquisitionResult:
    complete: bool = False
    selected: int = 0
    reused: int = 0
    acquired: int = 0
    failed: int = 0
    deferred_for_quota: int = 0
    catalogue_issues: tuple[CatalogueIssue, ...] = ()
    unclassifiable: int = 0
    quota_stop: QuotaStop | None = None
    stopped: StorageFailure | ReauthorizationRequired | None = None
    interrupted: bool = False

    @property
    def succeeded(self) -> bool:
        return (
            self.complete
            and self.failed == 0
            and self.quota_stop is None
            and self.stopped is None
            and not self.interrupted
        )


def download_synchronization(
    execution: DiscoveryExecution,
    output: OutputStorage,
    quota: QuotaRegistry,
    read_catalogue: CatalogueReader,
    request_fit: FitRequest,
    clock: Clock,
    report: ProgressReporter,
    *,
    on_fit_available: Callable[[RemoteActivity], None] | None = None,
    needs_local_work: Callable[[RegistryEntry], bool] | None = None,
) -> AcquisitionResult:
    """Run acquisition under the caller's exclusive output lock."""
    result = AcquisitionResult()
    try:
        snapshot = output.registry.snapshot()
        if snapshot.account != quota.account:
            raise StorageFailure(
                "The COROS output and FIT quota belong to different accounts."
            )
        period = execution.period
        dates = CatalogueDates.covering(period)
        initial_reference = execution.initial_reference
        if initial_reference is not None:
            output.registry.save_reference(initial_reference, period.zone)
        discovery = discover_catalogue(dates, read_catalogue)
        plan = plan_synchronization(
            execution,
            snapshot.account,
            discovery.catalog,
            (entry.tracked for entry in snapshot.entries),
        )
        candidates = tuple(work.activity for work in plan.new)
        known = (
            item for item in plan.known if execution.includes_tracked(item.start_at)
        )
        resumes = (work.activity for work in plan.resumes)
        activities = {item.identity: item for item in (*candidates, *known, *resumes)}
        result.complete = plan.complete
        result.selected = len(activities)
        result.catalogue_issues = discovery.issues
        result.unclassifiable = len(plan.anomalies)
        if candidates or plan.complete:
            output.registry.save_discovery(
                candidates,
                discovered_at=period.end,
                complete=plan.complete,
                reference=plan.reference_to_commit,
                zone=period.zone if plan.reference_to_commit is not None else None,
            )
        if not plan.complete:
            return result

        for entry in snapshot.entries:
            activity = entry.tracked.activity
            if (
                activity.identity not in activities
                and execution.includes_tracked(activity.start_at)
                and entry.tracked.acquisition is AcquisitionState.ACQUIRED
                and (
                    not has_verified_fit(entry, output.files)
                    or (needs_local_work is not None and needs_local_work(entry))
                )
            ):
                activities[activity.identity] = activity
        result.selected = len(activities)

        requests = BudgetedFitRequests(
            quota, output.registry, output.files, request_fit
        )
        for activity in sorted(activities.values(), key=activity_order):
            if _acquire(activity, execution, output, requests, clock, report, result):
                if on_fit_available is not None:
                    on_fit_available(activity)
    except (StorageFailure, ReauthorizationRequired) as error:
        result.stopped = error
    except KeyboardInterrupt:
        result.interrupted = True
    return result


def _acquire(
    activity: RemoteActivity,
    execution: DiscoveryExecution,
    output: OutputStorage,
    requests: BudgetedFitRequests,
    clock: Clock,
    report: ProgressReporter,
    result: AcquisitionResult,
) -> bool:
    identity = activity.identity
    registry = output.registry

    def before_request() -> None:
        registry.record_attempt(identity, Stage.ACQUISITION, clock.now())
        report(activity, AcquisitionProgress.STARTED, "")

    try:
        entry = registry.entry(identity)
        if has_verified_fit(entry, output.files):
            if entry.tracked.acquisition is not AcquisitionState.ACQUIRED:
                registry.record_acquired(identity)
            result.reused += 1
            return True
        if entry.paths is None and activity.start_at is None:
            result.failed += 1
            report(activity, AcquisitionProgress.FAILED, "Missing activity start time.")
            return False
        if result.quota_stop is not None:
            result.deferred_for_quota += 1
            return False
        if entry.tracked.acquisition is AcquisitionState.ACQUIRED:
            output.reset_damaged_acquisition(entry)
        paths = output.reserve(identity, execution.period.zone)
        if entry.paths is not None:
            output.files.require_available(paths.fit)
        contents = requests.request(identity, before_request=before_request)
        if contents is None:
            registry.record_acquired(identity)
            result.reused += 1
            return True
        output.publish_fit(identity, contents)
    except (FitQuotaExceeded, ServerFitQuotaExceeded) as error:
        result.quota_stop = error
        result.deferred_for_quota += 1
        return False
    except (InvalidFit, OutputConflict, ConnectionFailure) as error:
        reason = FailureReason.UNAVAILABLE
        if isinstance(error, InvalidFit):
            reason = FailureReason.INVALID_FIT
        elif isinstance(error, OutputConflict):
            reason = FailureReason.CONFLICT
        result.failed += 1
        if isinstance(error, ReauthorizationRequired):
            report(activity, AcquisitionProgress.FAILED, "")
            raise
        report(activity, AcquisitionProgress.FAILED, str(error))
        registry.record_failure(identity, Stage.ACQUISITION, reason)
        return False
    except StorageFailure:
        result.failed += 1
        raise
    result.acquired += 1
    report(activity, AcquisitionProgress.SUCCEEDED, "")
    return True
