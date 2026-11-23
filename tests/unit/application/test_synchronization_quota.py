from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, create_autospec
from zoneinfo import ZoneInfo

import pytest

from fit_to_md.application.synchronization_quota import (
    BudgetedFitRequests,
    QuotaRegistry,
    QuotaUnavailable,
)
from fit_to_md.application.synchronization_storage import (
    Attempts,
    FilePaths,
    FitIntegrity,
    OutputFiles,
    RegistryEntry,
    RegistryReader,
    StorageFailure,
)
from fit_to_md.domain.synchronization.model import (
    AccountIdentity,
    ActivityIdentity,
    RemoteActivity,
    TrackedActivity,
)
from fit_to_md.domain.synchronization.quota import (
    FitQuotaExceeded,
    ServerFitQuotaExceeded,
)

ACCOUNT = AccountIdentity("coros", "issuer", "subject")
IDENTITY = ActivityIdentity(ACCOUNT, "remote", 100)
NOW = datetime(2026, 11, 21, tzinfo=UTC)
PATHS = FilePaths.for_start(NOW, ZoneInfo("UTC"))
INTEGRITY = FitIntegrity(100, "a" * 64)


def setup_guard() -> tuple[BudgetedFitRequests, Mock]:
    calls = Mock()
    calls.quota = create_autospec(QuotaRegistry, instance=True, spec_set=True)
    calls.quota.account = ACCOUNT
    calls.registry = create_autospec(RegistryReader, instance=True, spec_set=True)
    calls.registry.entry.return_value = RegistryEntry(
        TrackedActivity(RemoteActivity(IDENTITY, NOW)),
        NOW,
        PATHS,
        INTEGRITY,
        None,
        Attempts(),
        Attempts(),
    )
    calls.files = create_autospec(OutputFiles, instance=True, spec_set=True)
    calls.files.has_fit.return_value = False
    calls.request_fit.return_value = b"remote contents"
    return BudgetedFitRequests(
        calls.quota, calls.registry, calls.files, calls.request_fit
    ), calls


def test_reusable_local_file_never_reserves_or_calls_coros() -> None:
    guard, calls = setup_guard()
    calls.files.has_fit.return_value = True
    assert guard.request(IDENTITY) is None
    calls.files.has_fit.assert_called_once_with(PATHS.fit, INTEGRITY)
    calls.quota.reserve_fit.assert_not_called()
    calls.request_fit.assert_not_called()


def test_reservation_commits_before_call_and_timeout_is_not_retried_or_refunded() -> (
    None
):
    guard, calls = setup_guard()

    def timeout(identity: ActivityIdentity) -> bytes:
        calls.quota.reserve_fit.assert_called_once_with()
        assert identity == IDENTITY
        raise TimeoutError

    calls.request_fit.side_effect = timeout
    with pytest.raises(TimeoutError):
        guard.request(IDENTITY)
    calls.request_fit.assert_called_once_with(IDENTITY)
    calls.quota.reserve_fit.assert_called_once_with()

    calls.request_fit.side_effect = None
    assert guard.request(IDENTITY) == b"remote contents"
    assert calls.quota.reserve_fit.call_count == 2


@pytest.mark.parametrize("retry_at", [None, NOW + timedelta(minutes=10)])
def test_server_refusal_stops_remote_calls_and_preserves_local_reuse(
    retry_at: datetime | None,
) -> None:
    guard, calls = setup_guard()
    calls.request_fit.side_effect = ServerFitQuotaExceeded(retry_at)
    with pytest.raises(ServerFitQuotaExceeded) as first:
        guard.request(IDENTITY)
    with pytest.raises(ServerFitQuotaExceeded) as later:
        guard.request(IDENTITY)
    assert first.value.retry_at == later.value.retry_at == retry_at
    calls.quota.reserve_fit.assert_called_once_with()
    calls.request_fit.assert_called_once_with(IDENTITY)
    calls.files.has_fit.return_value = True
    assert guard.request(IDENTITY) is None


@pytest.mark.parametrize(
    "failure", [FitQuotaExceeded(NOW), QuotaUnavailable("Unavailable")]
)
def test_local_quota_refusal_happens_before_remote_request(failure: Exception) -> None:
    guard, calls = setup_guard()
    calls.quota.reserve_fit.side_effect = failure
    with pytest.raises(type(failure)) as raised:
        guard.request(IDENTITY)
    assert raised.value is failure
    calls.request_fit.assert_not_called()


def test_fit_inspection_failure_and_mismatched_account_do_not_consume_quota() -> None:
    guard, calls = setup_guard()
    account = AccountIdentity("coros", "issuer", "different-subject")
    with pytest.raises(QuotaUnavailable, match="different accounts"):
        guard.request(ActivityIdentity(account, IDENTITY.remote_id, IDENTITY.sport))
    calls.registry.entry.assert_not_called()

    calls.files.has_fit.side_effect = StorageFailure("Could not verify FIT")
    with pytest.raises(StorageFailure, match="Could not verify FIT"):
        guard.request(IDENTITY)
    calls.quota.reserve_fit.assert_not_called()
    calls.request_fit.assert_not_called()
