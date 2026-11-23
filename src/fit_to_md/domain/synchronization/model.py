from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class InvalidInstant(ValueError):
    """An instant cannot be represented as an aware UTC datetime."""


def as_utc(instant: datetime) -> datetime:
    if instant.utcoffset() is None:
        raise InvalidInstant("An instant must include a timezone.")
    try:
        return instant.astimezone(UTC)
    except OverflowError as exc:
        raise InvalidInstant("The instant is outside the supported UTC range.") from exc


@dataclass(frozen=True, order=True)
class AccountIdentity:
    provider: str
    issuer: str
    subject: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.provider or not self.issuer or not self.subject:
            raise ValueError("An account requires a provider, issuer and subject.")


@dataclass(frozen=True, order=True)
class ActivityIdentity:
    account: AccountIdentity
    remote_id: str = field(repr=False)
    sport: int

    def __post_init__(self) -> None:
        if not self.remote_id:
            raise ValueError("An activity requires a remote identifier.")


@dataclass(frozen=True)
class RemoteActivity:
    identity: ActivityIdentity
    start_at: datetime | None

    def __post_init__(self) -> None:
        if self.start_at is not None:
            object.__setattr__(self, "start_at", as_utc(self.start_at))


@dataclass(frozen=True)
class Reference:
    instant: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "instant", as_utc(self.instant))


class AcquisitionState(StrEnum):
    PENDING = "pending"
    ACQUIRED = "acquired"
    FAILED = "failed"


class ConversionState(StrEnum):
    PENDING = "pending"
    CONVERTED = "converted"
    FAILED = "failed"
    UNSUPPORTED = "conversion_unsupported"


@dataclass(frozen=True)
class TrackedActivity:
    activity: RemoteActivity
    acquisition: AcquisitionState = AcquisitionState.PENDING
    conversion: ConversionState = ConversionState.PENDING
