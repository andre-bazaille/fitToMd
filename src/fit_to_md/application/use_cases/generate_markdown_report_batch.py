from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from fit_to_md.application.ports import MarkdownReportWriter
from fit_to_md.application.use_cases.generate_markdown_report import (
    GeneratedMarkdownReport,
    GenerateMarkdownReport,
)
from fit_to_md.domain.activity.ports import ActivityReadError


class ActivityTimeUnavailableError(Exception):
    """An activity lacks the metadata required for time-based output naming."""


class BatchOutputNaming(Enum):
    SOURCE_NAME = "source_name"
    ACTIVITY_TIME = "activity_time"


class BatchReportStatus(Enum):
    SUCCESS = "success"
    SKIPPED_EXISTING = "skipped_existing"
    COLLISION = "collision"
    PROCESSING_FAILED = "processing_failed"
    WRITE_FAILED = "write_failed"


@dataclass(frozen=True)
class BatchReportOutcome:
    source: Path
    status: BatchReportStatus
    output: Path | None = None
    generated: GeneratedMarkdownReport | None = None
    error: Exception | None = None
    collision_sources: tuple[Path, ...] = ()


@dataclass(frozen=True)
class _PlannedReport:
    source: Path
    output: Path | None
    matching_outputs: tuple[Path, ...] = ()
    existing: bool = False
    error: Exception | None = None


class GenerateMarkdownReportBatch:
    def __init__(
        self,
        generator: GenerateMarkdownReport,
        writer: MarkdownReportWriter,
    ) -> None:
        self._generator = generator
        self._writer = writer

    def execute(
        self,
        sources: Sequence[Path],
        naming: BatchOutputNaming,
        output_directory: Path | None = None,
    ) -> Iterator[BatchReportOutcome]:
        plans = tuple(
            self._plan(source, naming, output_directory) for source in sources
        )
        output_groups = self._output_groups(plans)

        for plan in plans:
            if plan.error is not None:
                yield BatchReportOutcome(
                    source=plan.source,
                    status=BatchReportStatus.PROCESSING_FAILED,
                    error=plan.error,
                )
                continue

            assert plan.output is not None
            if plan.existing:
                yield BatchReportOutcome(
                    source=plan.source,
                    output=plan.output,
                    status=BatchReportStatus.SKIPPED_EXISTING,
                )
                continue

            collision_sources = output_groups.get(plan.output, ())
            if len(collision_sources) > 1:
                yield BatchReportOutcome(
                    source=plan.source,
                    output=plan.output,
                    status=BatchReportStatus.COLLISION,
                    collision_sources=collision_sources,
                )
                continue

            try:
                if any(self._writer.is_file(path) for path in plan.matching_outputs):
                    yield BatchReportOutcome(
                        source=plan.source,
                        output=plan.output,
                        status=BatchReportStatus.SKIPPED_EXISTING,
                    )
                    continue
                generated = self._generator.execute_detailed(plan.source)
            except (ActivityReadError, ActivityTimeUnavailableError, OSError) as error:
                yield BatchReportOutcome(
                    source=plan.source,
                    output=plan.output,
                    status=BatchReportStatus.PROCESSING_FAILED,
                    error=error,
                )
                continue

            try:
                self._writer.write(plan.output, generated.markdown)
            except OSError as error:
                yield BatchReportOutcome(
                    source=plan.source,
                    output=plan.output,
                    status=BatchReportStatus.WRITE_FAILED,
                    generated=generated,
                    error=error,
                )
                continue

            yield BatchReportOutcome(
                source=plan.source,
                output=plan.output,
                status=BatchReportStatus.SUCCESS,
                generated=generated,
            )

    def _plan(
        self,
        source: Path,
        naming: BatchOutputNaming,
        output_directory: Path | None,
    ) -> _PlannedReport:
        if naming is BatchOutputNaming.SOURCE_NAME:
            output_name = source.with_suffix(".md").name
            output = (
                output_directory / output_name
                if output_directory is not None
                else source.with_suffix(".md")
            )
            try:
                existing = self._writer.is_file(output)
            except OSError as error:
                return _PlannedReport(source, None, error=error)
            return _PlannedReport(source, output, (output,), existing=existing)

        try:
            metadata = self._generator.inspect(source)
            matching_outputs = activity_time_output_candidates(
                source,
                metadata.start_time,
                output_directory=output_directory,
            )
            existing = any(self._writer.is_file(path) for path in matching_outputs)
        except (ActivityReadError, ActivityTimeUnavailableError, OSError) as error:
            return _PlannedReport(source, None, error=error)
        return _PlannedReport(
            source,
            matching_outputs[0],
            matching_outputs,
            existing=existing,
        )

    @staticmethod
    def _output_groups(
        plans: Sequence[_PlannedReport],
    ) -> dict[Path, tuple[Path, ...]]:
        grouped: dict[Path, list[Path]] = {}
        for plan in plans:
            if plan.output is not None and not plan.existing:
                grouped.setdefault(plan.output, []).append(plan.source)
        return {output: tuple(sources) for output, sources in grouped.items()}


def activity_time_output_candidates(
    source: Path,
    start_time: datetime | None,
    *,
    output_directory: Path | None = None,
) -> tuple[Path, ...]:
    if start_time is None:
        raise ActivityTimeUnavailableError(
            "Activity start time unavailable; cannot use activity time for output name."
        )
    destination = output_directory or source.parent
    portable_output = destination / f"{start_time.strftime('%Y-%m-%d %H-%M')}.md"
    legacy_output = destination / f"{start_time.strftime('%Y-%m-%d %H:%M')}.md"
    return portable_output, legacy_output
