"""Authorize, resume and probe a COROS session through application ports."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import replace
from datetime import date
from typing import Protocol

from fit_to_md.domain.coros_connection import (
    ISSUERS,
    ActivityProbeResult,
    ConnectionFailure,
    ConnectionRecord,
    ConnectionStatus,
    CorosAccount,
    IdentityProbeResult,
    LocalSessionState,
    ProbeResult,
    ReauthorizationRequired,
    Session,
    Verification,
)


class SessionStore(Protocol):
    def load(self) -> Session | None: ...
    def save(self, session: Session) -> None: ...
    def clear(self) -> None: ...


class ConnectionStore(Protocol):
    def load(self) -> ConnectionRecord | None: ...
    def save(self, record: ConnectionRecord) -> None: ...
    def clear(self) -> None: ...


class ConnectionAccess(Protocol):
    def authorize(self, open_browser: Callable[[str], None]) -> Session: ...
    def refresh(self, session: Session) -> Session: ...
    def confirm_account(self, session: Session) -> CorosAccount: ...


class ConnectionLock(Protocol):
    def hold(self) -> AbstractContextManager[None]: ...


_RECONNECT = "Run 'fit-to-md coros login' to authorize COROS."
_SWITCH_ACCOUNT = "Run 'fit-to-md coros logout' before changing COROS accounts."


class CorosSessionManager:
    """Manage the single active production session."""

    def __init__(
        self,
        access_for_region: Callable[[str], ConnectionAccess],
        store: ConnectionStore,
        lock: ConnectionLock,
        now: Callable[[], float],
    ) -> None:
        self._access_for_region = access_for_region
        self._store = store
        self._lock = lock
        self._now = now

    def login(
        self, open_browser: Callable[[str], None], *, region: str | None = None
    ) -> CorosAccount:
        with self._lock.hold():
            current = self._store.load()
            selected = region or (current.account.region if current else "eu")
            if selected not in ISSUERS:
                raise ConnectionFailure("Unsupported COROS region.")
            if current is not None and current.account.region != selected:
                raise ConnectionFailure(_SWITCH_ACCOUNT)

            access = self._access_for_region(selected)
            try:
                session = access.authorize(open_browser)
                account = access.confirm_account(session)
            except ReauthorizationRequired as error:
                raise ReauthorizationRequired(_RECONNECT) from error
            if account.region != selected:
                raise ConnectionFailure("COROS returned an unexpected account region.")
            if current is not None and current.account != account:
                raise ConnectionFailure(_SWITCH_ACCOUNT)

            self._store.save(ConnectionRecord(account, session))
            return account

    def status(self) -> ConnectionStatus:
        return self.inspect_local()[0]

    def inspect_local(self) -> tuple[ConnectionStatus, CorosAccount | None]:
        """Read the status and recorded account from the same local session."""
        current = self._store.load()
        if current is None:
            return ConnectionStatus(LocalSessionState.DISCONNECTED), None

        if current.verification is Verification.RECONNECT:
            state = LocalSessionState.RECONNECT
        elif current.verification is Verification.PENDING:
            state = LocalSessionState.VERIFICATION_PENDING
        elif current.session.needs_refresh(self._now()):
            state = LocalSessionState.RENEWAL_NEEDED
        else:
            state = LocalSessionState.READY
        return ConnectionStatus(state, current.account.region), current.account

    def resume(self) -> tuple[CorosAccount, Session]:
        """Return the confirmed account and usable credentials without interaction."""
        with self._lock.hold():
            current = self._store.load()
            if current is None or current.verification is Verification.RECONNECT:
                raise ReauthorizationRequired(_RECONNECT)

            needs_refresh = current.session.needs_refresh(self._now())
            if not needs_refresh and current.verification is Verification.CONFIRMED:
                return current.account, current.session

            access = self._access_for_region(current.account.region)
            try:
                if needs_refresh:
                    session = access.refresh(current.session)
                    current = replace(
                        current, session=session, verification=Verification.PENDING
                    )
                    self._store.save(current)

                account = access.confirm_account(current.session)
            except ReauthorizationRequired as error:
                self._store.save(replace(current, verification=Verification.RECONNECT))
                raise ReauthorizationRequired(_RECONNECT) from error

            if account != current.account:
                self._store.save(replace(current, verification=Verification.RECONNECT))
                raise ReauthorizationRequired(
                    f"COROS reported a different account after renewal. {_RECONNECT}"
                )

            self._store.save(replace(current, verification=Verification.CONFIRMED))
            return account, current.session

    def saved_account(self) -> CorosAccount:
        """Get the locally recorded account without network access or renewal."""
        current = self._store.load()
        if current is None or current.verification is Verification.RECONNECT:
            raise ReauthorizationRequired(_RECONNECT)
        return current.account

    def logout(self) -> None:
        with self._lock.hold():
            self._store.clear()


class CorosAccess(Protocol):
    def probe_identity(self, session: Session) -> IdentityProbeResult: ...
    def authorize(self, open_browser: Callable[[str], None]) -> Session: ...
    def refresh(self, session: Session) -> Session: ...
    def discover_tools(self, session: Session) -> tuple[str, frozenset[str]]: ...
    def probe_activities(
        self,
        session: Session,
        day: date,
        inspect: Callable[[dict[str, object]], None] | None = None,
        *,
        through: date | None = None,
        limit: int = 20,
    ) -> ActivityProbeResult: ...
    def download_sample(self, session: Session, day: date) -> bytes: ...


class CorosConnection:
    def __init__(
        self, access: CorosAccess, store: SessionStore, now: Callable[[], float]
    ) -> None:
        self._access = access
        self._store = store
        self._now = now

    def login(self, open_browser: Callable[[str], None]) -> None:
        self._store.load()  # Verify the vault before starting browser authorization.
        self._store.save(self._access.authorize(open_browser))

    def _session(self, force_refresh: bool = False) -> tuple[Session, bool]:
        session = self._store.load()
        if session is None:
            raise ConnectionFailure("No COROS session. Run login first.")

        refreshed = force_refresh or session.needs_refresh(self._now())
        if refreshed:
            session = self._access.refresh(session)
            self._store.save(
                session
            )  # Persist rotation before making further requests.

        return session, refreshed

    def probe_activities(
        self,
        day: date,
        inspect: Callable[[dict[str, object]], None] | None = None,
        *,
        through: date | None = None,
        limit: int = 20,
    ) -> ActivityProbeResult:
        session, _ = self._session()
        return self._access.probe_activities(
            session, day, inspect, through=through, limit=limit
        )

    def probe(self, *, force_refresh: bool = False) -> ProbeResult:
        session, refreshed = self._session(force_refresh)
        version, tools = self._access.discover_tools(session)
        return ProbeResult(version, tools, refreshed)

    def probe_identity(self) -> IdentityProbeResult:
        session, _ = self._session()
        return self._access.probe_identity(session)

    def download_sample(self, day: date) -> bytes:
        session, _ = self._session()
        return self._access.download_sample(session, day)

    def logout(self) -> None:
        self._store.clear()
