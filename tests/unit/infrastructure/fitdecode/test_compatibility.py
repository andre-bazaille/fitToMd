from fit_to_md.domain.activity import Activity, ActivityLap, ActivityRecord
from fit_to_md.domain.reporting import (
    SessionSummaryBuilder,
    SplitBuilder,
    TransitionBuilder,
)
from fit_to_md.infrastructure.fitdecode.builders import (
    SessionSummaryBuilder as LegacySessionSummaryBuilder,
)
from fit_to_md.infrastructure.fitdecode.builders import (
    SplitBuilder as LegacySplitBuilder,
)
from fit_to_md.infrastructure.fitdecode.builders import (
    TransitionBuilder as LegacyTransitionBuilder,
)
from fit_to_md.infrastructure.fitdecode.models import (
    ParsedActivityData,
    ParsedLap,
    ParsedRecord,
)


def test_legacy_fitdecode_imports_reexport_domain_types() -> None:
    assert LegacySessionSummaryBuilder is SessionSummaryBuilder
    assert LegacySplitBuilder is SplitBuilder
    assert LegacyTransitionBuilder is TransitionBuilder
    assert ParsedActivityData is Activity
    assert ParsedLap is ActivityLap
    assert ParsedRecord is ActivityRecord
