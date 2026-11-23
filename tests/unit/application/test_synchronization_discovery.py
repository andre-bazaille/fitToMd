from datetime import UTC, date, datetime, timedelta
from pathlib import PurePosixPath
from zoneinfo import ZoneInfo

import pytest

from fit_to_md.application.synchronization_discovery import (
    CATALOGUE_LIMIT,
    CatalogueDates,
    CatalogueFailure,
    CatalogueFailureKind,
    CataloguePage,
    PreviewAction,
    discover_catalogue,
    preview_synchronization,
)
from fit_to_md.application.synchronization_storage import (
    Attempts,
    FilePaths,
    OutputConflict,
    RegistryEntry,
    RegistrySnapshot,
    StorageFailure,
    VaultReference,
)
from fit_to_md.domain.synchronization.model import (
    AccountIdentity,
    AcquisitionState,
    ActivityIdentity,
    ConversionState,
    Reference,
    RemoteActivity,
    TrackedActivity,
)
from fit_to_md.domain.synchronization.periods import (
    DiscoveryExecution,
    Period,
    SelectionError,
    SelectionOptions,
    resolve_selection,
)

ACCOUNT = AccountIdentity("coros", "regional-issuer", "subject")
START = datetime(2026, 11, 20, 23, tzinfo=UTC)
LAUNCH = datetime(2026, 11, 21, 12, tzinfo=UTC)
ZONE = ZoneInfo("Europe/Paris")
DAY = timedelta(days=1)


def activity(identity: str, at: datetime | None = START) -> RemoteActivity:
    return RemoteActivity(ActivityIdentity(ACCOUNT, identity, 100), at)


def dates(first: int, last: int) -> CatalogueDates:
    return CatalogueDates(date(2026, 11, first), date(2026, 11, last))


def rows(count: int) -> tuple[RemoteActivity, ...]:
    return tuple(activity(f"parent-{index}") for index in range(count))


def test_saturated_parents_are_replaced_by_disjoint_children_and_deduplicated() -> None:
    first, next_activity = activity("1"), activity("2")
    pages = {
        dates(15, 18): rows(CATALOGUE_LIMIT),
        dates(15, 16): (first,),
        dates(17, 18): rows(CATALOGUE_LIMIT),
        dates(17, 17): (first, next_activity),
        dates(18, 18): (),
    }
    calls: list[CatalogueDates] = []

    def read(requested: CatalogueDates) -> CataloguePage:
        calls.append(requested)
        return CataloguePage(requested, pages[requested])

    result = discover_catalogue(dates(15, 18), read)
    assert calls == list(pages)
    assert result.catalog.complete and not result.issues
    assert result.catalog.activities == (first, next_activity)


@pytest.mark.parametrize(
    ("page_failure", "expected_kind"),
    [
        (CataloguePage(None, ()), CatalogueFailureKind.BOUNDS),
        (CataloguePage(dates(15, 16), ()), CatalogueFailureKind.BOUNDS),
        (
            CataloguePage(dates(15, 15), rows(CATALOGUE_LIMIT + 1)),
            CatalogueFailureKind.RESPONSE,
        ),
        (
            CataloguePage(dates(15, 15), rows(CATALOGUE_LIMIT)),
            CatalogueFailureKind.SATURATION,
        ),
        (CatalogueFailure(CatalogueFailureKind.NETWORK), CatalogueFailureKind.NETWORK),
    ],
)
def test_leaf_failure_flags_partial_coverage_and_keeps_other_leaves(
    page_failure: CataloguePage | CatalogueFailure, expected_kind: CatalogueFailureKind
) -> None:
    root = dates(15, 16)
    good = activity("good")

    def read(requested: CatalogueDates) -> CataloguePage:
        if requested == root:
            return CataloguePage(requested, rows(CATALOGUE_LIMIT))
        if requested == dates(15, 15):
            if isinstance(page_failure, CatalogueFailure):
                raise page_failure
            return page_failure
        assert requested == dates(16, 16)
        return CataloguePage(requested, (good,))

    result = discover_catalogue(root, read)
    assert not result.catalog.complete
    assert good in result.catalog.activities
    assert len(result.catalog.activities) == (
        CATALOGUE_LIMIT + 1 if expected_kind is CatalogueFailureKind.SATURATION else 1
    )
    (issue,) = result.issues
    assert issue.dates == dates(15, 15) and issue.failure.kind is expected_kind


def test_authentication_error_stops_pending_subranges() -> None:
    root = dates(15, 16)
    calls: list[CatalogueDates] = []

    def read(requested: CatalogueDates) -> CataloguePage:
        calls.append(requested)
        if requested == root:
            return CataloguePage(requested, rows(CATALOGUE_LIMIT))
        raise CatalogueFailure(CatalogueFailureKind.AUTHENTICATION)

    result = discover_catalogue(root, read)
    assert calls == [root, dates(15, 15)]
    assert not result.catalog.complete and not result.catalog.activities


class FreeDestinations:
    def require_available(self, path: PurePosixPath) -> None:
        assert path.suffix in (".fit", ".md")

    def has_report(self, path: PurePosixPath) -> bool:
        return path.suffix == ".md"


class OccupiedDestinations:
    def __init__(self, *paths: PurePosixPath) -> None:
        self._paths = set(paths)

    def require_available(self, path: PurePosixPath) -> None:
        if path in self._paths:
            raise OutputConflict("Existing file.")


def selection(
    options: SelectionOptions, reference: Reference | None = None
) -> DiscoveryExecution:
    execution = resolve_selection(options, reference, launched_at=LAUNCH, zone=ZONE)
    assert isinstance(execution, DiscoveryExecution)
    return execution


def entry(
    source: RemoteActivity, *, known: bool = False, reserved: bool = False
) -> RegistryEntry:
    tracked = (
        TrackedActivity(source, AcquisitionState.ACQUIRED, ConversionState.CONVERTED)
        if known
        else TrackedActivity(source)
    )
    paths = None
    if reserved:
        assert source.start_at is not None
        paths = FilePaths.for_start(source.start_at, ZONE)
    return RegistryEntry(
        tracked, LAUNCH - DAY, paths, None, None, Attempts(), Attempts()
    )


def snapshot(*entries: RegistryEntry) -> RegistrySnapshot:
    return RegistrySnapshot(
        ACCOUNT, VaultReference("vault", "session"), None, None, None, entries
    )


@pytest.mark.parametrize(
    ("zone", "launch", "last_days", "start", "end"),
    [
        (
            "Europe/Paris",
            datetime(2026, 3, 29, 12, tzinfo=UTC),
            1,
            date(2026, 3, 27),
            date(2026, 3, 30),
        ),
        (
            "America/New_York",
            datetime(2027, 1, 1, 6, tzinfo=UTC),
            2,
            date(2026, 12, 30),
            date(2027, 1, 2),
        ),
        (
            "Pacific/Kiritimati",
            datetime(2026, 12, 31, 11, tzinfo=UTC),
            1,
            date(2026, 12, 30),
            date(2027, 1, 1),
        ),
    ],
)
def test_candidate_dates_follow_utc_envelope_over_calendar_transitions(
    zone: str, launch: datetime, last_days: int, start: date, end: date
) -> None:
    execution = resolve_selection(
        SelectionOptions(last_days=last_days),
        None,
        launched_at=launch,
        zone=ZoneInfo(zone),
    )
    assert isinstance(execution, DiscoveryExecution)
    assert CatalogueDates.covering(execution.period) == CatalogueDates(start, end)


def test_candidate_dates_reject_overflow_and_reversed_dates() -> None:
    instant = datetime.min.replace(tzinfo=UTC)
    with pytest.raises(SelectionError, match="cannot be covered"):
        CatalogueDates.covering(Period(instant, instant, ZONE))
    with pytest.raises(ValueError, match="chronological"):
        dates(16, 15)


def test_filters_exact_bounds_and_sorts_known_new_resume_by_identity() -> None:
    start, end = START, LAUNCH
    older = activity("older", start - DAY)
    known = activity("known", start)
    new = activity("new", start)
    resume = activity("resume", start + timedelta(minutes=1))
    before = activity("before", start - timedelta(seconds=1))
    after = activity("launch", end)

    def read(requested: CatalogueDates) -> CataloguePage:
        assert requested == dates(19, 22)
        return CataloguePage(requested, (after, new, before, resume, known))

    preview = preview_synchronization(
        selection(SelectionOptions(since="2026-11-21", dry_run=True)),
        ACCOUNT,
        read,
        snapshot(entry(older), entry(resume), entry(known, known=True, reserved=True)),
        FreeDestinations(),
    )
    assert preview.complete
    assert [(row.activity, row.action) for row in preview.entries] == [
        (known, PreviewAction.KNOWN),
        (new, PreviewAction.NEW),
        (resume, PreviewAction.RESUME),
    ]
    assert preview.estimated_acquisitions == 1


def test_invalid_and_conflicting_starts_are_excluded_even_if_tracked() -> None:
    invalid, good, conflict = (
        activity("invalid", None),
        activity("good"),
        activity("conflict"),
    )
    duplicate = activity("conflict", START + timedelta(seconds=1))
    preview = preview_synchronization(
        selection(SelectionOptions(last_days=1, dry_run=True)),
        ACCOUNT,
        lambda request: CataloguePage(
            request, (invalid, conflict, duplicate, conflict, good)
        ),
        snapshot(entry(conflict), entry(activity("invalid"))),
        FreeDestinations(),
    )
    assert not preview.complete
    assert preview.unclassifiable == 2
    assert [row.activity for row in preview.entries] == [good]


@pytest.mark.parametrize("occupied_report", [False, True])
def test_preview_checks_candidate_reserved_and_unowned_destinations(
    occupied_report: bool,
) -> None:
    first, second = activity("a"), activity("b", START + timedelta(seconds=30))
    owner = activity("owner", START + timedelta(minutes=1))
    competing = activity("competing", owner.start_at)
    manual = activity("manual", START + timedelta(minutes=2))
    manual_path = FilePaths.for_start(START + timedelta(minutes=2), ZONE).fit
    owner_entry = entry(owner, reserved=True)
    assert owner_entry.paths is not None
    first_paths = FilePaths.for_start(START, ZONE)
    occupied = [manual_path, owner_entry.paths.fit]
    if occupied_report:
        occupied.append(first_paths.report)
    preview = preview_synchronization(
        selection(SelectionOptions(last_days=1, dry_run=True)),
        ACCOUNT,
        lambda request: CataloguePage(request, (manual, competing, second, first)),
        snapshot(owner_entry),
        OccupiedDestinations(*occupied),
    )
    assert preview.complete and preview.estimated_acquisitions == 1
    conflicts = {row.activity: row.conflicts for row in preview.entries}
    assert conflicts == {
        first: (first_paths.report,) if occupied_report else (),
        second: (first_paths.fit,),
        competing: (owner_entry.paths.fit,),
        owner: (owner_entry.paths.fit,),
        manual: (manual_path,),
    }


def test_download_only_ignores_unowned_report_and_reuses_existing_conversion_state() -> (
    None
):
    new = activity("new")
    acquired = activity("acquired", START + timedelta(minutes=1))
    acquired_entry = entry(acquired, reserved=True)
    acquired_entry = RegistryEntry(
        TrackedActivity(acquired, AcquisitionState.ACQUIRED),
        acquired_entry.discovered_at,
        acquired_entry.paths,
        None,
        None,
        Attempts(),
        Attempts(),
    )
    preview = preview_synchronization(
        selection(SelectionOptions(last_days=1, dry_run=True, download_only=True)),
        ACCOUNT,
        lambda request: CataloguePage(request, (new, acquired)),
        snapshot(acquired_entry),
        OccupiedDestinations(FilePaths.for_start(START, ZONE).report),
    )
    assert [row.action for row in preview.entries] == [
        PreviewAction.NEW,
        PreviewAction.KNOWN,
    ]
    assert not any(row.convert or row.conflicts for row in preview.entries)
    assert preview.estimated_acquisitions == 1


def test_non_conflict_storage_error_propagates() -> None:
    class UnreadableOutput:
        def require_available(self, path: PurePosixPath) -> None:
            raise StorageFailure("Unreadable output.")

    with pytest.raises(StorageFailure, match="Unreadable output"):
        preview_synchronization(
            selection(SelectionOptions(last_days=1, dry_run=True)),
            ACCOUNT,
            lambda request: CataloguePage(request, (activity("new"),)),
            None,
            UnreadableOutput(),
        )
