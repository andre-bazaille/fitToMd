"""Pure active-time and sampled-measurement rules for workout analysis."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite

from fit_to_md.domain.activity.entities import ActivityRecord
from fit_to_md.domain.activity.timeline import ActiveInterval, ActiveTimeline

MAX_SAMPLE_SPAN_S = 5.0


@dataclass(frozen=True)
class ValueSpan:
    start: datetime
    end: datetime
    value: float

    @property
    def seconds(self) -> float:
        return (self.end - self.start).total_seconds()


def intersect_interval(
    first: ActiveInterval, second: ActiveInterval
) -> ActiveInterval | None:
    """Intersect two half-open intervals; touching boundaries have no duration."""
    start = max(first.start, second.start)
    end = min(first.end, second.end)
    return ActiveInterval(start, end) if end > start else None


def active_duration(
    timeline: ActiveTimeline, start: datetime, end: datetime
) -> float | None:
    """Return active seconds in [start, end), or unknown timing."""
    if not timeline.is_known:
        return None
    if end < start:
        raise ValueError("End precedes start")
    bounds = ActiveInterval(start, end) if end > start else None
    if bounds is None:
        return 0.0
    return sum(
        (part.end - part.start).total_seconds()
        for interval in timeline.intervals
        if (part := intersect_interval(interval, bounds)) is not None
    )


def contains_lap_time(timestamp: datetime, start: datetime, end: datetime) -> bool:
    """A shared boundary observation belongs to the following lap."""
    return start <= timestamp < end


def wall_time_at_active_offset(
    timeline: ActiveTimeline, start: datetime, end: datetime, offset_s: float
) -> datetime | None:
    """Locate an active offset; exact pause onset resolves to its stop time."""
    if not timeline.is_known or not isfinite(offset_s) or offset_s < 0 or end < start:
        return None
    if offset_s == 0:
        return (
            start if any(i.start <= start < i.end for i in timeline.intervals) else None
        )
    remaining = offset_s
    for interval in timeline.intervals:
        if interval.end <= start or interval.start >= end:
            continue
        left, right = max(start, interval.start), min(end, interval.end)
        seconds = (right - left).total_seconds()
        if seconds <= 0:
            continue
        if remaining <= seconds:
            return left + timedelta(seconds=remaining)
        remaining -= seconds
    return None


def chronological_records(
    records: Iterable[ActivityRecord],
) -> tuple[ActivityRecord, ...]:
    """Sort a separate analysis view; last source record wins timestamp ties."""
    by_time = {record.timestamp: record for record in records}
    return tuple(by_time[key] for key in sorted(by_time))


def valid_hr(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and isfinite(value)
        and value > 0
    )


def valid_cadence(value: object) -> bool:
    return valid_hr(value)


def sampled_spans(
    records: tuple[ActivityRecord, ...],
    timeline: ActiveTimeline,
    value_of: Callable[[ActivityRecord], int | float | None],
    valid: Callable[[object], bool],
    bounds: ActiveInterval | None = None,
) -> tuple[ValueSpan, ...]:
    """Earlier sample covers at most five wall seconds, clipped to active time."""
    if not timeline.is_known:
        return ()
    spans: list[ValueSpan] = []
    ordered = chronological_records(records)
    for earlier, later in zip(ordered, ordered[1:], strict=False):
        value = value_of(earlier)
        if not valid(value) or later.timestamp <= earlier.timestamp:
            continue
        assert value is not None
        capped = ActiveInterval(
            earlier.timestamp,
            min(
                later.timestamp,
                earlier.timestamp + timedelta(seconds=MAX_SAMPLE_SPAN_S),
            ),
        )
        if bounds is not None:
            clipped = intersect_interval(capped, bounds)
            if clipped is None:
                continue
            capped = clipped
        for active in timeline.intervals:
            part = intersect_interval(capped, active)
            if part is not None:
                spans.append(ValueSpan(part.start, part.end, float(value)))
    return tuple(spans)


def hr_spans(
    records: tuple[ActivityRecord, ...],
    timeline: ActiveTimeline,
    bounds: ActiveInterval | None = None,
) -> tuple[ValueSpan, ...]:
    return sampled_spans(
        records, timeline, lambda record: record.heart_rate_bpm, valid_hr, bounds
    )


def weighted_average(spans: Iterable[ValueSpan]) -> float | None:
    values = tuple(spans)
    covered = sum(span.seconds for span in values)
    return (
        sum(span.value * span.seconds for span in values) / covered
        if covered > 0
        else None
    )
