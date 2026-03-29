from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import fitdecode
import pytest

from fit_to_md.infrastructure.fitdecode.builders import TransitionBuilder
from fit_to_md.infrastructure.fitdecode.extractor import FitdecodeActivityExtractor


class FakeField:
    def __init__(self, name: str, value: object) -> None:
        self.name = name
        self.value = value


class FakeFrame:
    frame_type = fitdecode.FIT_FRAME_DATA

    def __init__(self, name: str, values: dict[str, object]) -> None:
        self.name = name
        self.fields = [FakeField(field_name, field_value) for field_name, field_value in values.items()]


class FakeReader:
    def __init__(self, frames: list[FakeFrame]) -> None:
        self._frames = frames

    def __enter__(self) -> FakeReader:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def __iter__(self):
        return iter(self._frames)


def test_extractor_builds_summary_splits_and_transitions() -> None:
    start = datetime(2026, 3, 29, 6, 0, 0)
    frames = [
        FakeFrame("sport", {"sport": "running", "sub_sport": "trail_running"}),
        FakeFrame(
            "session",
            {
                "start_time": start,
                "timestamp": start + timedelta(seconds=720),
                "total_distance": 2100.0,
                "total_timer_time": 720.0,
                "total_elapsed_time": 750.0,
                "total_ascent": 18.0,
                "total_descent": 10.0,
                "avg_heart_rate": 145,
                "max_heart_rate": 170,
                "avg_running_cadence": 86,
                "avg_fractional_cadence": 0.0,
                "enhanced_avg_speed": 2.9166667,
                "avg_temperature": 17.0,
                "min_temperature": 12.0,
                "max_temperature": 21.0,
            },
        ),
        FakeFrame(
            "lap",
            {
                "start_time": start,
                "timestamp": start + timedelta(seconds=300),
                "total_distance": 1000.0,
                "total_timer_time": 300.0,
                "total_ascent": 10.0,
                "total_descent": 0.0,
                "avg_heart_rate": 138,
                "max_heart_rate": 150,
                "avg_running_cadence": 85,
                "avg_fractional_cadence": 0.0,
            },
        ),
        FakeFrame(
            "lap",
            {
                "start_time": start + timedelta(seconds=300),
                "timestamp": start + timedelta(seconds=600),
                "total_distance": 1000.0,
                "total_timer_time": 300.0,
                "total_ascent": 0.0,
                "total_descent": 5.0,
                "avg_heart_rate": 149,
                "max_heart_rate": 164,
                "avg_running_cadence": 86,
                "avg_fractional_cadence": 0.0,
            },
        ),
        FakeFrame(
            "lap",
            {
                "start_time": start + timedelta(seconds=600),
                "timestamp": start + timedelta(seconds=720),
                "total_distance": 100.0,
                "total_timer_time": 120.0,
                "total_ascent": 8.0,
                "total_descent": 5.0,
                "avg_heart_rate": 152,
                "max_heart_rate": 170,
                "avg_running_cadence": 85,
                "avg_fractional_cadence": 0.0,
            },
        ),
    ]
    frames.extend(_record_frames(start))

    extractor = FitdecodeActivityExtractor(reader_factory=lambda _: FakeReader(frames))

    report = extractor.extract(Path("activity.fit"))

    assert report.summary.activity_type == "Trail Running"
    assert report.summary.total_distance_km == pytest.approx(2.1)
    assert report.summary.total_timer_time_s == pytest.approx(720.0)
    assert report.summary.avg_cadence_spm == 172
    assert report.summary.avg_speed_kmh == pytest.approx(10.5, abs=0.01)
    assert report.summary.avg_temperature_c == pytest.approx(17.0)
    assert report.summary.min_temperature_c == pytest.approx(12.0)
    assert report.summary.max_temperature_c == pytest.approx(21.0)
    assert len(report.splits) == 2
    assert report.splits[0].time_seconds == pytest.approx(300.0, abs=0.01)
    assert report.splits[0].elevation_delta_m == pytest.approx(10.0, abs=0.2)
    assert report.splits[1].time_seconds == pytest.approx(300.0, abs=0.01)
    assert report.splits[1].elevation_delta_m == pytest.approx(-5.0, abs=0.2)
    assert len(report.transitions) == 2
    assert report.transitions[0].label == "End of Lap 1 to Start of Lap 2"
    assert report.transitions[0].samples[0].offset_seconds == -60
    assert report.transitions[0].samples[-1].offset_seconds == 60
    assert report.transitions[0].samples[6].speed_kmh == pytest.approx(12.0, abs=0.1)


def test_extractor_falls_back_to_lap_splits_when_record_distances_missing() -> None:
    start = datetime(2026, 3, 29, 7, 0, 0)
    frames = [
        FakeFrame(
            "session",
            {
                "start_time": start,
                "total_timer_time": 660.0,
                "total_distance": 2000.0,
            },
        ),
        FakeFrame(
            "lap",
            {
                "start_time": start,
                "timestamp": start + timedelta(seconds=330),
                "total_distance": 1000.0,
                "total_timer_time": 330.0,
                "total_ascent": 5.0,
                "total_descent": 2.0,
                "avg_heart_rate": 130,
                "max_heart_rate": 140,
                "avg_running_cadence": 83,
                "avg_fractional_cadence": 0.0,
            },
        ),
        FakeFrame(
            "lap",
            {
                "start_time": start + timedelta(seconds=330),
                "timestamp": start + timedelta(seconds=660),
                "total_distance": 1000.0,
                "total_timer_time": 330.0,
                "total_ascent": 2.0,
                "total_descent": 4.0,
                "avg_heart_rate": 136,
                "max_heart_rate": 146,
                "avg_running_cadence": 84,
                "avg_fractional_cadence": 0.0,
            },
        ),
        FakeFrame("record", {"timestamp": start, "heart_rate": 120}),
        FakeFrame("record", {"timestamp": start + timedelta(seconds=10), "heart_rate": 121}),
    ]

    extractor = FitdecodeActivityExtractor(reader_factory=lambda _: FakeReader(frames))

    report = extractor.extract(Path("activity.fit"))

    assert len(report.splits) == 2
    assert report.splits[0].pace_seconds_per_km == pytest.approx(330.0)
    assert report.splits[0].elevation_delta_m == pytest.approx(3.0)
    assert report.transitions == ()


def test_extractor_normalizes_running_record_cadence_with_fractional_component() -> None:
    start = datetime(2026, 3, 29, 8, 0, 0)
    frames = [
        FakeFrame("sport", {"sport": "running"}),
        FakeFrame(
            "session",
            {
                "start_time": start,
                "total_timer_time": 300.0,
                "total_distance": 1000.0,
            },
        ),
        FakeFrame(
            "record",
            {
                "timestamp": start,
                "distance": 0.0,
                "heart_rate": 120,
                "cadence": 80,
                "fractional_cadence": 0.5,
                "enhanced_speed": 3.2,
                "enhanced_altitude": 10.0,
            },
        ),
        FakeFrame(
            "record",
            {
                "timestamp": start + timedelta(seconds=300),
                "distance": 1000.0,
                "heart_rate": 140,
                "cadence": 80,
                "fractional_cadence": 0.5,
                "enhanced_speed": 3.4,
                "enhanced_altitude": 20.0,
            },
        ),
    ]

    extractor = FitdecodeActivityExtractor(reader_factory=lambda _: FakeReader(frames))

    report = extractor.extract(Path("activity.fit"))

    assert report.summary.avg_cadence_spm == 161
    assert report.splits[0].avg_cadence_spm == 161


def test_extractor_derives_weather_summary_from_record_temperatures_when_session_missing() -> None:
    start = datetime(2026, 3, 29, 8, 30, 0)
    frames = [
        FakeFrame("sport", {"sport": "running"}),
        FakeFrame(
            "session",
            {
                "start_time": start,
                "total_timer_time": 180.0,
                "total_distance": 600.0,
            },
        ),
        FakeFrame(
            "record",
            {
                "timestamp": start,
                "distance": 0.0,
                "temperature": 10,
            },
        ),
        FakeFrame(
            "record",
            {
                "timestamp": start + timedelta(seconds=90),
                "distance": 300.0,
                "temperature": 12,
            },
        ),
        FakeFrame(
            "record",
            {
                "timestamp": start + timedelta(seconds=180),
                "distance": 600.0,
                "temperature": 14,
            },
        ),
    ]

    extractor = FitdecodeActivityExtractor(reader_factory=lambda _: FakeReader(frames))

    report = extractor.extract(Path("activity.fit"))

    assert report.summary.avg_temperature_c == pytest.approx(12.0)
    assert report.summary.min_temperature_c == pytest.approx(10.0)
    assert report.summary.max_temperature_c == pytest.approx(14.0)


def test_extractor_omits_grade_when_stopped_distance_change_is_noise() -> None:
    start = datetime(2026, 3, 29, 9, 0, 0)
    frames = [
        FakeFrame("sport", {"sport": "running"}),
        FakeFrame(
            "session",
            {
                "start_time": start,
                "total_timer_time": 120.0,
                "total_distance": 1000.0,
            },
        ),
        FakeFrame(
            "lap",
            {
                "start_time": start,
                "timestamp": start + timedelta(seconds=60),
                "total_distance": 500.0,
                "total_timer_time": 60.0,
            },
        ),
        FakeFrame(
            "lap",
            {
                "start_time": start + timedelta(seconds=60),
                "timestamp": start + timedelta(seconds=120),
                "total_distance": 500.0,
                "total_timer_time": 60.0,
            },
        ),
        FakeFrame(
            "record",
            {
                "timestamp": start,
                "distance": 0.0,
                "heart_rate": 120,
                "cadence": 80,
                "fractional_cadence": 0.0,
                "enhanced_speed": 3.0,
                "enhanced_altitude": 10.0,
                "temperature": 9,
            },
        ),
        FakeFrame(
            "record",
            {
                "timestamp": start + timedelta(seconds=60),
                "distance": 500.0,
                "heart_rate": 135,
                "cadence": 80,
                "fractional_cadence": 0.0,
                "enhanced_speed": 3.0,
                "enhanced_altitude": 20.0,
                "temperature": 10,
            },
        ),
        FakeFrame(
            "record",
            {
                "timestamp": start + timedelta(seconds=119),
                "distance": 999.9,
                "heart_rate": 145,
                "cadence": 0,
                "fractional_cadence": 0.0,
                "enhanced_speed": 0.0,
                "enhanced_altitude": 30.0,
                "temperature": 11,
            },
        ),
        FakeFrame(
            "record",
            {
                "timestamp": start + timedelta(seconds=120),
                "distance": 1000.0,
                "heart_rate": 144,
                "cadence": 0,
                "fractional_cadence": 0.0,
                "enhanced_speed": 0.0,
                "enhanced_altitude": 30.2,
                "temperature": 11,
            },
        ),
    ]

    extractor = FitdecodeActivityExtractor(
        reader_factory=lambda _: FakeReader(frames),
        transition_builder=TransitionBuilder(sample_interval_s=60, window_s=60),
    )

    report = extractor.extract(Path("activity.fit"))

    assert report.transitions[0].samples[-1].speed_kmh == pytest.approx(0.0)
    assert report.transitions[0].samples[-1].grade_percent is None


def _record_frames(start: datetime) -> list[FakeFrame]:
    frames: list[FakeFrame] = []
    for seconds in range(0, 721, 30):
        distance_m, altitude_m, speed_mps = _record_profile(seconds)
        frames.append(
            FakeFrame(
                "record",
                {
                    "timestamp": start + timedelta(seconds=seconds),
                    "distance": distance_m,
                    "heart_rate": 120 + (seconds // 20),
                    "cadence": 83 + (seconds // 120),
                    "fractional_cadence": 0.0,
                    "enhanced_speed": speed_mps,
                    "enhanced_altitude": altitude_m,
                },
            )
        )
    return frames


def _record_profile(seconds: int) -> tuple[float, float, float]:
    if seconds <= 300:
        return (seconds / 300) * 1000, (seconds / 300) * 10, 1000 / 300
    if seconds <= 600:
        progress = (seconds - 300) / 300
        return 1000 + (progress * 1000), 10 - (progress * 5), 1000 / 300
    progress = (seconds - 600) / 120
    return 2000 + (progress * 100), 5 + (progress * 3), 100 / 120