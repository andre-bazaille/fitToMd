"""Ports and orchestration for account quota and guarded FIT requests."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from fit_to_md.application.synchronization_storage import (
    OutputFiles,
    RegistryReader,
    StorageFailure,
    has_verified_fit,
)
from fit_to_md.domain.synchronization.model import AccountIdentity, ActivityIdentity
from fit_to_md.domain.synchronization.quota import DailyCalls, ServerFitQuotaExceeded


class QuotaUnavailable(StorageFailure):
    """The shared quota state cannot be used safely."""


@dataclass(frozen=True)
class QuotaBinding:
    account: AccountIdentity
    path: Path
    registry_id: str
    active: bool


class QuotaBindings(Protocol):
    def load(self, account: AccountIdentity) -> QuotaBinding | None: ...

    def save(self, binding: QuotaBinding) -> None: ...


class QuotaRegistry(Protocol):
    @property
    def account(self) -> AccountIdentity: ...

    def reserve_fit(self) -> None: ...

    def record_catalogue_call(self) -> None: ...

    def daily_calls(self, day: date, zone: ZoneInfo) -> DailyCalls: ...


class QuotaStorage(Protocol):
    def initial_path(self, account: AccountIdentity) -> Path: ...

    def exists(self, path: Path) -> bool: ...

    def create(self, binding: QuotaBinding) -> None: ...

    def open(self, binding: QuotaBinding) -> QuotaRegistry: ...


_RESTORE = "Restore a reliable COROS quota registry and vault binding before acquiring FIT files."


class AccountQuotas:
    def __init__(
        self,
        bindings: QuotaBindings,
        storage: QuotaStorage,
        hold_initialization_lock: Callable[[], AbstractContextManager[None]],
    ) -> None:
        self._bindings = bindings
        self._storage = storage
        self._hold_initialization_lock = hold_initialization_lock

    def open(self, account: AccountIdentity) -> QuotaRegistry:
        binding = self._bindings.load(account)
        if binding is None:
            raise QuotaUnavailable(
                "No COROS quota binding was found. For a first-time setup, run "
                "'fit-to-md coros init-quota'. For a previously initialized account, "
                "restore the existing quota state."
            )
        return self._open_bound(account, binding)

    def initialize(self, account: AccountIdentity) -> None:
        with self._hold_initialization_lock():
            current = self._bindings.load(account)
            if current is not None:
                verified_binding = replace(current, active=True)
                self._open_bound(account, verified_binding)
                if not current.active:
                    self._publish_binding(verified_binding)
                return

            path = self._storage.initial_path(account)
            if self._storage.exists(path):
                raise QuotaUnavailable(
                    f"An unbound COROS quota registry exists. {_RESTORE}"
                )

            pending = QuotaBinding(account, path, uuid4().hex, active=False)
            self._publish_binding(pending)
            self._storage.create(pending)
            active = replace(pending, active=True)
            self._open_bound(account, active)
            self._publish_binding(active)

    def _publish_binding(self, binding: QuotaBinding) -> None:
        self._bindings.save(binding)
        if self._bindings.load(binding.account) != binding:
            raise QuotaUnavailable(
                f"Could not verify the COROS quota binding. {_RESTORE}"
            )

    def _open_bound(
        self, account: AccountIdentity, binding: QuotaBinding
    ) -> QuotaRegistry:
        if binding.account != account or not binding.active:
            raise QuotaUnavailable(
                f"The COROS quota binding is incomplete or invalid. {_RESTORE}"
            )
        return self._storage.open(binding)


class BudgetedFitRequests:
    """Guard FIT requests under the output lock for one synchronization run."""

    def __init__(
        self,
        quota: QuotaRegistry,
        registry: RegistryReader,
        files: OutputFiles,
        request_fit: Callable[[ActivityIdentity], bytes],
    ) -> None:
        self._quota = quota
        self._registry = registry
        self._files = files
        self._request_fit = request_fit
        self._server_refusal: ServerFitQuotaExceeded | None = None

    def request(
        self,
        identity: ActivityIdentity,
        *,
        before_request: Callable[[], None] | None = None,
    ) -> bytes | None:
        """Fetch once, or return None when the tracked local FIT is reusable."""
        if identity.account != self._quota.account:
            raise QuotaUnavailable(
                "The activity and COROS FIT budget belong to different accounts."
            )
        entry = self._registry.entry(identity)
        if has_verified_fit(entry, self._files):
            return None
        if self._server_refusal is not None:
            raise ServerFitQuotaExceeded(self._server_refusal.retry_at)

        self._quota.reserve_fit()
        if before_request is not None:
            before_request()
        try:
            return self._request_fit(identity)
        except ServerFitQuotaExceeded as error:
            self._server_refusal = error
            raise
