"""Format-independent metadata for recorded workout laps."""

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite


class LapRole(StrEnum):
    UNKNOWN = "unknown"
    WARMUP = "warmup"
    WORK = "work"
    RECOVERY = "recovery"
    COOLDOWN = "cooldown"


class WorkoutIssue(StrEnum):
    MISSING_ASSOCIATION = "missing_association"
    INVALID_ASSOCIATION = "invalid_association"
    MISSING_DEFINITION = "missing_definition"
    AMBIGUOUS_DEFINITION = "ambiguous_definition"
    CONTROL_STEP = "control_step"
    UNSUPPORTED_DURATION = "unsupported_duration"
    UNSUPPORTED_TARGET = "unsupported_target"
    ROLE_CONFLICT = "role_conflict"


class DurationKind(StrEnum):
    OPEN = "open"
    SECONDS = "seconds"
    METERS = "meters"


@dataclass(frozen=True)
class WorkoutDuration:
    kind: DurationKind
    value: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, DurationKind):
            raise ValueError("Unknown workout duration kind")
        if self.kind == DurationKind.OPEN:
            if self.value is not None:
                raise ValueError("Open duration cannot have a value")
        elif (
            self.value is None
            or isinstance(self.value, bool)
            or not isfinite(self.value)
            or self.value <= 0
        ):
            raise ValueError("Workout duration must be finite and positive")


class TargetKind(StrEnum):
    OPEN = "open"
    SPEED_MPS = "speed_mps"
    CADENCE_RPM = "cadence_rpm"


@dataclass(frozen=True)
class WorkoutTarget:
    kind: TargetKind
    lower: float | None = None
    upper: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, TargetKind):
            raise ValueError("Unknown workout target kind")
        if self.kind == TargetKind.OPEN:
            if self.lower is not None or self.upper is not None:
                raise ValueError("Open target cannot have bounds")
        elif (
            self.lower is None
            or self.upper is None
            or isinstance(self.lower, bool)
            or isinstance(self.upper, bool)
            or not isfinite(self.lower)
            or not isfinite(self.upper)
            or self.lower < 0
            or self.upper <= 0
            or self.lower > self.upper
        ):
            raise ValueError("Target bounds must be finite, ordered and nonnegative")


@dataclass(frozen=True)
class WorkoutStep:
    identity: str
    label: str | None
    role: LapRole
    duration: WorkoutDuration | None
    target: WorkoutTarget | None
    secondary_target: WorkoutTarget | None = None
    issues: tuple[WorkoutIssue, ...] = ()

    @property
    def has_comparable_definition(self) -> bool:
        return self.duration is not None and self.target is not None and not self.issues
