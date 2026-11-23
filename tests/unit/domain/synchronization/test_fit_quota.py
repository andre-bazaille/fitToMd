from datetime import UTC, datetime, timedelta

import pytest

from fit_to_md.domain.synchronization.quota import (
    FitQuotaExceeded,
    require_fit_capacity,
)

NOW = datetime(2026, 11, 21, tzinfo=UTC)


def test_capacity_is_available_before_the_limit() -> None:
    require_fit_capacity([NOW] * 49)


def test_capacity_is_released_when_enough_reservations_have_expired() -> None:
    later = NOW + timedelta(hours=1)
    with pytest.raises(FitQuotaExceeded) as error:
        require_fit_capacity([NOW] * 2 + [later] * 50)
    assert error.value.retry_at == later + timedelta(hours=24)
