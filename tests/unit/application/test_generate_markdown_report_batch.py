import io
import json
from datetime import datetime
from http.client import IncompleteRead
from pathlib import Path

import pytest

from fit_to_md.application.use_cases.generate_markdown_report import (
    GeneratedMarkdownReport,
    GenerateMarkdownReport,
    ReportGenerationMetadata,
)
from fit_to_md.application.use_cases.generate_markdown_report_batch import (
    BatchOutputNaming,
    BatchReportStatus,
    GenerateMarkdownReportBatch,
)
from fit_to_md.domain.activity import Activity, ActivitySession
from fit_to_md.domain.activity.ports import InvalidActivityError
from fit_to_md.domain.reporting.entities import FitReport, SessionSummary
from fit_to_md.domain.reporting.ports import ProviderDiagnosticKind
from fit_to_md.infrastructure.weather.open_meteo import (
    OpenMeteoHistoricalWeatherProvider,
)


def _generated(markdown: str, start_time: datetime | None = None):
    return GeneratedMarkdownReport(
        FitReport(
            summary=SessionSummary(
                start_time=start_time,
                activity_name=None,
                activity_type=None,
                total_distance_km=None,
                total_timer_time_s=None,
                total_elapsed_time_s=None,
                total_ascent_m=None,
                total_descent_m=None,
                avg_heart_rate_bpm=None,
                max_heart_rate_bpm=None,
                avg_cadence_spm=None,
                avg_speed_kmh=None,
                avg_temperature_c=None,
                min_temperature_c=None,
                max_temperature_c=None,
            )
        ),
        markdown,
    )


class StubGenerator:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.start_times: dict[Path, datetime | None] = {}
        self.markdown: dict[Path, str] = {}
        self.inspect_errors: dict[Path, Exception] = {}
        self.generate_errors: dict[Path, Exception] = {}

    def inspect(self, source: Path) -> ReportGenerationMetadata:
        self.events.append(f"inspect:{source.name}")
        if source in self.inspect_errors:
            raise self.inspect_errors[source]
        return ReportGenerationMetadata(self.start_times[source])

    def execute_detailed(self, source: Path) -> GeneratedMarkdownReport:
        self.events.append(f"generate:{source.name}")
        if source in self.generate_errors:
            raise self.generate_errors[source]
        return _generated(self.markdown[source], self.start_times.get(source))


class StubWriter:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.existing: set[Path] = set()
        self.contents: dict[Path, str] = {}
        self.write_errors: dict[Path, OSError] = {}

    def is_file(self, path: Path) -> bool:
        return path in self.existing

    def write(self, path: Path, markdown: str) -> None:
        self.events.append(f"write:{path.name}")
        if path in self.write_errors:
            raise self.write_errors[path]
        self.contents[path] = markdown


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self._buffer = io.StringIO(json.dumps(payload))

    def __enter__(self) -> io.StringIO:
        return self._buffer

    def __exit__(self, exc_type, exc, tb) -> None:
        self._buffer.close()
        return None


@pytest.mark.parametrize("legacy", (False, True))
def test_activity_time_existing_output_skips_generation(
    tmp_path: Path, legacy: bool
) -> None:
    events: list[str] = []
    source = tmp_path / "activity.fit"
    start = datetime(2026, 9, 17, 7, 5)
    generator = StubGenerator(events)
    generator.start_times[source] = start
    writer = StubWriter(events)
    separator = ":" if legacy else "-"
    writer.existing.add(tmp_path / f"2026-09-17 07{separator}05.md")

    outcomes = list(
        GenerateMarkdownReportBatch(generator, writer).execute(
            [source], BatchOutputNaming.ACTIVITY_TIME
        )
    )

    assert [outcome.status for outcome in outcomes] == [
        BatchReportStatus.SKIPPED_EXISTING
    ]
    assert events == ["inspect:activity.fit"]
    assert writer.contents == {}


def test_activity_time_collisions_are_planned_before_generation(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    first = tmp_path / "a.fit"
    second = tmp_path / "b.fit"
    unique = tmp_path / "c.fit"
    duplicate_time = datetime(2026, 9, 17, 7, 5)
    generator = StubGenerator(events)
    generator.start_times = {
        first: duplicate_time,
        second: duplicate_time,
        unique: datetime(2026, 9, 17, 8, 10),
    }
    generator.markdown[unique] = "# unique"
    writer = StubWriter(events)

    outcomes = list(
        GenerateMarkdownReportBatch(generator, writer).execute(
            [first, second, unique], BatchOutputNaming.ACTIVITY_TIME
        )
    )

    assert [outcome.status for outcome in outcomes] == [
        BatchReportStatus.COLLISION,
        BatchReportStatus.COLLISION,
        BatchReportStatus.SUCCESS,
    ]
    assert outcomes[0].collision_sources == (first, second)
    assert "generate:a.fit" not in events
    assert "generate:b.fit" not in events
    assert writer.contents[tmp_path / "2026-09-17 08-10.md"] == "# unique"


def test_batch_writes_activity_time_output_to_selected_directory(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    source = tmp_path / "activities" / "activity.fit"
    output_directory = tmp_path / "reports"
    generator = StubGenerator(events)
    generator.start_times[source] = datetime(2026, 9, 17, 7, 5)
    generator.markdown[source] = "# report"
    writer = StubWriter(events)

    outcomes = list(
        GenerateMarkdownReportBatch(generator, writer).execute(
            [source],
            BatchOutputNaming.ACTIVITY_TIME,
            output_directory=output_directory,
        )
    )

    expected_output = output_directory / "2026-09-17 07-05.md"
    assert [outcome.status for outcome in outcomes] == [BatchReportStatus.SUCCESS]
    assert outcomes[0].output == expected_output
    assert writer.contents == {expected_output: "# report"}


def test_batch_writes_source_name_output_to_selected_directory(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    source = tmp_path / "activities" / "activity.fit"
    output_directory = tmp_path / "reports"
    generator = StubGenerator(events)
    generator.markdown[source] = "# report"
    writer = StubWriter(events)

    outcomes = list(
        GenerateMarkdownReportBatch(generator, writer).execute(
            [source],
            BatchOutputNaming.SOURCE_NAME,
            output_directory=output_directory,
        )
    )

    expected_output = output_directory / "activity.md"
    assert [outcome.status for outcome in outcomes] == [BatchReportStatus.SUCCESS]
    assert outcomes[0].output == expected_output
    assert writer.contents == {expected_output: "# report"}


def test_source_name_batch_continues_after_processing_and_write_failures(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    read_failure = tmp_path / "a.fit"
    write_failure = tmp_path / "b.fit"
    successful = tmp_path / "c.fit"
    generator = StubGenerator(events)
    generator.generate_errors[read_failure] = InvalidActivityError("invalid")
    generator.markdown = {
        write_failure: "# cannot write",
        successful: "# successful",
    }
    writer = StubWriter(events)
    writer.write_errors[write_failure.with_suffix(".md")] = OSError("read only")

    outcomes = list(
        GenerateMarkdownReportBatch(generator, writer).execute(
            [read_failure, write_failure, successful],
            BatchOutputNaming.SOURCE_NAME,
        )
    )

    assert [outcome.status for outcome in outcomes] == [
        BatchReportStatus.PROCESSING_FAILED,
        BatchReportStatus.WRITE_FAILED,
        BatchReportStatus.SUCCESS,
    ]
    assert writer.contents[successful.with_suffix(".md")] == "# successful"


def test_source_name_batch_continues_after_interrupted_weather_response(
    tmp_path: Path,
) -> None:
    sources = (tmp_path / "a.fit", tmp_path / "b.fit")
    activity = Activity(
        session=ActivitySession(
            start_time=datetime(2026, 9, 17, 8, 0),
            start_latitude_deg=48.85,
            start_longitude_deg=2.35,
        ),
        sport="running",
    )

    class Reader:
        def read(self, source: Path) -> Activity:
            return activity

    class Renderer:
        def render(self, report: FitReport) -> str:
            return "# report"

    responses = iter(
        (
            IncompleteRead(b'{"hourly":'),
            FakeResponse(
                {
                    "hourly": {
                        "time": ["2026-09-17T08:00"],
                        "temperature_2m": [14.0],
                        "apparent_temperature": [13.0],
                        "weather_code": [1],
                        "wind_speed_10m": [8.0],
                        "wind_direction_10m": [180],
                    }
                }
            ),
        )
    )

    def fake_urlopen(*args, **kwargs):
        response = next(responses)
        if isinstance(response, BaseException):
            raise response
        return response

    generator = GenerateMarkdownReport(
        reader=Reader(),
        renderer=Renderer(),
        weather_provider=OpenMeteoHistoricalWeatherProvider(urlopen_fn=fake_urlopen),
    )
    writer = StubWriter([])

    outcomes = list(
        GenerateMarkdownReportBatch(generator, writer).execute(
            sources, BatchOutputNaming.SOURCE_NAME
        )
    )

    assert [outcome.status for outcome in outcomes] == [
        BatchReportStatus.SUCCESS,
        BatchReportStatus.SUCCESS,
    ]
    assert outcomes[0].generated is not None
    assert outcomes[0].generated.diagnostics[0].kind is (
        ProviderDiagnosticKind.PROVIDER_UNAVAILABLE
    )
    assert outcomes[1].generated is not None
    assert outcomes[1].generated.report.summary.weather is not None
    assert set(writer.contents) == {
        sources[0].with_suffix(".md"),
        sources[1].with_suffix(".md"),
    }


def test_successes_are_written_and_yielded_incrementally(tmp_path: Path) -> None:
    events: list[str] = []
    first = tmp_path / "a.fit"
    second = tmp_path / "b.fit"
    generator = StubGenerator(events)
    generator.markdown = {first: "# first", second: "# second"}
    writer = StubWriter(events)
    outcomes = GenerateMarkdownReportBatch(generator, writer).execute(
        [first, second], BatchOutputNaming.SOURCE_NAME
    )

    first_outcome = next(outcomes)

    assert first_outcome.status is BatchReportStatus.SUCCESS
    assert events == ["generate:a.fit", "write:a.md"]

    second_outcome = next(outcomes)

    assert second_outcome.status is BatchReportStatus.SUCCESS
    assert events == [
        "generate:a.fit",
        "write:a.md",
        "generate:b.fit",
        "write:b.md",
    ]
