from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, Mock
from zoneinfo import ZoneInfo

from fit_to_md.application.synchronization import Clock
from fit_to_md.application.synchronization_acquisition import (
    AcquisitionProgress,
    download_synchronization,
)
from fit_to_md.application.synchronization_discovery import CataloguePage
from fit_to_md.application.synchronization_quota import QuotaRegistry
from fit_to_md.application.synchronization_storage import (
    Attempts,
    FilePaths,
    FitIntegrity,
    OutputFiles,
    OutputStorage,
    Registry,
    RegistryEntry,
    RegistrySnapshot,
    Stage,
    StorageFailure,
    VaultReference,
)
from fit_to_md.domain.synchronization.model import (
    AccountIdentity,
    ActivityIdentity,
    Reference,
    RemoteActivity,
    TrackedActivity,
)
from fit_to_md.domain.synchronization.periods import (
    DiscoveryExecution,
    DiscoveryMode,
    Period,
)

ACCOUNT = AccountIdentity("coros", "issuer", "subject")
NOW = datetime(2026, 11, 21, 12, tzinfo=UTC)
ZONE = ZoneInfo("Europe/Paris")
ACTIVITY = RemoteActivity(
    ActivityIdentity(ACCOUNT, "10", 100), NOW - timedelta(hours=1)
)
EXECUTION = DiscoveryExecution(
    Period(NOW - timedelta(hours=2), NOW, ZONE),
    DiscoveryMode.FIRST_EXPLICIT,
    True,
    False,
)


def test_acquisition_orders_quota_and_publication_with_typed_ports() -> None:
    registry = Mock(spec=Registry)
    registry.snapshot.return_value = RegistrySnapshot(
        ACCOUNT,
        VaultReference("service", "entry"),
        None,
        None,
        None,
        (),
    )
    paths = FilePaths.for_start(NOW - timedelta(hours=1), ZONE)
    pending = RegistryEntry(
        TrackedActivity(ACTIVITY), NOW, None, None, None, Attempts(), Attempts()
    )
    reserved = RegistryEntry(
        TrackedActivity(ACTIVITY), NOW, paths, None, None, Attempts(), Attempts()
    )
    registry.entry.side_effect = [pending, pending, reserved, reserved]
    files = Mock(spec=OutputFiles)
    files.stage_fit.return_value = MagicMock()
    staged = files.stage_fit.return_value.__enter__.return_value
    staged.integrity = FitIntegrity(3, "a" * 64)
    quota = Mock(spec=QuotaRegistry, account=ACCOUNT)
    read = Mock(side_effect=lambda dates: CataloguePage(dates, (ACTIVITY,)))
    request = Mock(return_value=b"FIT")
    clock = Mock(spec=Clock)
    clock.now.return_value = NOW
    report = Mock()
    calls = Mock()
    calls.attach_mock(registry.save_reference, "initial_reference")
    calls.attach_mock(read, "read_catalogue")
    calls.attach_mock(quota.reserve_fit, "budget")
    calls.attach_mock(registry.save_discovery, "save_candidates")
    calls.attach_mock(registry.reserve, "reserve_path")
    calls.attach_mock(registry.record_attempt, "record_attempt")
    calls.attach_mock(request, "request")
    calls.attach_mock(registry.record_fit_integrity, "record_integrity")
    calls.attach_mock(staged.publish, "publish")
    calls.attach_mock(registry.record_acquired, "confirm")

    result = download_synchronization(
        EXECUTION, OutputStorage(registry, files), quota, read, request, clock, report
    )

    assert result.succeeded and result.selected == result.acquired == 1
    assert [call[0] for call in calls.mock_calls] == [
        "initial_reference",
        "read_catalogue",
        "save_candidates",
        "reserve_path",
        "budget",
        "record_attempt",
        "request",
        "record_integrity",
        "publish",
        "confirm",
    ]
    registry.save_reference.assert_called_once_with(
        Reference(EXECUTION.period.start), ZONE
    )
    registry.save_discovery.assert_called_once_with(
        (ACTIVITY,),
        discovered_at=NOW,
        complete=True,
        reference=Reference(NOW),
        zone=ZONE,
    )
    registry.record_attempt.assert_called_once_with(
        ACTIVITY.identity, Stage.ACQUISITION, NOW
    )
    report.assert_called_with(ACTIVITY, AcquisitionProgress.SUCCEEDED, "")


def test_registry_failure_while_saving_candidates_prevents_acquisition() -> None:
    registry = Mock(spec=Registry)
    registry.snapshot.return_value = RegistrySnapshot(
        ACCOUNT,
        VaultReference("service", "entry"),
        None,
        None,
        None,
        (),
    )
    registry.save_discovery.side_effect = StorageFailure("Registry unavailable.")
    files = Mock(spec=OutputFiles)
    quota = Mock(spec=QuotaRegistry, account=ACCOUNT)
    request = Mock()

    result = download_synchronization(
        EXECUTION,
        OutputStorage(registry, files),
        quota,
        lambda dates: CataloguePage(dates, (ACTIVITY,)),
        request,
        Mock(spec=Clock),
        Mock(),
    )

    assert result.complete and not result.succeeded
    assert isinstance(result.stopped, StorageFailure)
    registry.reserve.assert_not_called()
    request.assert_not_called()
    quota.reserve_fit.assert_not_called()
    files.stage_fit.assert_not_called()


def test_failure_to_save_initial_reference_prevents_catalogue_call() -> None:
    registry = Mock(spec=Registry)
    registry.snapshot.return_value = RegistrySnapshot(
        ACCOUNT, VaultReference("service", "entry"), None, None, None, ()
    )
    registry.save_reference.side_effect = StorageFailure("Registry unavailable.")
    read = Mock()
    quota = Mock(spec=QuotaRegistry, account=ACCOUNT)

    result = download_synchronization(
        EXECUTION,
        OutputStorage(registry, Mock(spec=OutputFiles)),
        quota,
        read,
        Mock(),
        Mock(spec=Clock),
        Mock(),
    )

    assert isinstance(result.stopped, StorageFailure)
    read.assert_not_called()
    registry.save_discovery.assert_not_called()
    quota.reserve_fit.assert_not_called()
