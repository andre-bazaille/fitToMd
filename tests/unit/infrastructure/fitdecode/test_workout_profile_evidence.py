"""Installed-profile evidence; revise the support matrix on profile changes."""

from fitdecode import profile


def _fields(message: str) -> dict:
    return next(m.fields for m in profile.MESSAGE_TYPES.values() if m.name == message)


def test_profile_exposes_explicit_lap_workout_step_reference() -> None:
    fields = _fields("lap")
    assert fields[23].name == "intensity"
    assert fields[24].name == "lap_trigger"
    assert fields[254].name == "message_index"
    assert fields[71].name == "wkt_step_index"
    assert fields[71].type.name == "message_index"
    assert "wkt_step_name" not in {f.name for f in fields.values()}
    assert profile.FIELD_TYPES["lap_trigger"].enum[0] == "manual"
    assert profile.FIELD_TYPES["lap_trigger"].enum[2] == "distance"


def test_profile_roles_and_workout_definitions_are_available() -> None:
    assert profile.FIELD_TYPES["intensity"].enum == {
        0: "active",
        1: "rest",
        2: "warmup",
        3: "cooldown",
        4: "recovery",
        5: "interval",
        6: "other",
    }
    fields = _fields("workout_step")
    assert fields[254].name == "message_index"
    assert fields[0].name == "wkt_step_name"
    assert fields[7].name == "intensity"
    assert fields[3].name == "target_type"
    assert {s.name for s in fields[2].subfields} >= {
        "duration_time",
        "duration_distance",
        "duration_step",
    }
    assert "repeat_steps" in {s.name for s in fields[4].subfields}


def test_event_profile_does_not_supply_a_decoded_step_reference() -> None:
    fields = _fields("event")
    names = {f.name for f in fields.values()}
    names.update(s.name for f in fields.values() for s in f.subfields or ())
    assert not {"wkt_step_index", "workout_step_index"} & names
    assert {"event", "event_type", "timestamp", "timer_trigger"} <= names
