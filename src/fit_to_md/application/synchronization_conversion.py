"""Generate reports from the verified local FITs of a synchronization run."""

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from fit_to_md.application.synchronization import Clock
from fit_to_md.application.synchronization_storage import (
    ConversionOptions,
    FailureReason,
    OutputConflict,
    OutputStorage,
    RegistryEntry,
    Stage,
    StorageFailure,
    has_registered_report,
)
from fit_to_md.application.use_cases.generate_markdown_report import (
    GeneratedMarkdownReport,
)
from fit_to_md.domain.activity.ports import (
    InvalidActivityError,
    UnsupportedActivityError,
)
from fit_to_md.domain.synchronization.model import ConversionState, RemoteActivity


class LocalReportGenerator(Protocol):
    def execute_detailed(self, source: Path) -> GeneratedMarkdownReport: ...


class ConvertSynchronizationReports:
    """Record independent conversion outcomes under the synchronization lock."""

    def __init__(
        self,
        output: OutputStorage,
        generator: LocalReportGenerator,
        options: ConversionOptions,
        clock: Clock,
        progress: Callable[[RemoteActivity, str], None],
        diagnostics: Callable[[GeneratedMarkdownReport, Path], None],
    ) -> None:
        self._output = output
        self._generator = generator
        self._options = options
        self._clock = clock
        self._progress = progress
        self._diagnostics = diagnostics
        self.created = 0
        self.reused = 0
        self.failed = 0

    def needs_repair(self, entry: RegistryEntry) -> bool:
        return (
            entry.tracked.conversion is ConversionState.CONVERTED
            and not has_registered_report(entry, self._output.files)
        )

    def convert(self, activity: RemoteActivity) -> None:
        identity = activity.identity
        registry = self._output.registry
        entry = registry.entry(identity)
        if entry.tracked.conversion is ConversionState.UNSUPPORTED:
            self.failed += 1
            self._progress(
                activity,
                "Unsupported FIT for automatic conversion. Use local conversion to try manually.",
            )
            return
        if has_registered_report(entry, self._output.files):
            self.reused += 1
            return

        assert entry.paths is not None
        try:
            if self._output.recover_report(entry):
                self.reused += 1
                return
            registry.record_conversion_attempt(
                identity, self._clock.now(), self._options
            )
            self._progress(activity, "Converting local FIT…")
            self._output.files.require_available(entry.paths.report)
            source = self._output.files.local_fit(entry.paths.fit)
            generated = self._generator.execute_detailed(source)
            self._output.publish_report(identity, generated.markdown, self._options)
        except (
            OutputConflict,
            UnsupportedActivityError,
            InvalidActivityError,
            OSError,
        ) as error:
            self.failed += 1
            if isinstance(error, OutputConflict):
                reason = FailureReason.CONFLICT
                message = f"{error}. Move the existing file before retrying."
            elif isinstance(error, UnsupportedActivityError):
                reason = FailureReason.UNSUPPORTED
                message = "Unsupported FIT for automatic conversion. Use local conversion to try manually."
            else:
                reason = FailureReason.CONVERSION_FAILED
                message = "Could not generate a report from the local FIT."
            registry.record_failure(identity, Stage.CONVERSION, reason)
            self._progress(activity, message)
            return
        except StorageFailure:
            self.failed += 1
            raise

        self.created += 1
        self._diagnostics(generated, source)
        self._progress(activity, "Report created.")
