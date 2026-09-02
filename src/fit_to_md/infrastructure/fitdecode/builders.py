"""Compatibility exports for reporting services moved to the domain layer.

New code should import these classes from ``fit_to_md.domain.reporting``.
"""

from fit_to_md.domain.reporting import (
    SessionSummaryBuilder,
    SplitBuilder,
    TransitionBuilder,
)

__all__ = ["SessionSummaryBuilder", "SplitBuilder", "TransitionBuilder"]
