"""Translate optional FIT workout metadata without discarding activity totals."""

from dataclasses import replace
from math import isfinite

from fit_to_md.domain.activity.entities import ActivityLap
from fit_to_md.domain.activity.workout import (
    DurationKind,
    LapRole,
    TargetKind,
    WorkoutDuration,
    WorkoutIssue,
    WorkoutStep,
    WorkoutTarget,
)

_ROLES = {
    "active": LapRole.WORK,
    "interval": LapRole.WORK,
    "rest": LapRole.RECOVERY,
    "recovery": LapRole.RECOVERY,
    "warmup": LapRole.WARMUP,
    "cooldown": LapRole.COOLDOWN,
}


def _role(value: object) -> LapRole:
    return (
        _ROLES.get(value, LapRole.UNKNOWN)
        if isinstance(value, str)
        else LapRole.UNKNOWN
    )


def _index(value: object) -> int | None:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value < 0xFFFF
        or value & 0x7000
    ):
        return None
    return value & 0x0FFF


def _number(value: object) -> float | None:
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and isfinite(value)
    ):
        return float(value)
    return None


def _duration(values: dict[str, object]) -> WorkoutDuration | None:
    kind = values.get("duration_type")
    if kind == "open":
        return WorkoutDuration(DurationKind.OPEN)
    field, normalized = {
        "time": ("duration_time", DurationKind.SECONDS),
        "distance": ("duration_distance", DurationKind.METERS),
    }.get(kind if isinstance(kind, str) else "", ("", DurationKind.OPEN))
    value = _number(values.get(field))
    if not field or value is None or value <= 0:
        return None
    return WorkoutDuration(normalized, value)


def _target(values: dict[str, object], prefix: str = "") -> WorkoutTarget | None:
    kind = values.get(f"{prefix}target_type")
    if kind == "open":
        return WorkoutTarget(TargetKind.OPEN)
    # Secondary custom subfield semantics are not verified; never guess units.
    if prefix:
        return None
    if kind not in ("speed", "cadence"):
        return None
    zone = values.get(f"target_{kind}_zone")
    if isinstance(zone, bool) or zone != 0:
        return None  # A zone index is not a numeric target range.
    lower = _number(values.get(f"custom_target_{kind}_low"))
    upper = _number(values.get(f"custom_target_{kind}_high"))
    if lower is None or upper is None or lower < 0 or upper <= 0 or lower > upper:
        return None
    return WorkoutTarget(
        TargetKind.SPEED_MPS if kind == "speed" else TargetKind.CADENCE_RPM,
        lower,
        upper,
    )


def _step(identity: int, values: dict[str, object]) -> WorkoutStep:
    name = values.get("wkt_step_name")
    label = " ".join(name.split()) if isinstance(name, str) else ""
    duration = _duration(values)
    target = _target(values)
    secondary_present = any(
        key.startswith("secondary_") and value is not None
        for key, value in values.items()
    )
    secondary = _target(values, "secondary_") if secondary_present else None
    issues = []
    if duration is None:
        issues.append(WorkoutIssue.UNSUPPORTED_DURATION)
    if target is None or (secondary_present and secondary is None):
        issues.append(WorkoutIssue.UNSUPPORTED_TARGET)
    return WorkoutStep(
        str(identity),
        label or None,
        _role(values.get("intensity")),
        duration,
        target,
        secondary,
        tuple(issues),
    )


def attach_workout_metadata(
    laps: tuple[ActivityLap, ...],
    lap_values: tuple[dict[str, object], ...],
    step_values: tuple[dict[str, object], ...],
) -> tuple[ActivityLap, ...]:
    """Resolve explicit links only, after every source definition has been read."""
    definitions: dict[int, list[dict[str, object]]] = {}
    for values in step_values:
        identity = _index(values.get("message_index"))
        if identity is not None:
            definitions.setdefault(identity, []).append(values)
    result = []
    for lap, values in zip(laps, lap_values, strict=True):
        role = _role(values.get("intensity"))
        step = None
        issues: list[WorkoutIssue] = []
        reference = values.get("wkt_step_index")
        identity = _index(reference)
        if reference is None:
            issues.append(WorkoutIssue.MISSING_ASSOCIATION)
        elif identity is None:
            issues.append(WorkoutIssue.INVALID_ASSOCIATION)
        elif identity not in definitions:
            issues.append(WorkoutIssue.MISSING_DEFINITION)
        elif len(definitions[identity]) != 1:
            issues.append(WorkoutIssue.AMBIGUOUS_DEFINITION)
        else:
            definition = definitions[identity][0]
            duration_type = definition.get("duration_type")
            if isinstance(duration_type, str) and duration_type.startswith(
                "repeat_until_"
            ):
                issues.append(WorkoutIssue.CONTROL_STEP)
            else:
                step = _step(identity, definition)
                issues.extend(step.issues)
                if role == LapRole.UNKNOWN:
                    role = step.role
                elif step.role != LapRole.UNKNOWN and role != step.role:
                    issues.append(WorkoutIssue.ROLE_CONFLICT)
        result.append(
            replace(
                lap,
                role=role,
                label=step.label if step else None,
                workout_step=step,
                workout_issues=tuple(issues),
            )
        )
    return tuple(result)
