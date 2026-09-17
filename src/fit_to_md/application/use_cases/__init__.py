"""Use cases for fit_to_md."""

from fit_to_md.application.use_cases.generate_markdown_report import (
    GeneratedMarkdownReport,
    GenerateMarkdownReport,
    ReportGenerationMetadata,
)
from fit_to_md.application.use_cases.generate_markdown_report_batch import (
    ActivityTimeUnavailableError,
    BatchOutputNaming,
    BatchReportOutcome,
    BatchReportStatus,
    GenerateMarkdownReportBatch,
)

__all__ = [
    "ActivityTimeUnavailableError",
    "BatchOutputNaming",
    "BatchReportOutcome",
    "BatchReportStatus",
    "GeneratedMarkdownReport",
    "GenerateMarkdownReport",
    "GenerateMarkdownReportBatch",
    "ReportGenerationMetadata",
]
