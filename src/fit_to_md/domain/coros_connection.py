"""Session state for the isolated COROS connection context."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

ISSUERS = {
    "eu": "https://mcpeu.coros.com",
    "us": "https://mcpus.coros.com",
    "cn": "https://mcpcn.coros.com",
}


class ConnectionFailure(Exception):
    """An operational failure with a user-safe message."""


class ReauthorizationRequired(ConnectionFailure):
    """COROS requires the user to authorize access again."""


@dataclass(frozen=True)
class Session:
    client_id: str
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: float

    def needs_refresh(self, now: float) -> bool:
        return self.expires_at <= now + 60


@dataclass(frozen=True)
class CorosAccount:
    region: str
    issuer: str
    subject: str = field(repr=False)

    def __post_init__(self) -> None:
        if ISSUERS.get(self.region) != self.issuer or not self.subject.strip():
            raise ValueError("Invalid COROS account identity.")


class Verification(StrEnum):
    CONFIRMED = "confirmed"
    PENDING = "verifying"
    RECONNECT = "reconnect"


@dataclass(frozen=True)
class ConnectionRecord:
    account: CorosAccount
    session: Session = field(repr=False)
    verification: Verification = Verification.CONFIRMED


class LocalSessionState(StrEnum):
    DISCONNECTED = "disconnected"
    READY = "ready"
    RENEWAL_NEEDED = "renewal_needed"
    VERIFICATION_PENDING = "verification_pending"
    RECONNECT = "reconnect"


@dataclass(frozen=True)
class ConnectionStatus:
    state: LocalSessionState
    region: str | None = None


@dataclass(frozen=True)
class ProbeResult:
    protocol_version: str
    tools: frozenset[str]
    refreshed: bool


@dataclass(frozen=True)
class RemoteActivityRef:
    label_id: str
    sport_type: int


@dataclass(frozen=True)
class IdentityProbeResult:
    fingerprint: str


@dataclass(frozen=True)
class ActivityDateObservation:
    fingerprint: str
    start: datetime | None
    end: datetime | None
    problem: str | None = None


@dataclass(frozen=True)
class ActivityProbeResult:
    count: int
    count_kind: str
    returned_period: tuple[str, str] | None = None
    references: tuple[RemoteActivityRef, ...] = field(default=(), repr=False)
    dates: tuple[ActivityDateObservation, ...] = ()
