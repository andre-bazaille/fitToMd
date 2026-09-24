"""Conservative active-time evidence for workout analysis, independent of FIT."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from math import isfinite


class TimelineSource(StrEnum):
    UNKNOWN = "unknown"
    TIMER_EVENTS = "timer_events"
    SESSION_TOTALS = "session_totals"


class TimelineIssue(StrEnum):
    MISSING_EVIDENCE = "missing_evidence"
    INVALID_BOUNDS = "invalid_bounds"
    INVALID_EVENTS = "invalid_events"
    INCONSISTENT_TOTALS = "inconsistent_totals"


@dataclass(frozen=True)
class ActiveInterval:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        try:
            valid = self.end > self.start
        except TypeError as error:
            raise ValueError(
                "Interval timestamps must have compatible time zones"
            ) from error
        if not valid:
            raise ValueError("Active interval must have positive duration")


@dataclass(frozen=True)
class ActiveTimeline:
    intervals: tuple[ActiveInterval, ...] = ()
    source: TimelineSource = TimelineSource.UNKNOWN
    issue: TimelineIssue | None = TimelineIssue.MISSING_EVIDENCE

    def __post_init__(self) -> None:
        if not isinstance(self.source, TimelineSource):
            raise ValueError("Unknown timeline source")
        if self.source == TimelineSource.UNKNOWN:
            if self.intervals or self.issue is None:
                raise ValueError("Unknown timeline requires a reason and no intervals")
        elif self.issue is not None:
            raise ValueError("Known timeline cannot have an uncertainty reason")
        for previous, current in zip(self.intervals, self.intervals[1:], strict=False):
            try:
                valid = current.start >= previous.end
            except TypeError as error:
                raise ValueError("Intervals must have compatible time zones") from error
            if not valid:
                raise ValueError("Active intervals must be ordered and nonoverlapping")

    @property
    def is_known(self) -> bool:
        return self.source != TimelineSource.UNKNOWN


class TimerState(StrEnum):
    START = "start"
    STOP = "stop"


@dataclass(frozen=True)
class TimerTransition:
    timestamp: datetime
    state: TimerState


def _valid_total(value: float | None) -> bool:
    return (
        value is not None
        and not isinstance(value, bool)
        and isfinite(value)
        and value >= 0
    )


def build_active_timeline(
    start: datetime | None,
    end: datetime | None,
    timer_seconds: float | None,
    elapsed_seconds: float | None,
    transitions: tuple[TimerTransition, ...],
    *,
    invalid_events: bool = False,
) -> ActiveTimeline:
    """Resolve evidence without guessing where an unlocated pause occurred."""

    def unknown(issue: TimelineIssue) -> ActiveTimeline:
        return ActiveTimeline(issue=issue)

    if start is None or end is None:
        return unknown(TimelineIssue.INVALID_BOUNDS)
    try:
        wall_seconds = (end - start).total_seconds()
        if wall_seconds < 0:
            return unknown(TimelineIssue.INVALID_BOUNDS)
        events = sorted(set(transitions), key=lambda event: event.timestamp)
        if any(event.timestamp < start or event.timestamp > end for event in events):
            return unknown(TimelineIssue.INVALID_EVENTS)
    except TypeError:
        return unknown(TimelineIssue.INVALID_BOUNDS)
    if invalid_events:
        return unknown(TimelineIssue.INVALID_EVENTS)
    if not events:
        if not (_valid_total(timer_seconds) and _valid_total(elapsed_seconds)):
            return unknown(TimelineIssue.MISSING_EVIDENCE)
        assert timer_seconds is not None and elapsed_seconds is not None
        if (
            abs(timer_seconds - elapsed_seconds) > 1
            or abs(elapsed_seconds - wall_seconds) > 1
        ):
            return unknown(TimelineIssue.INCONSISTENT_TOTALS)
        intervals = (ActiveInterval(start, end),) if end > start else ()
        return ActiveTimeline(intervals, TimelineSource.SESSION_TOTALS, None)

    intervals_list: list[ActiveInterval] = []
    opened: datetime | None = None
    previous_time: datetime | None = None
    for event in events:
        if event.timestamp == previous_time or not isinstance(event.state, TimerState):
            return unknown(TimelineIssue.INVALID_EVENTS)
        previous_time = event.timestamp
        if event.state == TimerState.START:
            if opened is not None:
                return unknown(TimelineIssue.INVALID_EVENTS)
            opened = event.timestamp
        else:
            if opened is None:
                return unknown(TimelineIssue.INVALID_EVENTS)
            intervals_list.append(ActiveInterval(opened, event.timestamp))
            opened = None
    if opened is not None:
        if not _valid_total(timer_seconds):
            return unknown(TimelineIssue.MISSING_EVIDENCE)
        if end > opened:
            intervals_list.append(ActiveInterval(opened, end))
    active_seconds = sum(
        (span.end - span.start).total_seconds() for span in intervals_list
    )
    if timer_seconds is not None:
        if not _valid_total(timer_seconds) or abs(active_seconds - timer_seconds) > 1:
            return unknown(TimelineIssue.INCONSISTENT_TOTALS)
    if elapsed_seconds is not None:
        # A closed timer can stop long before the session is finally saved.
        # Elapsed time may end with the timer or include the later idle period.
        if (
            not _valid_total(elapsed_seconds)
            or elapsed_seconds < active_seconds - 1
            or elapsed_seconds > wall_seconds + 1
        ):
            return unknown(TimelineIssue.INCONSISTENT_TOTALS)
    return ActiveTimeline(tuple(intervals_list), TimelineSource.TIMER_EVENTS, None)
