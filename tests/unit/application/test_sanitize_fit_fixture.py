from datetime import UTC, datetime
from pathlib import Path

import pytest

from fit_to_md.application.use_cases.sanitize_fit_fixture import SanitizeFitFixture
from fit_to_md.domain.privacy import FitSanitizationPolicy, SanitizationSummary


class StubSanitizer:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, Path, datetime, FitSanitizationPolicy]] = []

    def sanitize(
        self,
        source: Path,
        destination: Path,
        target_start: datetime,
        policy: FitSanitizationPolicy,
    ) -> SanitizationSummary:
        self.calls.append((source, destination, target_start, policy))
        return SanitizationSummary(3, 4, 5)


def test_use_case_delegates_to_sanitizer_with_default_policy() -> None:
    sanitizer = StubSanitizer()
    use_case = SanitizeFitFixture(sanitizer)
    target = datetime(2020, 1, 1, tzinfo=UTC)

    result = use_case.execute(Path("private.fit"), Path("public.fit"), target)

    assert result == SanitizationSummary(3, 4, 5)
    assert sanitizer.calls == [
        (Path("private.fit"), Path("public.fit"), target, FitSanitizationPolicy())
    ]


def test_use_case_refuses_to_overwrite_the_private_source() -> None:
    with pytest.raises(ValueError, match="different path"):
        SanitizeFitFixture(StubSanitizer()).execute(
            Path("activity.fit"),
            Path("activity.fit"),
            datetime(2020, 1, 1, tzinfo=UTC),
        )
