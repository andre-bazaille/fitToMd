import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from .model import InvalidInstant, Reference, as_utc


class SelectionErrorCode(StrEnum):
    CONFLICTING_OPTIONS = "conflicting_options"
    INVALID_DATE = "invalid_date"
    INVALID_DAY_COUNT = "invalid_day_count"
    FUTURE_DATE = "future_date"
    UNREPRESENTABLE_BOUND = "unrepresentable_bound"
    MISSING_REFERENCE = "missing_reference"
    FUTURE_REFERENCE = "future_reference"


class SelectionError(ValueError):
    def __init__(self, code: SelectionErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SelectionOptions:
    since: str | None = None
    last_days: int | None = None
    init_coros_reference: str | None = None
    download_only: bool = False
    dry_run: bool = False


@dataclass(frozen=True)
class Period:
    start: datetime
    end: datetime
    zone: ZoneInfo

    def __post_init__(self) -> None:
        object.__setattr__(self, "start", as_utc(self.start))
        object.__setattr__(self, "end", as_utc(self.end))
        if self.start > self.end:
            raise SelectionError(
                SelectionErrorCode.UNREPRESENTABLE_BOUND,
                "The period starts after the launch time.",
            )

    def contains(self, instant: datetime | None) -> bool:
        return instant is not None and self.start <= as_utc(instant) < self.end


class DiscoveryMode(StrEnum):
    FIRST_EXPLICIT = "first_explicit"
    EXPLICIT = "explicit"
    INCREMENTAL = "incremental"


@dataclass(frozen=True)
class DiscoveryExecution:
    period: Period
    mode: DiscoveryMode
    download_only: bool
    dry_run: bool

    def includes_tracked(self, start: datetime | None) -> bool:
        """Whether the current mode can resume an already tracked activity."""
        return self.mode is DiscoveryMode.INCREMENTAL or self.period.contains(start)

    @property
    def initial_reference(self) -> Reference | None:
        """Reference to persist after validation and authentication, before discovery."""
        if self.mode is DiscoveryMode.FIRST_EXPLICIT and not self.dry_run:
            return Reference(self.period.start)
        return None

    def reference_after_discovery(self, *, complete: bool) -> Reference | None:
        """Reference to commit atomically with the candidates, if applicable."""
        if complete and not self.dry_run and self.mode is not DiscoveryMode.EXPLICIT:
            return Reference(self.period.end)
        return None


@dataclass(frozen=True)
class ReferenceRepositioning:
    previous_reference: Reference | None
    new_reference: Reference
    zone: ZoneInfo


def resolve_selection(
    options: SelectionOptions,
    reference: Reference | None,
    *,
    launched_at: datetime,
    zone: ZoneInfo,
) -> DiscoveryExecution | ReferenceRepositioning:
    """Resolve the requested operation using a single launch time and local zone."""
    explicit = options.since is not None or options.last_days is not None
    if options.since is not None and options.last_days is not None:
        raise SelectionError(
            SelectionErrorCode.CONFLICTING_OPTIONS,
            "Choose either --since or --last-days.",
        )
    if options.init_coros_reference is not None and (
        explicit or options.download_only or options.dry_run
    ):
        raise SelectionError(
            SelectionErrorCode.CONFLICTING_OPTIONS,
            "--init-coros-reference cannot be combined with a period, "
            "--download-only or --dry-run.",
        )

    launch = as_utc(launched_at)
    today = _local_day(launch, zone)
    if options.init_coros_reference is not None:
        day = _parse_day(options.init_coros_reference, today)
        return ReferenceRepositioning(reference, Reference(_midnight(day, zone)), zone)

    if options.since is not None:
        day = _parse_day(options.since, today)
    elif options.last_days is not None:
        day = _relative_day(today, options.last_days)
    else:
        if reference is None:
            raise SelectionError(
                SelectionErrorCode.MISSING_REFERENCE,
                "An initial period is required. Try --last-days 7 --output DIR.",
            )
        if reference.instant > launch:
            raise SelectionError(
                SelectionErrorCode.FUTURE_REFERENCE,
                "The synchronization reference is after the launch time.",
            )
        day = _local_day(reference.instant, zone)

    mode = DiscoveryMode.INCREMENTAL
    if explicit:
        mode = (
            DiscoveryMode.FIRST_EXPLICIT
            if reference is None
            else DiscoveryMode.EXPLICIT
        )
    return DiscoveryExecution(
        Period(_midnight(day, zone), launch, zone),
        mode,
        options.download_only,
        options.dry_run,
    )


def _parse_day(value: str, today: date) -> date:
    try:
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
            raise ValueError
        day = date.fromisoformat(value)
    except ValueError as exc:
        raise SelectionError(
            SelectionErrorCode.INVALID_DATE,
            "Expected a valid date in YYYY-MM-DD format.",
        ) from exc
    if day > today:
        raise SelectionError(
            SelectionErrorCode.FUTURE_DATE, "The date is in the future."
        )
    return day


def _relative_day(today: date, count: int) -> date:
    if type(count) is not int or count <= 0:
        raise SelectionError(
            SelectionErrorCode.INVALID_DAY_COUNT,
            "--last-days requires a positive integer.",
        )
    try:
        return today - timedelta(days=count - 1)
    except OverflowError as exc:
        raise SelectionError(
            SelectionErrorCode.UNREPRESENTABLE_BOUND,
            "The period is outside the supported calendar.",
        ) from exc


def _local_day(instant: datetime, zone: ZoneInfo) -> date:
    try:
        return instant.astimezone(zone).date()
    except OverflowError as exc:
        raise SelectionError(
            SelectionErrorCode.UNREPRESENTABLE_BOUND,
            "The instant is outside the local calendar.",
        ) from exc


def _midnight(day: date, zone: ZoneInfo) -> datetime:
    local = datetime.combine(day, time.min, tzinfo=zone)
    try:
        utc = as_utc(local)
        if utc.astimezone(zone) == local:
            return utc
    except (InvalidInstant, OverflowError) as exc:
        raise SelectionError(
            SelectionErrorCode.UNREPRESENTABLE_BOUND,
            "Local midnight is outside the UTC calendar.",
        ) from exc
    raise SelectionError(
        SelectionErrorCode.UNREPRESENTABLE_BOUND,
        "Local midnight does not exist on this date.",
    )
