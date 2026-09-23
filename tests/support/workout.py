"""Synthetic decoded messages, not evidence of binary FIT association support."""

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal, Self

import fitdecode

START = datetime(2020, 1, 1, 12, tzinfo=UTC)
Variant = Literal[
    "structured",
    "unlabeled",
    "lap_only",
    "sparse_hr",
    "paused",
    "unknown_pause",
    "overlapping",
    "malformed_metadata",
]


@dataclass(frozen=True)
class FakeField:
    name: str
    value: object


@dataclass
class FakeFrame:
    name: str
    values: dict[str, object]
    frame_type: int = field(default=fitdecode.FIT_FRAME_DATA, init=False)

    @property
    def fields(self) -> list[FakeField]:
        return [FakeField(name, value) for name, value in self.values.items()]


class FakeReader:
    def __init__(self, frames: tuple[FakeFrame, ...]) -> None:
        self.frames = frames

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def __iter__(self) -> Iterator[FakeFrame]:
        return iter(self.frames)


@dataclass(frozen=True)
class ExplicitAssociation:
    """Expected normalized association for later domain/adapter tests."""

    lap_index: int
    step_identity: int
    role: str
    duration_kind: str
    duration_value: float


@dataclass(frozen=True)
class WorkoutScenario:
    frames: tuple[FakeFrame, ...]
    domain_associations: tuple[ExplicitAssociation, ...] = ()

    def reader_factory(self, source: str) -> FakeReader:
        return FakeReader(self.frames)


def workout_scenario(variant: Variant = "structured") -> WorkoutScenario:
    """Build fresh messages for 14 laps, 5.6 km, and 26 active minutes.

    Six 400 m efforts take 100 seconds each; each 200 m recovery takes
    120 seconds. The 1 km warm-up and cooldown each take 120 seconds.
    Deliberately simple arithmetic, not a realistic training prescription.
    """
    allowed = {
        "structured",
        "unlabeled",
        "lap_only",
        "sparse_hr",
        "paused",
        "unknown_pause",
        "overlapping",
        "malformed_metadata",
    }
    if variant not in allowed:
        raise ValueError(f"Unknown workout fixture variant: {variant}")
    paused = variant in {"paused", "unknown_pause"}

    def wall_time(seconds: int) -> datetime:
        return START + timedelta(
            seconds=seconds + (30 if paused and seconds >= 170 else 0)
        )

    steps = [
        FakeFrame(
            "workout_step",
            {
                "message_index": 1,
                "wkt_step_name": "400 m effort",
                "intensity": "active",
                "duration_type": "distance",
                "duration_distance": 400.0,
                "target_type": "open",
            },
        ),
        FakeFrame(
            "workout_step",
            {
                "message_index": 2,
                "wkt_step_name": "Recovery",
                "intensity": "recovery",
                "duration_type": "time",
                "duration_time": 120.0,
                "target_type": "open",
            },
        ),
        FakeFrame(
            "workout_step",
            {
                "message_index": 3,
                "duration_type": "repeat_until_steps_cmplt",
                "duration_step": 1,
                "repeat_steps": 6,
            },
        ),
    ]
    pattern = [(1000.0, 120, "warmup")]
    pattern += [(400.0, 100, "active"), (200.0, 120, "recovery")] * 6
    pattern += [(1000.0, 120, "cooldown")]
    laps: list[FakeFrame] = []
    records: list[FakeFrame] = []
    associations: list[ExplicitAssociation] = []
    elapsed = 0
    distance = 0.0
    for index, (length, duration, intensity) in enumerate(pattern, 1):
        laps.append(
            FakeFrame(
                "lap",
                {
                    "message_index": index - 1,
                    "start_time": wall_time(elapsed),
                    "timestamp": wall_time(elapsed + duration),
                    "total_distance": length,
                    "total_timer_time": float(duration),
                    "avg_heart_rate": 160 if intensity == "active" else 140,
                    "max_heart_rate": 170,
                    "avg_running_cadence": 85,
                    "intensity": intensity,
                    "lap_trigger": "manual",
                },
            )
        )
        if intensity in {"active", "recovery"}:
            laps[-1].values["wkt_step_index"] = 1 if intensity == "active" else 2
            associations.append(
                ExplicitAssociation(
                    index,
                    1 if intensity == "active" else 2,
                    "work" if intensity == "active" else "recovery",
                    "distance" if intensity == "active" else "time",
                    400.0 if intensity == "active" else 120.0,
                )
            )
        for offset in range(0, duration, 5):
            hr = 170 if intensity == "active" else max(130, 170 - offset // 2)
            records.append(
                FakeFrame(
                    "record",
                    {
                        "timestamp": wall_time(elapsed + offset),
                        "distance": distance + length * offset / duration,
                        "heart_rate": hr,
                        "speed": length / duration,
                        "cadence": 85,
                        "altitude": 100.0,
                    },
                )
            )
        elapsed += duration
        distance += length
    records.append(
        FakeFrame(
            "record",
            {
                "timestamp": wall_time(elapsed),
                "distance": distance,
                "heart_rate": 130,
                "speed": 1000 / 120,
                "cadence": 85,
                "altitude": 100.0,
            },
        )
    )
    events = [
        FakeFrame(
            "event", {"timestamp": START, "event": "timer", "event_type": "start"}
        ),
        FakeFrame(
            "event",
            {
                "timestamp": wall_time(elapsed),
                "event": "timer",
                "event_type": "stop_all",
            },
        ),
    ]
    if paused:
        events[1:1] = [
            FakeFrame(
                "event",
                {
                    "timestamp": START + timedelta(seconds=170),
                    "event": "timer",
                    "event_type": "stop",
                },
            ),
            FakeFrame(
                "event",
                {
                    "timestamp": START + timedelta(seconds=200),
                    "event": "timer",
                    "event_type": "start",
                },
            ),
        ]
    if variant == "unknown_pause":
        events = []
    if variant == "unlabeled":
        steps = []
        associations = []
        for lap in laps:
            lap.values.pop("intensity")
            lap.values.pop("wkt_step_index", None)
    if variant == "lap_only":
        records = []
        events = []
    if variant == "sparse_hr":
        records = records[::4]
        records[2].values.pop("heart_rate")
    if variant == "overlapping":
        laps[2].values["start_time"] = wall_time(200)
    if variant == "malformed_metadata":
        steps[0].values.update(
            {"message_index": "invalid", "intensity": "unrecognized"}
        )
        steps[1].values["wkt_step_name"] = "\n | [Recovery] *\t"
        laps[1].values["intensity"] = "unrecognized"
        laps[1].values["wkt_step_index"] = "invalid"
        laps[3].values["wkt_step_index"] = 999
        steps.append(FakeFrame("workout_step", dict(steps[1].values)))
        associations = []
    frames = [
        FakeFrame("sport", {"sport": "running"}),
        *steps,
        *records,
        *events,
        *laps,
        FakeFrame(
            "session",
            {
                "start_time": START,
                "timestamp": wall_time(elapsed),
                "total_distance": distance,
                "total_timer_time": float(elapsed),
                "total_elapsed_time": float(elapsed + (30 if paused else 0)),
                "sport": "running",
                "avg_heart_rate": 150,
                "max_heart_rate": 170,
                "avg_running_cadence": 85,
                "avg_speed": distance / elapsed,
            },
        ),
    ]
    return WorkoutScenario(tuple(frames), tuple(associations))
