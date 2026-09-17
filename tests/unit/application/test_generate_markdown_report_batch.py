from datetime import datetime
from pathlib import Path

import pytest

from fit_to_md.application.use_cases.generate_markdown_report import (
    GeneratedMarkdownReport,
    ReportGenerationMetadata,
)
from fit_to_md.application.use_cases.generate_markdown_report_batch import (
    BatchOutputNaming,
    BatchReportStatus,
    GenerateMarkdownReportBatch,
)
from fit_to_md.domain.activity.ports import InvalidActivityError
from fit_to_md.domain.reporting.entities import FitReport, SessionSummary


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
