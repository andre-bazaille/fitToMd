from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from fit_to_md.domain.synchronization.model import (
    AccountIdentity,
    AcquisitionState,
    ActivityIdentity,
    ConversionState,
    InvalidInstant,
    Reference,
    RemoteActivity,
    TrackedActivity,
)
from fit_to_md.domain.synchronization.periods import (
    DiscoveryExecution,
    SelectionOptions,
    resolve_selection,
)
from fit_to_md.domain.synchronization.planning import (
    AccountMismatch,
    ActivityWork,
    CatalogDiscovery,
    MissingActivityStart,
    plan_synchronization,
)

ACCOUNT = AccountIdentity("coros", "region-1", "synthetic subject")
REFERENCE = Reference(datetime(2026, 11, 15, 15, tzinfo=UTC))
LAUNCH = datetime(2026, 11, 16, 14, tzinfo=UTC)
BOUND = datetime(2026, 11, 15, tzinfo=UTC)


def _execution(
    options: SelectionOptions | None = None,
    reference: Reference | None = REFERENCE,
) -> DiscoveryExecution:
    result = resolve_selection(
        options or SelectionOptions(),
        reference,
        launched_at=LAUNCH,
        zone=ZoneInfo("UTC"),
    )
    assert isinstance(result, DiscoveryExecution)
    return result


def _activity(
    name: str,
    start: datetime | None = BOUND,
    *,
    account: AccountIdentity = ACCOUNT,
    sport: int = 100,
) -> RemoteActivity:
    return RemoteActivity(ActivityIdentity(account, name, sport), start)


def test_identity_is_scoped_and_opaque() -> None:
    key = _activity("activity-1").identity
    assert key == _activity("activity-1", LAUNCH).identity
    assert key != _activity("activity-1", sport=101).identity
    assert (
        key
        != _activity(
            "activity-1", account=AccountIdentity("coros", "region-2", ACCOUNT.subject)
        ).identity
    )
    assert (
        key
        != _activity(
            "activity-1",
            account=AccountIdentity("coros", ACCOUNT.issuer, "synthetic subject "),
        ).identity
    )
    assert (
        key
        != _activity(
            "activity-1",
            account=AccountIdentity("other", ACCOUNT.issuer, ACCOUNT.subject),
        ).identity
    )
    assert ACCOUNT.subject not in repr(key)
    assert key.remote_id not in repr(key)


@pytest.mark.parametrize(
    ("provider", "issuer", "subject"),
    [("", "region", "id"), ("coros", "", "id"), ("coros", "region", "")],
)
def test_account_identity_requires_complete_key(
    provider: str, issuer: str, subject: str
) -> None:
    with pytest.raises(ValueError, match="requires a provider, issuer and subject"):
        AccountIdentity(provider, issuer, subject)


def test_activity_requires_identity_and_normalizes_start_when_present() -> None:
    with pytest.raises(ValueError, match="requires a remote identifier"):
        _activity("")
    with pytest.raises(InvalidInstant):
        _activity("naive", datetime(2026, 11, 15))
    with pytest.raises(InvalidInstant):
        Reference(datetime(2026, 11, 15))
    local = datetime(2026, 11, 15, 1, tzinfo=ZoneInfo("Europe/Paris"))
    activity = _activity("local", local)
    assert activity.start_at == BOUND
    assert activity.start_at is not None and activity.start_at.tzinfo is UTC


def test_incremental_catalog_filters_on_start_and_explicit_can_find_older_upload() -> (
    None
):
    # This activity started the previous evening and lasted past local midnight.
    overnight = _activity("overnight", BOUND - timedelta(minutes=30))
    at_start = _activity("at-start")
    same_start = _activity("same-start")
    before_launch = _activity("before-launch", LAUNCH - timedelta(microseconds=1))
    at_launch = _activity("at-launch", LAUNCH)
    catalog = CatalogDiscovery(
        (at_launch, before_launch, overnight, same_start, at_start), True
    )

    incremental = plan_synchronization(_execution(), ACCOUNT, catalog, ())
    assert tuple(work.activity for work in incremental.new) == (
        at_start,
        same_start,
        before_launch,
    )
    explicit = plan_synchronization(
        _execution(SelectionOptions(since="2026-11-14")), ACCOUNT, catalog, ()
    )
    assert tuple(work.activity for work in explicit.new) == (
        overnight,
        at_start,
        same_start,
        before_launch,
    )


def test_recognizes_known_activity_using_full_identity_and_preserves_recorded_metadata() -> (
    None
):
    original = _activity("same-id")
    revised = _activity("same-id", BOUND + timedelta(hours=1))
    other_sport = _activity("same-id", sport=101)
    result = plan_synchronization(
        _execution(),
        ACCOUNT,
        CatalogDiscovery((revised, other_sport), True),
        (
            TrackedActivity(
                original, AcquisitionState.ACQUIRED, ConversionState.CONVERTED
            ),
        ),
    )
    assert result.known == (original,)
    assert result.new == (ActivityWork(other_sport, acquire=True, convert=True),)
    assert result.resumes == ()


def test_resume_in_scope_for_each_mode_using_recorded_start() -> None:
    old_fit = _activity("old-fit", BOUND - timedelta(days=2))
    old_download = _activity("old-download", BOUND - timedelta(days=1))
    failed_conversion = _activity("failed-conversion")
    pending = _activity("pending")
    unsupported = _activity("unsupported")
    tracked = (
        TrackedActivity(pending),
        TrackedActivity(old_fit, AcquisitionState.ACQUIRED),
        TrackedActivity(old_download, AcquisitionState.FAILED),
        TrackedActivity(
            failed_conversion, AcquisitionState.ACQUIRED, ConversionState.FAILED
        ),
        TrackedActivity(
            unsupported, AcquisitionState.ACQUIRED, ConversionState.UNSUPPORTED
        ),
    )
    revised = _activity(old_fit.identity.remote_id, BOUND)
    catalog = CatalogDiscovery((unsupported, revised, pending), True)
    in_window = (
        ActivityWork(failed_conversion, acquire=False, convert=True),
        ActivityWork(pending, acquire=True, convert=True),
    )

    incremental = plan_synchronization(_execution(), ACCOUNT, catalog, tracked)
    assert incremental.resumes == (
        ActivityWork(old_fit, acquire=False, convert=True),
        ActivityWork(old_download, acquire=True, convert=True),
        *in_window,
    )
    assert incremental.known == (unsupported,)
    explicit = plan_synchronization(
        _execution(SelectionOptions(last_days=2)), ACCOUNT, catalog, tracked
    )
    assert explicit.resumes == in_window
    assert explicit.known == (old_fit, unsupported)
    assert explicit.new == incremental.new == ()


def test_download_only_selects_acquisition_and_later_normal_run_selects_conversion() -> (
    None
):
    acquired = _activity("acquired")
    failed = _activity("failed")
    new = _activity("new")
    tracked = (
        TrackedActivity(acquired, AcquisitionState.ACQUIRED),
        TrackedActivity(failed),
    )
    catalog = CatalogDiscovery((acquired, new), True)
    only = plan_synchronization(
        _execution(SelectionOptions(download_only=True)), ACCOUNT, catalog, tracked
    )
    assert only.new == (ActivityWork(new, acquire=True, convert=False),)
    assert only.resumes == (ActivityWork(failed, acquire=True, convert=False),)
    assert only.known == (acquired,)
    following = plan_synchronization(
        _execution(),
        ACCOUNT,
        catalog,
        (*tracked, TrackedActivity(new, AcquisitionState.ACQUIRED)),
    )
    assert following.new == ()
    assert ActivityWork(new, acquire=False, convert=True) in following.resumes


def test_first_explicit_reference_is_available_before_discovery_and_advances_on_success() -> (
    None
):
    execution = _execution(SelectionOptions(since="2026-11-15"), None)
    assert execution.initial_reference == Reference(BOUND)

    incomplete = plan_synchronization(
        execution, ACCOUNT, CatalogDiscovery((), False), ()
    )
    assert not incomplete.complete
    assert incomplete.reference_to_commit is None
    assert execution.initial_reference == Reference(BOUND)

    complete = plan_synchronization(execution, ACCOUNT, CatalogDiscovery((), True), ())
    assert complete.complete
    assert complete.reference_to_commit == Reference(LAUNCH)


@pytest.mark.parametrize(
    ("options", "reference"),
    [
        (SelectionOptions(last_days=2), REFERENCE),
        (SelectionOptions(last_days=2, dry_run=True), None),
        (SelectionOptions(dry_run=True), REFERENCE),
    ],
)
def test_later_explicit_or_dry_run_keeps_reference(
    options: SelectionOptions, reference: Reference | None
) -> None:
    execution = _execution(options, reference)
    new = _activity("new")
    result = plan_synchronization(
        execution, ACCOUNT, CatalogDiscovery((new,), True), ()
    )
    assert result.complete
    assert result.new == (ActivityWork(new, acquire=True, convert=True),)
    assert execution.initial_reference is None
    assert result.reference_to_commit is None


def test_missing_date_makes_discovery_incomplete_and_preserves_earlier_work() -> None:
    undated = _activity("undated", None)
    previous = _activity("previous", BOUND - timedelta(days=2))
    execution = _execution()
    result = plan_synchronization(
        execution,
        ACCOUNT,
        CatalogDiscovery((undated,), True),
        (TrackedActivity(previous),),
    )
    assert result.anomalies == (MissingActivityStart(undated.identity),)
    assert not result.complete
    assert result.reference_to_commit is None
    assert result.new == ()
    assert result.resumes == (ActivityWork(previous, acquire=True, convert=True),)
    assert execution.initial_reference is None

    complete = plan_synchronization(execution, ACCOUNT, CatalogDiscovery((), True), ())
    assert complete.reference_to_commit == Reference(LAUNCH)


@pytest.mark.parametrize("in_catalog", [True, False])
def test_rejects_activity_from_different_account(in_catalog: bool) -> None:
    other = _activity(
        "other", account=AccountIdentity("coros", ACCOUNT.issuer, "another subject")
    )
    catalog = CatalogDiscovery((other,) if in_catalog else (), True)
    tracked = () if in_catalog else (TrackedActivity(other),)
    with pytest.raises(AccountMismatch, match="different account"):
        plan_synchronization(_execution(), ACCOUNT, catalog, tracked)
