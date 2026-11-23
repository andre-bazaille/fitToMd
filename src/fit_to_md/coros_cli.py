"""CLI composition and presentation for COROS commands."""

import argparse
import time
import webbrowser
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Never, TextIO

from fit_to_md.application.coros_connection import CorosSessionManager
from fit_to_md.application.synchronization import (
    PrepareSynchronization,
    to_account_identity,
)
from fit_to_md.application.synchronization_acquisition import (
    AcquisitionProgress,
    AcquisitionResult,
    download_synchronization,
)
from fit_to_md.application.synchronization_conversion import (
    ConvertSynchronizationReports,
)
from fit_to_md.application.synchronization_discovery import (
    CatalogueDates,
    CatalogueFailure,
    CatalogueFailureKind,
    CatalogueIssue,
    CataloguePage,
    CatalogueReader,
    PreviewAction,
    SynchronizationPreview,
    preview_synchronization,
)
from fit_to_md.application.synchronization_quota import AccountQuotas, QuotaUnavailable
from fit_to_md.application.synchronization_storage import (
    ConversionOptions,
    StorageFailure,
    inspect_pending_work,
)
from fit_to_md.cli import (
    CONFIGURABLE_OPTIONS,
    _arguments_with_config_defaults,
    _configure_elevation_progress,
    _write_elevation_usage_summary,
    _write_provider_diagnostics,
    add_report_arguments,
    build_report_runtime,
)
from fit_to_md.domain.coros_connection import (
    ISSUERS,
    ConnectionFailure,
    LocalSessionState,
    ReauthorizationRequired,
)
from fit_to_md.domain.synchronization.model import Reference, RemoteActivity
from fit_to_md.domain.synchronization.periods import (
    DiscoveryExecution,
    Period,
    ReferenceRepositioning,
    SelectionError,
    SelectionOptions,
)
from fit_to_md.domain.synchronization.quota import FIT_LIMIT, FIT_WINDOW
from fit_to_md.infrastructure.coros_gateway import CorosGateway, HttpsTransport
from fit_to_md.infrastructure.coros_lock import SessionFileLock
from fit_to_md.infrastructure.coros_vault import (
    VaultBackend,
    VaultConnectionStore,
    VaultQuotaBindings,
    system_backend,
)
from fit_to_md.infrastructure.local_time import SystemLocalTimezone
from fit_to_md.infrastructure.synchronization.output import SqliteOutput
from fit_to_md.infrastructure.synchronization.quota import SqliteQuotaStorage

_STATUS_LABELS = {
    LocalSessionState.DISCONNECTED: "No saved session",
    LocalSessionState.READY: "Saved session available",
    LocalSessionState.RENEWAL_NEEDED: "Session renewal needed",
    LocalSessionState.VERIFICATION_PENDING: "Account verification pending",
    LocalSessionState.RECONNECT: "Reconnection required",
}


def _build_session_manager() -> CorosSessionManager:
    return _session_manager(system_backend(extra="coros"), SessionFileLock())


def _session_manager(
    backend: VaultBackend, lock: SessionFileLock
) -> CorosSessionManager:
    return CorosSessionManager(
        _gateway,
        VaultConnectionStore(backend),
        lock,
        time.time,
    )


def _gateway(region: str) -> CorosGateway:
    return CorosGateway(
        region,
        HttpsTransport(),
        now=time.time,
        monotonic=time.monotonic,
        sleep=time.sleep,
        client_name="FitToMd",
    )


class _UtcClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def _initialize_quota() -> None:
    backend = system_backend(extra="coros")
    lock = SessionFileLock()
    account, _ = _session_manager(backend, lock).resume()
    _account_quotas(backend, lock, _UtcClock()).initialize(to_account_identity(account))


def _account_quotas(
    backend: VaultBackend, lock: SessionFileLock, clock: _UtcClock
) -> AccountQuotas:
    return AccountQuotas(
        VaultQuotaBindings(backend), SqliteQuotaStorage(clock), lock.hold
    )


def _open_browser(url: str, stderr: TextIO) -> None:
    print("Authorize FitToMd in your browser.", file=stderr, flush=True)
    try:
        opened = webbrowser.open(url)
    except (webbrowser.Error, OSError, ValueError):
        opened = False
    if not opened:
        raise ConnectionFailure("Could not open the browser for COROS login.")


def _preview(
    args: argparse.Namespace, output: SqliteOutput, stdout: TextIO, stderr: TextIO
) -> int:
    options = SelectionOptions(
        since=args.since,
        last_days=args.last_days,
        download_only=args.download_only,
        dry_run=True,
    )
    launched = PrepareSynchronization(
        _UtcClock(), SystemLocalTimezone(extra="coros")
    ).capture()
    execution = None
    if options.since is not None or options.last_days is not None:
        execution = launched.resolve(options, None)
        assert isinstance(execution, DiscoveryExecution)
        CatalogueDates.covering(execution.period)

    manager = _build_session_manager()
    account = to_account_identity(manager.saved_account())
    reader: CatalogueReader | None = None

    def read(dates: CatalogueDates) -> CataloguePage:
        nonlocal reader
        if reader is None:
            resumed, session = manager.resume()
            if to_account_identity(resumed) != account:
                raise CatalogueFailure(CatalogueFailureKind.AUTHENTICATION)
            reader = _gateway(resumed.region).prepare_catalogue(resumed, session)
        return reader(dates)

    with output.read(account) as registry:
        snapshot = registry.snapshot() if registry is not None else None
        if execution is None:
            execution = launched.resolve(
                options, snapshot.reference if snapshot else None
            )
        assert isinstance(execution, DiscoveryExecution)
        preview = preview_synchronization(execution, account, read, snapshot, output)
    _print_preview(preview, execution.period, stdout, stderr)
    return 0 if preview.complete else 1


def _print_preview(
    preview: SynchronizationPreview, period: Period, stdout: TextIO, stderr: TextIO
) -> None:
    _print_period(period, stdout)
    coverage = "complete" if preview.complete else "incomplete (provisional plan)"
    print(f"Catalogue coverage: {coverage}", file=stdout)
    counts = Counter(entry.action for entry in preview.entries)
    for action in PreviewAction:
        print(f"{action}: {counts[action]}", file=stdout)
    print(
        f"Conflicts: {sum(bool(entry.conflicts) for entry in preview.entries)}",
        file=stdout,
    )
    print(f"Estimated FIT requests: {preview.estimated_acquisitions}", file=stdout)
    for entry in preview.entries:
        instant = entry.activity.start_at
        when = (
            instant.astimezone(period.zone).isoformat() if instant else "unknown start"
        )
        paths = ", ".join(path.as_posix() for path in entry.conflicts)
        actions = ", ".join(
            action
            for selected, action in (
                (entry.acquire, "acquire"),
                (entry.convert, "convert"),
            )
            if selected
        )
        detail = f"conflict at {paths}" if paths else actions
        line = f"{when} (sport {entry.activity.identity.sport}) — {entry.action}"
        print(f"{line} ({detail})" if detail else line, file=stdout)
    _print_catalogue_diagnostics(preview.unclassifiable, preview.issues, stderr)


def _print_period(period: Period, stdout: TextIO) -> None:
    start = period.start.astimezone(period.zone).isoformat()
    end = period.end.astimezone(period.zone).isoformat()
    print(f"Period [{start}, {end}) ({period.zone.key})", file=stdout)


def _print_catalogue_diagnostics(
    unclassifiable: int, issues: tuple[CatalogueIssue, ...], stderr: TextIO
) -> None:
    if unclassifiable:
        print(
            f"Activities with missing, invalid or contradictory timestamps: "
            f"{unclassifiable}",
            file=stderr,
        )
    for issue in issues:
        failure = issue.failure
        status = f" (HTTP {failure.status})" if failure.status is not None else ""
        retry = (
            f" Retry after {failure.retry_at.isoformat()}." if failure.retry_at else ""
        )
        print(
            f"Catalogue {issue.dates.start} to {issue.dates.end}: "
            f"{failure}{status}{retry}",
            file=stderr,
        )


def _synchronize(
    args: argparse.Namespace,
    output: SqliteOutput,
    conversion_options: ConversionOptions,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    clock = _UtcClock()
    options = SelectionOptions(
        since=args.since,
        last_days=args.last_days,
        init_coros_reference=args.init_coros_reference,
        download_only=args.download_only,
    )
    launched = PrepareSynchronization(
        clock, SystemLocalTimezone(extra="coros")
    ).capture()
    resolve_without_reference = (
        options.since is not None
        or options.last_days is not None
        or options.init_coros_reference is not None
    )

    def resolve(
        reference: Reference | None,
    ) -> DiscoveryExecution | ReferenceRepositioning:
        execution = launched.resolve(options, reference)
        if isinstance(execution, DiscoveryExecution):
            CatalogueDates.covering(execution.period)
        return execution

    if resolve_without_reference:
        resolve(None)

    backend = system_backend(extra="coros")
    lock = SessionFileLock()
    manager = _session_manager(backend, lock)
    account = to_account_identity(manager.saved_account())
    checked, reference = output.inspect_for_write(account)
    if checked and not resolve_without_reference:
        resolve(reference)
    quota = (
        _account_quotas(backend, lock, clock).open(account)
        if options.init_coros_reference is None
        else None
    )
    resumed, session = manager.resume()
    if to_account_identity(resumed) != account:
        raise ReauthorizationRequired(CatalogueFailureKind.AUTHENTICATION.value)

    def subject(activity: RemoteActivity) -> str:
        when = (
            activity.start_at.astimezone(launched.zone).isoformat()
            if activity.start_at is not None
            else "unknown start"
        )
        return f"FIT for {when} (sport {activity.identity.sport})"

    def progress(
        activity: RemoteActivity, event: AcquisitionProgress, detail: str
    ) -> None:
        if event is AcquisitionProgress.STARTED:
            print(f"Downloading {subject(activity)}…", file=stderr, flush=True)
        else:
            suffix = f" ({detail})" if detail else ""
            print(f"{subject(activity)}: {event.value}{suffix}", file=stderr)

    runtime = None
    conversion = None
    with output.write(account, VaultConnectionStore.reference) as storage:
        execution = resolve(storage.registry.reference())
        if isinstance(execution, ReferenceRepositioning):
            storage.registry.save_reference(execution.new_reference, execution.zone)
        else:
            assert quota is not None
            if not options.download_only:
                runtime = build_report_runtime(args)
                _configure_elevation_progress(runtime.elevation_diagnostics, stderr)
                conversion = ConvertSynchronizationReports(
                    storage,
                    runtime.generator,
                    conversion_options,
                    clock,
                    lambda activity, message: print(
                        f"{subject(activity)}: {message}", file=stderr, flush=True
                    ),
                    lambda report, source: _write_provider_diagnostics(
                        report.diagnostics, stderr, source=source
                    ),
                )
            read, request = _gateway(resumed.region).prepare_acquisition(
                resumed, session, quota.record_catalogue_call
            )
            result = download_synchronization(
                execution,
                storage,
                quota,
                read,
                request,
                clock,
                progress,
                on_fit_available=conversion.convert if conversion else None,
                needs_local_work=conversion.needs_repair if conversion else None,
            )
    if isinstance(execution, ReferenceRepositioning):
        previous = execution.previous_reference
        print(
            f"Previous reference: {previous.instant.isoformat() if previous else 'unset'}",
            file=stdout,
        )
        print(
            f"New reference: {execution.new_reference.instant.isoformat()} ({execution.zone.key})",
            file=stdout,
        )
        return 0
    _print_acquisition(result, execution.period, stdout, stderr, conversion)
    if runtime is not None:
        _write_elevation_usage_summary(runtime.elevation_diagnostics, stderr)
    assert quota is not None
    zone = execution.period.zone
    day = clock.now().astimezone(zone).date()
    try:
        calls = quota.daily_calls(day, zone)
    except QuotaUnavailable as error:
        print(f"Could not read daily COROS quota counters. {error}", file=stderr)
        return 130 if result.interrupted else 1
    print(f"Local quota day: {day} ({zone.key})", file=stdout)
    print(f"Catalogue calls: {calls.catalogue}", file=stdout)
    print(f"FIT requests: {calls.fit}", file=stdout)
    hours = FIT_WINDOW // timedelta(hours=1)
    print(
        f"Local FIT limit: {FIT_LIMIT} requests per rolling {hours} hours", file=stdout
    )
    if result.interrupted:
        return 130
    return (
        0 if result.succeeded and (conversion is None or not conversion.failed) else 1
    )


def _print_acquisition(
    result: AcquisitionResult,
    period: Period,
    stdout: TextIO,
    stderr: TextIO,
    conversion: ConvertSynchronizationReports | None,
) -> None:
    _print_period(period, stdout)
    coverage = "complete" if result.complete else "incomplete"
    print(f"Catalogue coverage: {coverage}", file=stdout)
    print(f"Selected: {result.selected}", file=stdout)
    print(f"FITs already available: {result.reused}", file=stdout)
    print(f"FITs downloaded: {result.acquired}", file=stdout)
    print(f"Acquisition errors: {result.failed}", file=stdout)
    if conversion is not None:
        print(f"Reports already available: {conversion.reused}", file=stdout)
        print(f"Reports created: {conversion.created}", file=stdout)
        print(f"Conversion errors: {conversion.failed}", file=stdout)
    print(f"Deferred for quota: {result.deferred_for_quota}", file=stdout)
    _print_catalogue_diagnostics(result.unclassifiable, result.catalogue_issues, stderr)
    if result.quota_stop is not None:
        stop = result.quota_stop
        retry = f" Retry after {stop.retry_at.isoformat()}." if stop.retry_at else ""
        print(f"{stop}{retry}", file=stderr)
    if result.stopped is not None:
        print(result.stopped, file=stderr)
    if result.interrupted:
        print("COROS command interrupted.", file=stderr)


def _conversion_options(args: argparse.Namespace) -> ConversionOptions:
    return ConversionOptions(
        workout_report=args.workout_report,
        hr_zone_boundaries=(
            args.hr_zone_boundaries.thresholds_bpm
            if args.hr_zone_boundaries is not None
            else None
        ),
        weather_mode=args.weather_mode,
        elevation_source=args.elevation_source,
        elevation_smoothing_distance=args.elevation_smoothing_distance,
        elevation_min_change=args.elevation_min_change,
        dem_sample_distance=args.dem_sample_distance,
        dynamics_step_size=args.dynamics_step_size,
        opentopodata_dataset=args.opentopodata_dataset,
        opentopodata_base_url=args.opentopodata_base_url,
    )


def _status(
    manager: CorosSessionManager, output: SqliteOutput | None, stdout: TextIO
) -> None:
    status, saved_account = manager.inspect_local()
    region = f" (region: {status.region})" if status.region is not None else ""
    print(f"COROS local status: {_STATUS_LABELS[status.state]}{region}.", file=stdout)
    if output is None:
        return

    print(f"Reports: {output.directory}", file=stdout)
    print(f"FITs: {output.directory / 'fits'}", file=stdout)
    print(f"Registry: {output.registry_path}", file=stdout)
    if saved_account is None:
        print(
            "Synchronization tracking unavailable without a saved account.", file=stdout
        )
        return

    with output.read(to_account_identity(saved_account)) as registry:
        snapshot = registry.snapshot() if registry is not None else None
        reference = snapshot.reference if snapshot is not None else None
        print(
            f"Reference (UTC): {reference.instant.isoformat() if reference else 'unset'}",
            file=stdout,
        )
        print(
            f"Reference timezone: {(snapshot.reference_zone if snapshot else None) or 'unset'}",
            file=stdout,
        )
        pending = inspect_pending_work(
            snapshot.entries if snapshot is not None else (), output
        )
        print(f"Activities with pending work: {pending.activities}", file=stdout)
        print(f"FITs pending: {pending.fits}", file=stdout)
        print(f"Reports pending: {pending.reports}", file=stdout)
        print(f"Unsupported conversions: {pending.unsupported}", file=stdout)


def run_connection_command(argv: Sequence[str], stdout: TextIO, stderr: TextIO) -> int:
    class CorosArgumentParser(argparse.ArgumentParser):
        def error(self, message: str) -> Never:
            self.print_usage(stderr)
            print(f"{self.prog}: error: {message}", file=stderr)
            raise SystemExit(2)

    parser = CorosArgumentParser(
        prog="fit-to-md coros",
        description="Connect to COROS and synchronize FIT files.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    login = commands.add_parser("login", help="Authorize COROS in your browser")
    login.add_argument(
        "--region",
        choices=ISSUERS,
        help="COROS account region (defaults to the saved region or eu)",
    )
    status = commands.add_parser(
        "status", help="Show local session and optional output tracking"
    )
    commands.add_parser("logout", help="Remove the local COROS session")
    commands.add_parser(
        "init-quota", help="Initialize the FIT quota for a first-time account"
    )
    sync = commands.add_parser(
        "sync", help="Synchronize FIT files and Markdown reports"
    )
    for command in (sync, status):
        command.add_argument(
            "--config", type=Path, help="Read defaults from a configuration file"
        )
        command.add_argument(
            "-o",
            "--output",
            type=Path,
            metavar="DIR",
            help="Use an existing output directory",
        )
    add_report_arguments(sync)
    sync.add_argument("--dry-run", action="store_true")
    period = sync.add_mutually_exclusive_group()
    period.add_argument("--since", metavar="YYYY-MM-DD")
    period.add_argument("--last-days", type=int, metavar="N")
    period.add_argument(
        "--init-coros-reference",
        metavar="YYYY-MM-DD",
        help="Set the synchronization reference without acquisition",
    )
    sync.add_argument("--download-only", action="store_true")
    if argv and argv[0] in ("sync", "status"):
        subparser = sync if argv[0] == "sync" else status
        config_options = (
            ("output",)
            if subparser is status
            else tuple(
                option
                for option in CONFIGURABLE_OPTIONS
                if option != "output-by-activity-time"
            )
        )
        argv = [
            argv[0],
            *_arguments_with_config_defaults(
                subparser, argv[1:], use_options=config_options
            ),
        ]
    args = parser.parse_args(argv)
    if args.command == "sync" and args.output is None:
        sync.error("An existing --output directory is required (CLI or configuration).")
    try:
        output = None
        if args.command in ("sync", "status") and args.output is not None:
            try:
                output = SqliteOutput(args.output)
            except StorageFailure as error:
                print(error, file=stderr)
                return 2
        if args.command == "sync":
            assert output is not None
            if args.init_coros_reference is not None and (
                args.download_only or args.dry_run
            ):
                print(
                    "--init-coros-reference cannot be combined with --download-only or --dry-run.",
                    file=stderr,
                )
                return 2
            try:
                conversion_options = _conversion_options(args)
            except ValueError as error:
                print(error, file=stderr)
                return 2
            if args.dry_run:
                return _preview(args, output, stdout, stderr)
            return _synchronize(args, output, conversion_options, stdout, stderr)
        if args.command == "init-quota":
            _initialize_quota()
            print("COROS quota registry is ready.", file=stdout)
            return 0

        manager = _build_session_manager()
        match args.command:
            case "login":
                account = manager.login(
                    lambda url: _open_browser(url, stderr), region=args.region
                )
                print(
                    f"COROS account confirmed (region: {account.region}).", file=stdout
                )
            case "status":
                _status(manager, output, stdout)
            case "logout":
                manager.logout()
                print("COROS local session removed.", file=stdout)
    except SelectionError as error:
        print(error, file=stderr)
        return 2
    except (ConnectionFailure, QuotaUnavailable, StorageFailure) as error:
        print(error, file=stderr)
        return 1
    except KeyboardInterrupt:
        print("COROS command interrupted.", file=stderr)
        return 130
    return 0
