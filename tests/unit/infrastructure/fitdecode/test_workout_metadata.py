from dataclasses import replace
from pathlib import Path

import pytest

from fit_to_md.domain.activity.timeline import TimelineIssue, TimelineSource
from fit_to_md.domain.activity.workout import (
    DurationKind,
    LapRole,
    TargetKind,
    WorkoutIssue,
)
from fit_to_md.infrastructure.fitdecode.reader import FitdecodeActivityReader
from tests.support.workout import FakeFrame, WorkoutScenario, workout_scenario


def read(scenario: WorkoutScenario):
    return FitdecodeActivityReader(scenario.reader_factory).read(Path("synthetic.fit"))


def lap(scenario, index=1):
    return [f for f in scenario.frames if f.name == "lap"][index]


def step(scenario, index=0):
    return [f for f in scenario.frames if f.name == "workout_step"][index]


def test_repeated_explicit_links_match_fixture_expectations() -> None:
    scenario = workout_scenario()
    activity = read(scenario)
    assert [x.index for x in activity.laps] == list(range(1, 15))
    for association in scenario.domain_associations:
        item = activity.laps[association.lap_index - 1]
        assert item.role == association.role
        assert item.workout_step.identity == str(association.step_identity)
        assert item.workout_step.duration.value == association.duration_value
        assert item.workout_step.has_comparable_definition
    assert activity.laps[1].label == "400 m effort"
    assert activity.laps[1].workout_step.duration.kind == DurationKind.METERS
    assert activity.laps[2].workout_step.duration.kind == DurationKind.SECONDS
    assert activity.laps[0].role == LapRole.WARMUP
    assert activity.laps[-1].role == LapRole.COOLDOWN


def test_definitions_after_laps_produce_identical_associations() -> None:
    scenario = workout_scenario()
    reordered = replace(
        scenario,
        frames=tuple(f for f in scenario.frames if f.name != "workout_step")
        + tuple(f for f in scenario.frames if f.name == "workout_step"),
    )
    assert read(reordered) == read(scenario)


@pytest.mark.parametrize(
    "value,role",
    [
        ("active", LapRole.WORK),
        ("interval", LapRole.WORK),
        ("rest", LapRole.RECOVERY),
        ("recovery", LapRole.RECOVERY),
        ("warmup", LapRole.WARMUP),
        ("cooldown", LapRole.COOLDOWN),
        ("other", LapRole.UNKNOWN),
        ("unknown", LapRole.UNKNOWN),
        (None, LapRole.UNKNOWN),
        ([], LapRole.UNKNOWN),
    ],
)
def test_native_role_mapping_without_links(value, role) -> None:
    scenario = workout_scenario("unlabeled")
    lap(scenario).values["intensity"] = value
    assert read(scenario).laps[1].role == role


def test_native_role_wins_conflict_and_unknown_falls_back() -> None:
    scenario = workout_scenario()
    lap(scenario).values["intensity"] = "rest"
    result = read(scenario).laps[1]
    assert result.role == LapRole.RECOVERY
    assert WorkoutIssue.ROLE_CONFLICT in result.workout_issues
    lap(scenario).values["intensity"] = "unknown"
    assert read(scenario).laps[1].role == LapRole.WORK


@pytest.mark.parametrize(
    "reference,issue",
    [
        (None, WorkoutIssue.MISSING_ASSOCIATION),
        (999, WorkoutIssue.MISSING_DEFINITION),
        ("1", WorkoutIssue.INVALID_ASSOCIATION),
        (True, WorkoutIssue.INVALID_ASSOCIATION),
        (-1, WorkoutIssue.INVALID_ASSOCIATION),
        (65535, WorkoutIssue.INVALID_ASSOCIATION),
        (65536, WorkoutIssue.INVALID_ASSOCIATION),
        (0x1001, WorkoutIssue.INVALID_ASSOCIATION),
        (1.0, WorkoutIssue.INVALID_ASSOCIATION),
        ([], WorkoutIssue.INVALID_ASSOCIATION),
        (3, WorkoutIssue.CONTROL_STEP),
    ],
)
def test_invalid_links_keep_totals_and_never_infer(reference, issue) -> None:
    scenario = workout_scenario()
    lap(scenario).values["wkt_step_index"] = reference
    result = read(scenario).laps[1]
    assert result.workout_step is None
    assert result.label is None
    assert issue in result.workout_issues
    assert result.total_distance_m == 400
    assert result.role == LapRole.WORK


def test_selected_flag_is_not_part_of_identity_and_duplicates_are_ambiguous() -> None:
    scenario = workout_scenario()
    lap(scenario).values["wkt_step_index"] = 0x8001
    assert read(scenario).laps[1].workout_step.identity == "1"
    duplicate = FakeFrame(
        "workout_step", dict(step(scenario).values, message_index=0x8001)
    )
    scenario = replace(scenario, frames=(*scenario.frames, duplicate))
    result = read(scenario).laps[1]
    assert result.workout_step is None
    assert result.workout_issues == (WorkoutIssue.AMBIGUOUS_DEFINITION,)


@pytest.mark.parametrize(
    "name,expected",
    [(" \t\n", None), (123, None), ("\n  400 m | effort  ", "400 m | effort")],
)
def test_label_normalization(name, expected) -> None:
    scenario = workout_scenario()
    step(scenario).values["wkt_step_name"] = name
    assert read(scenario).laps[1].label == expected


@pytest.mark.parametrize(
    "updates,issue",
    [
        ({"duration_distance": float("nan")}, WorkoutIssue.UNSUPPORTED_DURATION),
        ({"duration_distance": -1}, WorkoutIssue.UNSUPPORTED_DURATION),
        ({"duration_type": []}, WorkoutIssue.UNSUPPORTED_DURATION),
        ({"target_type": "heart_rate"}, WorkoutIssue.UNSUPPORTED_TARGET),
        ({"target_type": "power"}, WorkoutIssue.UNSUPPORTED_TARGET),
        ({"secondary_target_type": "heart_rate"}, WorkoutIssue.UNSUPPORTED_TARGET),
        ({"secondary_target_value": 3}, WorkoutIssue.UNSUPPORTED_TARGET),
    ],
)
def test_unsupported_definitions_keep_identity_but_cannot_be_compared(
    updates, issue
) -> None:
    scenario = workout_scenario()
    step(scenario).values.update(updates)
    result = read(scenario).laps[1]
    assert result.workout_step.identity == "1"
    assert not result.workout_step.has_comparable_definition
    assert issue in result.workout_issues
    assert result.total_timer_time_s == 100


@pytest.mark.parametrize(
    "kind,normalized",
    [("speed", TargetKind.SPEED_MPS), ("cadence", TargetKind.CADENCE_RPM)],
)
def test_custom_targets_use_decoded_units_once(kind, normalized) -> None:
    scenario = workout_scenario()
    values = step(scenario).values
    values.update(
        {
            "target_type": kind,
            f"target_{kind}_zone": 0,
            f"custom_target_{kind}_low": 3.0,
            f"custom_target_{kind}_high": 4.5,
        }
    )
    target = read(scenario).laps[1].workout_step.target
    assert (target.kind, target.lower, target.upper) == (normalized, 3, 4.5)
    values[f"target_{kind}_zone"] = 1
    assert read(scenario).laps[1].workout_step.target is None
    values[f"target_{kind}_zone"] = 0
    values[f"custom_target_{kind}_high"] = float("inf")
    assert read(scenario).laps[1].workout_step.target is None


def test_malformed_optional_metadata_preserves_activity() -> None:
    activity = read(workout_scenario("malformed_metadata"))
    assert len(activity.laps) == 14
    assert activity.session.total_distance_m == 5600
    assert activity.laps[1].workout_issues == (WorkoutIssue.INVALID_ASSOCIATION,)
    assert activity.laps[3].workout_issues == (WorkoutIssue.MISSING_DEFINITION,)
    assert activity.laps[2].workout_issues == (WorkoutIssue.AMBIGUOUS_DEFINITION,)


@pytest.mark.parametrize(
    "variant,source",
    [
        ("structured", TimelineSource.TIMER_EVENTS),
        ("paused", TimelineSource.TIMER_EVENTS),
        ("lap_only", TimelineSource.SESSION_TOTALS),
        ("unknown_pause", TimelineSource.UNKNOWN),
    ],
)
def test_reader_exposes_timing_evidence(variant, source) -> None:
    activity = read(workout_scenario(variant))
    assert activity.active_timeline.source == source
    if source != TimelineSource.UNKNOWN:
        assert (
            sum(
                (i.end - i.start).total_seconds()
                for i in activity.active_timeline.intervals
            )
            == 1560
        )
    if variant == "paused":
        assert len(activity.active_timeline.intervals) == 2
        assert activity.records[-1].elapsed_time_s == 1560


@pytest.mark.parametrize(
    "updates", [{"event_type": "unknown"}, {"event_type": None}, {"timestamp": None}]
)
def test_malformed_timer_event_marks_new_timeline_unknown(updates) -> None:
    scenario = workout_scenario()
    next(f for f in scenario.frames if f.name == "event").values.update(updates)
    result = read(scenario)
    assert result.active_timeline.issue == TimelineIssue.INVALID_EVENTS
    assert result.session.total_timer_time_s == 1560


def test_open_duration_and_secondary_open_target() -> None:
    scenario = workout_scenario()
    step(scenario).values.update(
        {"duration_type": "open", "secondary_target_type": "open"}
    )
    result = read(scenario).laps[1].workout_step
    assert result.duration.kind == DurationKind.OPEN
    assert result.secondary_target.kind == TargetKind.OPEN
    assert result.has_comparable_definition
    step(scenario).values["secondary_target_type"] = None
    assert read(scenario).laps[1].workout_step.has_comparable_definition


def test_unlinked_definitions_never_infer_roles_or_labels() -> None:
    scenario = workout_scenario()
    for frame in scenario.frames:
        if frame.name == "lap":
            frame.values.pop("wkt_step_index", None)
            frame.values.pop("intensity", None)
    activity = read(scenario)
    assert all(item.role == LapRole.UNKNOWN for item in activity.laps)
    assert all(
        item.label is None and item.workout_step is None for item in activity.laps
    )


def test_lap_only_constructor_defaults_remain_compatible() -> None:
    from fit_to_md.domain.activity.entities import ActivityLap

    item = ActivityLap(
        1, None, None, None, None, None, None, None, None, None, None, None, None
    )
    assert item.role == LapRole.UNKNOWN
    assert item.label is None and item.workout_step is None
    assert item.workout_issues == ()


def test_empty_fit_records_stop_hr_coverage_without_changing_legacy_records() -> None:
    from datetime import timedelta

    from fit_to_md.domain.reporting.heart_rate_zones import (
        HeartRateZoneBoundaries,
        HeartRateZoneBuilder,
    )
    from tests.support.workout import START

    scenario = WorkoutScenario(
        (
            FakeFrame("sport", {"sport": "running"}),
            FakeFrame("record", {"timestamp": START, "heart_rate": 150}),
            FakeFrame("record", {"timestamp": START + timedelta(seconds=1)}),
            FakeFrame(
                "record", {"timestamp": START + timedelta(seconds=5), "heart_rate": 160}
            ),
            FakeFrame("record", {"timestamp": START + timedelta(seconds=6)}),
            FakeFrame("record", {"heart_rate": 180}),
            FakeFrame(
                "lap",
                {
                    "start_time": START,
                    "timestamp": START + timedelta(seconds=6),
                    "total_timer_time": 6.0,
                },
            ),
            FakeFrame(
                "session",
                {
                    "start_time": START,
                    "timestamp": START + timedelta(seconds=6),
                    "total_timer_time": 6.0,
                    "total_elapsed_time": 6.0,
                    "sport": "running",
                },
            ),
        )
    )

    activity = read(scenario)
    report = HeartRateZoneBuilder(HeartRateZoneBoundaries((130, 145, 160, 175))).build(
        activity
    )

    assert [record.heart_rate_bpm for record in activity.records] == [150, 160]
    assert [record.heart_rate_bpm for record in activity.record_samples] == [
        150,
        None,
        160,
        None,
    ]
    assert report.session.zone_seconds == (0.0, 0.0, 1.0, 1.0, 0.0)
    assert report.session.coverage.covered_seconds == 2.0
    assert report.session.unknown_seconds == 4.0
    assert len(report.laps) == 1
    assert report.laps[0].measurement.zone_seconds == (0.0, 0.0, 1.0, 1.0, 0.0)
