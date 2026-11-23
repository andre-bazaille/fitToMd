"""Adapter for COROS's published browser-assisted CLI flow and MCP tools."""

import base64
import binascii
import hashlib
import json
import math
import re
import secrets
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from typing import Protocol, TypeVar, cast
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from fit_to_md.application.synchronization import to_account_identity
from fit_to_md.application.synchronization_acquisition import FitRequest
from fit_to_md.application.synchronization_discovery import (
    CATALOGUE_LIMIT,
    CatalogueDates,
    CatalogueFailure,
    CatalogueFailureKind,
    CataloguePage,
    CatalogueReader,
)
from fit_to_md.application.synchronization_storage import InvalidFit
from fit_to_md.domain.coros_connection import (
    ISSUERS,
    ActivityDateObservation,
    ActivityProbeResult,
    ConnectionFailure,
    CorosAccount,
    IdentityProbeResult,
    ReauthorizationRequired,
    RemoteActivityRef,
    Session,
)
from fit_to_md.domain.synchronization.model import (
    AccountIdentity,
    ActivityIdentity,
    RemoteActivity,
)
from fit_to_md.domain.synchronization.quota import ServerFitQuotaExceeded
from fit_to_md.infrastructure.fitdecode.frames import check_fit_frames

_CLIENT_NAME = "FitToMd COROS connection prototype"
_SCOPE = "openid offline_access mcp.tools"
_REDIRECT = "http://127.0.0.1:43123/callback"
_VERSION = "2025-06-18"
_RESPONSE_LIMIT = 2_000_000
_FIT_PAYLOAD_BYTES = 32 * 1024 * 1024
_FIT_RESPONSE_LIMIT = 4 * ((_FIT_PAYLOAD_BYTES + 2) // 3) + 1024 * 1024
_RECORD_HEADER = re.compile(
    r"^Sport Records [—–-] (\d{4}-\d{2}-\d{2}) to "
    r"(\d{4}-\d{2}-\d{2}) \((\d{1,6}) records?\)\s*$",
    re.MULTILINE,
)
_RECORD_ID = re.compile(
    r"^[ \t]*LabelId: ([0-9]{1,32})[ \t]*\|[ \t]*SportType: ([0-9]{1,8})[ \t]*$",
    re.MULTILINE,
)
_RECORD_BLOCK = re.compile(r"^\d+\. ", re.MULTILINE)
_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_Result = TypeVar("_Result")


class _NetworkFailure(ConnectionFailure):
    pass


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str]
    body: bytes


class Transport(Protocol):
    def send(
        self, request: Request, *, max_response_bytes: int = _RESPONSE_LIMIT
    ) -> Response: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        return None


class HttpsTransport:
    def send(
        self, request: Request, *, max_response_bytes: int = _RESPONSE_LIMIT
    ) -> Response:
        try:
            try:
                stream = build_opener(_NoRedirect()).open(request, timeout=10)
            except HTTPError as error:
                stream = error
            with stream:
                status = stream.getcode()
                headers = dict(stream.headers.items())
                if status == 429 or (
                    status != 200 and urlsplit(request.full_url).path == "/mcp"
                ):
                    # MCP HTTP errors need only the status and headers.
                    # A broken error body must not hide authentication failures.
                    return Response(status, headers, b"")
                body = stream.read(max_response_bytes + 1)
                if len(body) > max_response_bytes:
                    raise ConnectionFailure("COROS response exceeds the size limit.")
                return Response(status, headers, body)
        except (URLError, OSError, HTTPException):
            raise _NetworkFailure("Could not reach COROS over HTTPS.") from None
        except ValueError:
            raise ConnectionFailure("Could not reach COROS over HTTPS.") from None


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ConnectionFailure("Invalid object in COROS response.")
    return cast(dict[str, object], value)


def _fingerprint(*parts: str) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=True).encode()).hexdigest()


def _date_observations(text: str, issuer: str) -> tuple[ActivityDateObservation, ...]:
    observations: list[ActivityDateObservation] = []
    # Never zip independently extracted references and timestamps: a missing field
    # in one activity must not shift the association for all subsequent activities.
    for block in _RECORD_BLOCK.split(text)[1:]:
        refs = _RECORD_ID.findall(block)
        if len(refs) != 1:
            continue
        start, end = _record_times(block)
        problem = None
        if start is None or end is None:
            problem = "Missing or invalid Unix-second timestamp."
        elif end < start:
            problem = "End timestamp precedes start timestamp."
        observations.append(
            ActivityDateObservation(_fingerprint(issuer, *refs[0]), start, end, problem)
        )
    return tuple(observations)


def _record_times(block: str) -> tuple[datetime | None, datetime | None]:
    def read(key: str) -> datetime | None:
        values = re.findall(rf"\b{key}=([^\s|]*)", block)
        if len(values) == 1 and re.fullmatch(r"-?[0-9]{1,12}", values[0]):
            try:
                return _UNIX_EPOCH + timedelta(seconds=int(values[0]))
            except OverflowError:
                pass
        return None

    return read("startTimestamp"), read("endTimestamp")


def _tool_texts(result: Mapping[str, object]) -> Iterator[str]:
    content = result.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        item = _object(block)
        value = item.get("text")
        if item.get("type") != "text" or not isinstance(value, str):
            continue
        if value.lstrip().startswith('"'):
            try:
                unwrapped = json.loads(value)
            except (ValueError, RecursionError):
                continue
            if isinstance(unwrapped, str):
                value = unwrapped
        yield value


def _parse_catalogue(
    result: Mapping[str, object], account: AccountIdentity
) -> CataloguePage:
    if result.get("isError") is True:
        raise CatalogueFailure(CatalogueFailureKind.TOOL)
    if "isError" in result and result["isError"] is not False:
        raise CatalogueFailure(CatalogueFailureKind.RESPONSE)
    try:
        summaries = [
            (value, header)
            for value in _tool_texts(result)
            for header in _RECORD_HEADER.finditer(value)
        ]
    except ConnectionFailure:
        raise CatalogueFailure(CatalogueFailureKind.RESPONSE) from None
    if not summaries:
        structured = result.get("structuredContent")
        if isinstance(structured, dict) and any(
            structured.get(key) == [] for key in ("records", "activities")
        ):
            # This empty shape is recognized by the probe but provides no bounds.
            return CataloguePage(None, ())
    if len(summaries) != 1:
        raise CatalogueFailure(CatalogueFailureKind.RESPONSE)

    value, header = summaries[0]
    start_date, end_date, count = header.groups()
    try:
        dates = CatalogueDates(
            date.fromisoformat(start_date), date.fromisoformat(end_date)
        )
    except ValueError:
        raise CatalogueFailure(CatalogueFailureKind.BOUNDS) from None
    blocks = _RECORD_BLOCK.split(value[header.end() :])[1:]
    if len(blocks) != int(count) or len(_RECORD_ID.findall(value)) != len(blocks):
        raise CatalogueFailure(CatalogueFailureKind.RESPONSE)
    rows = []
    for block in blocks:
        refs = _RECORD_ID.findall(block)
        if len(refs) != 1:
            raise CatalogueFailure(CatalogueFailureKind.RESPONSE)
        label, sport = refs[0]
        start, end = _record_times(block)
        if start is None or end is None or end < start:
            start = None
        rows.append(RemoteActivity(ActivityIdentity(account, label, int(sport)), start))
    return CataloguePage(dates, tuple(rows))


def _text(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConnectionFailure(f"Missing or invalid COROS field: {key}.")
    return value


def _header(response: Response, name: str) -> str:
    return next((v for k, v in response.headers.items() if k.lower() == name), "")


def _retry_at(value: str, received_at: float) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    try:
        now = datetime.fromtimestamp(received_at, UTC)
        if value.isascii() and value.isdecimal():
            return now + timedelta(seconds=int(value))
        when = parsedate_to_datetime(value)
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        return max(when.astimezone(UTC), now)
    except (ValueError, OverflowError, OSError):
        return None


def _is_header_token(value: str) -> bool:
    return all(33 <= ord(character) <= 126 for character in value)


def _decoded(response: Response, request_id: int | None = None) -> dict[str, object]:
    try:
        content = response.body.decode()
        if "text/event-stream" in _header(response, "content-type").lower():
            events: list[str] = []
            for line in [*content.splitlines(), ""]:
                if line.startswith("data:"):
                    events.append(line[5:].removeprefix(" "))
                elif not line and events:
                    data = _object(json.loads("\n".join(events)))
                    if data.get("id") == request_id:
                        return data
                    events.clear()
            raise ConnectionFailure(
                "COROS stream did not contain the requested MCP response."
            )
        return _object(json.loads(content))
    except (UnicodeError, ValueError, RecursionError):
        raise ConnectionFailure("COROS returned an unreadable response.") from None


class CorosGateway:
    def __init__(
        self,
        region: str,
        transport: Transport,
        *,
        now: Callable[[], float],
        monotonic: Callable[[], float],
        sleep: Callable[[float], None],
        client_name: str = _CLIENT_NAME,
    ) -> None:
        self._region = region
        self._issuer = ISSUERS[region]
        self._transport = transport
        self._now = now
        self._monotonic = monotonic
        self._sleep = sleep
        self._client_name = client_name

    def probe_identity(self, session: Session) -> IdentityProbeResult:
        account = self.confirm_account(session)
        return IdentityProbeResult(_fingerprint(account.issuer, account.subject))

    def confirm_account(self, session: Session) -> CorosAccount:
        discovery = self._request("/.well-known/openid-configuration")
        if discovery.status != 200:
            raise ConnectionFailure("COROS identity discovery failed.")
        metadata = _decoded(discovery)
        endpoint = _text(metadata, "userinfo_endpoint")
        try:
            parsed = urlsplit(endpoint)
        except ValueError as error:
            raise ConnectionFailure("Untrusted COROS identity endpoint.") from error
        if (
            metadata.get("issuer") != self._issuer
            or parsed.scheme != "https"
            or parsed.netloc != urlsplit(self._issuer).netloc
            or not parsed.path.startswith("/")
            or endpoint != self._issuer + parsed.path
        ):
            raise ConnectionFailure("Untrusted COROS identity endpoint.")
        response = self._request(
            parsed.path, headers={"Authorization": "Bearer " + session.access_token}
        )
        if response.status in (401, 403):
            raise ReauthorizationRequired("COROS refused the identity request.")
        if response.status != 200:
            raise ConnectionFailure("COROS identity request failed.")
        subject = _text(_decoded(response), "sub")
        return CorosAccount(self._region, self._issuer, subject)

    def _request(
        self,
        path: str,
        *,
        payload: dict[str, object] | None = None,
        form: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        post: bool = False,
        max_response_bytes: int = _RESPONSE_LIMIT,
    ) -> Response:
        request_headers = dict(headers or {})
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
            request_headers["Content-Type"] = "application/json"
        elif form is not None:
            try:
                data = urlencode(form).encode()
            except UnicodeError:
                raise ConnectionFailure(
                    "Invalid COROS OAuth request parameters."
                ) from None
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = Request(
            self._issuer + path,
            data=data,
            headers=request_headers,
            method="POST" if post or data is not None else "GET",
        )
        return self._transport.send(request, max_response_bytes=max_response_bytes)

    def authorize(self, open_browser: Callable[[str], None]) -> Session:
        registration = self._request(
            "/connect/register",
            payload={
                "client_name": self._client_name,
                "redirect_uris": [_REDIRECT],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "scope": _SCOPE,
                "token_endpoint_auth_method": "none",
            },
        )
        if registration.status not in (200, 201):
            raise ConnectionFailure(
                f"COROS client registration failed (HTTP {registration.status})."
            )
        client_id = _text(_decoded(registration), "client_id")
        response = self._request(
            "/api/v1/cli/login-sessions", payload={"clientId": client_id}
        )
        if response.status not in (200, 201):
            raise ConnectionFailure(
                f"COROS could not start browser login (HTTP {response.status})."
            )
        browser_session = _decoded(response)
        login_url = _text(browser_session, "loginUrl")
        try:
            target = urlsplit(login_url)
            host = target.hostname or ""
            if (
                target.scheme != "https"
                or (host != "coros.com" and not host.endswith(".coros.com"))
                or target.username is not None
                or target.port not in (None, 443)
            ):
                raise ValueError
        except ValueError as error:
            raise ConnectionFailure(
                "COROS returned an invalid browser destination."
            ) from error

        try:
            session_id = quote(_text(browser_session, "sessionId"), safe="")
        except UnicodeError:
            raise ConnectionFailure(
                "COROS returned an invalid browser session."
            ) from None
        poll_token = _text(browser_session, "pollToken")
        if not _is_header_token(poll_token):
            raise ConnectionFailure("COROS returned an invalid login polling token.")
        interval = browser_session.get("intervalSeconds", 3)
        if type(interval) is not int or not 1 <= interval <= 300:
            raise ConnectionFailure("Invalid COROS login polling interval.")
        verifier = secrets.token_urlsafe(32)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        state = secrets.token_urlsafe(24)

        open_browser(login_url)
        ticket = self._claim(session_id, poll_token, interval)
        try:
            query = urlencode(
                {
                    "client_id": client_id,
                    "redirect_uri": _REDIRECT,
                    "response_type": "code",
                    "scope": _SCOPE,
                    "resource": self._issuer + "/mcp",
                    "code_challenge": challenge.decode().rstrip("="),
                    "code_challenge_method": "S256",
                    "state": state,
                    "login_ticket": ticket,
                }
            )
        except UnicodeError:
            raise ConnectionFailure("Invalid COROS OAuth request parameters.") from None
        auth = self._request("/oauth2/authorize?" + query)
        try:
            callback = (
                urlsplit(_header(auth, "location"))
                if auth.status in (302, 303)
                else None
            )
        except ValueError as error:
            raise ConnectionFailure(
                "COROS returned an invalid OAuth callback."
            ) from error
        if (
            callback is None
            or callback._replace(query="", fragment="").geturl() != _REDIRECT
        ):
            raise ConnectionFailure("COROS did not return the expected OAuth callback.")
        values = parse_qs(callback.query)
        codes = values.get("code", [])
        if values.get("state") != [state] or len(codes) != 1:
            raise ConnectionFailure(
                "Invalid COROS OAuth callback state or authorization code."
            )
        return self._tokens(
            client_id,
            {
                "grant_type": "authorization_code",
                "redirect_uri": _REDIRECT,
                "code": codes[0],
                "code_verifier": verifier,
            },
        )

    def _claim(self, session_id: str, poll_token: str, interval: int) -> str:
        deadline = self._monotonic() + 300
        while self._monotonic() < deadline:
            response = self._request(
                f"/api/v1/cli/login-sessions/{session_id}/claim",
                headers={"X-Poll-Token": poll_token},
                post=True,
            )
            if response.status != 200:
                raise ConnectionFailure(
                    f"COROS login claim failed (HTTP {response.status})."
                )
            data = _decoded(response)
            match data.get("status"):
                case "authorized":
                    return _text(data, "loginTicket")
                case "pending":
                    remaining = deadline - self._monotonic()
                    if remaining > 0:
                        self._sleep(min(interval, remaining))
                case "failed" | "expired" | "claimed":
                    raise ConnectionFailure(
                        "COROS browser login failed or expired; run login again."
                    )
                case _:
                    raise ConnectionFailure(
                        "COROS returned an unexpected login status."
                    )
        raise ConnectionFailure("COROS browser login timed out.")

    def _tokens(self, client_id: str, form: dict[str, str]) -> Session:
        response = self._request("/oauth2/token", form={**form, "client_id": client_id})
        if response.status != 200:
            if form["grant_type"] == "refresh_token" and response.status in (
                400,
                401,
                403,
            ):
                if response.status in (401, 403) or self._refresh_requires_login(
                    response
                ):
                    raise ReauthorizationRequired(
                        "COROS rejected the refresh; run login again."
                    )
                raise ConnectionFailure("COROS rejected the refresh (HTTP 400).")
            raise ConnectionFailure(
                f"COROS token request failed (HTTP {response.status})."
            )
        data = _decoded(response)
        lifetime = data.get("expires_in")
        if (
            not isinstance(lifetime, (int, float))
            or isinstance(lifetime, bool)
            or not 0 < lifetime <= 365 * 86400
        ):
            raise ConnectionFailure("Invalid COROS token lifetime.")
        access = _text(data, "access_token")
        if _text(data, "token_type").lower() != "bearer" or not _is_header_token(
            access
        ):
            raise ConnectionFailure("Invalid COROS bearer token.")
        expiry = self._now() + lifetime
        if not math.isfinite(expiry):
            raise ConnectionFailure("Invalid COROS expiration time.")
        return Session(client_id, access, _text(data, "refresh_token"), expiry)

    @staticmethod
    def _refresh_requires_login(response: Response) -> bool:
        try:
            code = _decoded(response).get("error")
        except ConnectionFailure:
            return False
        return code in ("invalid_grant", "invalid_client", "unauthorized_client")

    def refresh(self, session: Session) -> Session:
        return self._tokens(
            session.client_id,
            {"grant_type": "refresh_token", "refresh_token": session.refresh_token},
        )

    def _rpc(
        self,
        session: Session,
        method: str,
        request_id: int,
        params: dict[str, object],
        version: str | None = None,
        *,
        fit_request: bool = False,
        production_fit: bool = False,
        catalogue: bool = False,
    ) -> dict[str, object]:
        headers = {
            "Authorization": "Bearer " + session.access_token,
            "Accept": "application/json, text/event-stream",
        }
        if version is not None:
            headers["MCP-Protocol-Version"] = version
        try:
            response = self._request(
                "/mcp",
                payload={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params,
                },
                headers=headers,
                max_response_bytes=_FIT_RESPONSE_LIMIT
                if fit_request
                else _RESPONSE_LIMIT,
            )
        except _NetworkFailure:
            if catalogue:
                raise CatalogueFailure(CatalogueFailureKind.NETWORK) from None
            raise
        except ConnectionFailure:
            if catalogue:
                raise CatalogueFailure(CatalogueFailureKind.RESPONSE) from None
            raise
        if catalogue:
            return self._decode_catalogue_rpc(response, request_id)
        if production_fit and response.status in (401, 403):
            raise ReauthorizationRequired(CatalogueFailureKind.AUTHENTICATION.value)
        if response.status == 401:
            raise ConnectionFailure(
                "COROS refused the session; try probe --refresh or login."
            )
        if response.status == 429 and fit_request:
            raise ServerFitQuotaExceeded(
                _retry_at(_header(response, "retry-after"), self._now())
            )
        if response.status != 200:
            raise ConnectionFailure(
                f"COROS MCP {method} failed (HTTP {response.status})."
            )
        result = _decoded(response, request_id)
        if (
            result.get("id") != request_id
            or "error" in result
            or production_fit
            and (result.get("jsonrpc") != "2.0" or type(result.get("id")) is not int)
        ):
            raise ConnectionFailure(
                f"COROS MCP {method} returned an error or an invalid response."
            )
        return _object(result.get("result"))

    def _decode_catalogue_rpc(
        self, response: Response, request_id: int
    ) -> dict[str, object]:
        if response.status in (401, 403):
            raise CatalogueFailure(CatalogueFailureKind.AUTHENTICATION)
        if response.status != 200:
            raise CatalogueFailure(
                CatalogueFailureKind.HTTP,
                status=response.status,
                retry_at=_retry_at(_header(response, "retry-after"), self._now()),
            )
        try:
            envelope = _decoded(response, request_id)
            if (
                envelope.get("jsonrpc") != "2.0"
                or type(envelope.get("id")) is not int
                or envelope["id"] != request_id
            ):
                raise ConnectionFailure("Invalid MCP response identifier.")
            if "error" in envelope:
                raise CatalogueFailure(CatalogueFailureKind.RPC)
            return _object(envelope.get("result"))
        except ConnectionFailure:
            raise CatalogueFailure(CatalogueFailureKind.RESPONSE) from None

    def _initialize(self, session: Session, *, catalogue: bool = False) -> str:
        init = self._rpc(
            session,
            "initialize",
            1,
            {
                "protocolVersion": _VERSION,
                "clientInfo": {"name": self._client_name, "version": "0.1"},
                "capabilities": {},
            },
            catalogue=catalogue,
        )
        if init.get("protocolVersion") != _VERSION:
            if catalogue:
                raise CatalogueFailure(CatalogueFailureKind.RESPONSE)
            _text(init, "protocolVersion")
            raise ConnectionFailure(
                "COROS proposed an MCP version unsupported by this prototype."
            )
        return _VERSION

    def _retry_catalogue(self, operation: Callable[[], _Result]) -> _Result:
        pauses = iter((1, 2))
        while True:
            try:
                return operation()
            except CatalogueFailure as error:
                retryable = error.kind is CatalogueFailureKind.NETWORK or (
                    error.status is not None
                    and (error.status in (408, 429) or 500 <= error.status < 600)
                )
                if not retryable:
                    raise
                pause = next(pauses, 0)
                if error.retry_at is not None:
                    delay = error.retry_at - (
                        _UNIX_EPOCH + timedelta(seconds=self._now())
                    )
                    if delay > timedelta(seconds=pause):
                        error.defer_remaining = True
                        raise
                if not pause:
                    raise
                self._sleep(pause)

    def prepare_catalogue(
        self, account: CorosAccount, session: Session
    ) -> CatalogueReader:
        """Negotiate once and construct a catalogue reader for the confirmed account."""
        if account.issuer != self._issuer or account.region != self._region:
            raise CatalogueFailure(CatalogueFailureKind.AUTHENTICATION)
        identity = to_account_identity(account)
        version = self._retry_catalogue(
            lambda: self._initialize(session, catalogue=True)
        )
        return self._catalogue_reader(identity, session, lambda: version)

    def prepare_acquisition(
        self,
        account: CorosAccount,
        session: Session,
        record_catalogue_call: Callable[[], None],
    ) -> tuple[CatalogueReader, FitRequest]:
        """Share one MCP negotiation across the catalogue and FIT requests."""
        if account.issuer != self._issuer or account.region != self._region:
            raise ReauthorizationRequired(CatalogueFailureKind.AUTHENTICATION.value)
        version: str | None = None
        discovery_failure: CatalogueFailure | None = None
        try:
            version = self._retry_catalogue(
                lambda: self._initialize(session, catalogue=True)
            )
        except CatalogueFailure as error:
            if error.kind is CatalogueFailureKind.AUTHENTICATION:
                raise ReauthorizationRequired(str(error)) from None
            discovery_failure = error

        def negotiate() -> str:
            if discovery_failure is not None:
                raise discovery_failure
            assert version is not None
            return version

        identity = to_account_identity(account)
        return (
            self._catalogue_reader(identity, session, negotiate, record_catalogue_call),
            self._fit_downloader(identity, session, negotiate),
        )

    def _catalogue_reader(
        self,
        identity: AccountIdentity,
        session: Session,
        version: Callable[[], str],
        record_call: Callable[[], None] | None = None,
    ) -> CatalogueReader:
        def read(dates: CatalogueDates) -> CataloguePage:
            negotiated = version()

            def query() -> dict[str, object]:
                if record_call is not None:
                    record_call()
                return self._query_activity_records(
                    session, dates, negotiated, CATALOGUE_LIMIT, catalogue=True
                )

            return _parse_catalogue(self._retry_catalogue(query), identity)

        return read

    def discover_tools(self, session: Session) -> tuple[str, frozenset[str]]:
        version = self._initialize(session)
        tools: set[str] = set()
        cursors: set[str] = set()
        params: dict[str, object] = {}
        for request_id in range(2, 22):
            result = self._rpc(session, "tools/list", request_id, params, version)
            page = result.get("tools")
            if not isinstance(page, list):
                raise ConnectionFailure("Invalid COROS tool list.")
            tools.update(_text(_object(entry), "name") for entry in page)
            cursor = result.get("nextCursor")
            if cursor is None:
                return version, frozenset(tools)
            if not isinstance(cursor, str) or not cursor or cursor in cursors:
                raise ConnectionFailure("Invalid COROS tool catalog cursor.")
            cursors.add(cursor)
            params = {"cursor": cursor}
        raise ConnectionFailure("COROS tool catalog exceeded the prototype page limit.")

    def probe_activities(
        self,
        session: Session,
        day: date,
        inspect: Callable[[dict[str, object]], None] | None = None,
        *,
        through: date | None = None,
        limit: int = 20,
    ) -> ActivityProbeResult:
        version = self._initialize(session)
        return self._lookup_activities(
            session, day, version, inspect, through=through, limit=limit
        )

    def _lookup_activities(
        self,
        session: Session,
        day: date,
        version: str,
        inspect: Callable[[dict[str, object]], None] | None = None,
        *,
        through: date | None = None,
        limit: int = 20,
    ) -> ActivityProbeResult:
        result = self._query_activity_records(
            session, CatalogueDates(day, through or day), version, limit
        )
        if inspect is not None:
            inspect(result)
        if result.get("isError"):
            raise ConnectionFailure("COROS refused the activity query.")
        for value in _tool_texts(result):
            match = _RECORD_HEADER.search(value)
            if match:
                start, end, count = match.groups()
                references = tuple(
                    RemoteActivityRef(label, int(sport))
                    for label, sport in _RECORD_ID.findall(value)
                )
                return ActivityProbeResult(
                    int(count),
                    "reported",
                    (start, end),
                    references,
                    _date_observations(value, self._issuer),
                )
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            for key in ("records", "activities"):
                records = structured.get(key)
                if isinstance(records, list) and all(
                    isinstance(row, dict) for row in records
                ):
                    return ActivityProbeResult(len(records), "returned")
        raise ConnectionFailure(
            "Unrecognized COROS activity response; no count inferred."
        )

    def _query_activity_records(
        self,
        session: Session,
        dates: CatalogueDates,
        version: str,
        limit: int,
        *,
        catalogue: bool = False,
    ) -> dict[str, object]:
        return self._rpc(
            session,
            "tools/call",
            2,
            {
                "name": "querySportRecords",
                "arguments": {
                    "startDate": dates.start.isoformat().replace("-", ""),
                    "endDate": dates.end.isoformat().replace("-", ""),
                    "limit": limit,
                },
            },
            version,
            catalogue=catalogue,
        )

    def download_sample(self, session: Session, day: date) -> bytes:
        version = self._initialize(session)
        activities = self._lookup_activities(session, day, version)
        requested = day.isoformat()
        if (
            activities.returned_period != (requested, requested)
            or activities.count != 1
            or len(activities.references) != 1
        ):
            raise ConnectionFailure(
                "FIT probe requires exactly one identifiable activity on the requested day."
            )
        try:
            return self._download_fit(session, activities.references[0], version)
        except ServerFitQuotaExceeded:
            raise ConnectionFailure("COROS MCP tools/call failed (HTTP 429).") from None

    def prepare_fit_download(
        self, account: CorosAccount, session: Session
    ) -> FitRequest:
        """Construct a single-activity request after negotiating MCP."""
        if account.issuer != self._issuer:
            raise ConnectionFailure("The account does not match the COROS connection.")
        version = self._initialize(session)
        return self._fit_downloader(
            to_account_identity(account), session, lambda: version
        )

    def _fit_downloader(
        self, expected: AccountIdentity, session: Session, version: Callable[[], str]
    ) -> FitRequest:
        request_id = 2

        def download(identity: ActivityIdentity) -> bytes:
            nonlocal request_id
            if identity.account != expected:
                raise ConnectionFailure(
                    "The activity does not match the COROS connection."
                )
            request_id += 1
            return self._download_fit(
                session,
                RemoteActivityRef(identity.remote_id, identity.sport),
                version(),
                request_id=request_id,
                production=True,
            )

        return download

    def _download_fit(
        self,
        session: Session,
        activity: RemoteActivityRef,
        version: str,
        *,
        request_id: int = 3,
        production: bool = False,
    ) -> bytes:
        result = self._rpc(
            session,
            "tools/call",
            request_id,
            {
                "name": "downloadActivityFitFiles",
                "arguments": {
                    "labelId": activity.label_id,
                    "sportType": activity.sport_type,
                },
            },
            version,
            fit_request=True,
            production_fit=production,
        )
        if result.get("isError"):
            raise ConnectionFailure("COROS refused the FIT download.")
        content = result.get("content")
        resources: list[dict[str, object]] = []
        if isinstance(content, list):
            for block in content:
                item = _object(block)
                if item.get("type") == "resource":
                    resource = _object(item.get("resource"))
                    if production or isinstance(resource.get("blob"), str):
                        resources.append(resource)
        if len(resources) != 1 or not isinstance(resources[0].get("blob"), str):
            raise ConnectionFailure(
                "COROS did not return exactly one embedded FIT file."
            )
        try:
            data = base64.b64decode(_text(resources[0], "blob"), validate=True)
        except (binascii.Error, ValueError):
            if production:
                raise InvalidFit("COROS returned invalid FIT binary data.") from None
            raise ConnectionFailure("COROS returned invalid FIT binary data.") from None
        if (
            len(data) < 14
            or data[0] not in (12, 14)
            or data[8:12] != b".FIT"
            or len(data) != data[0] + int.from_bytes(data[4:8], "little") + 2
        ):
            if production:
                raise InvalidFit("COROS returned an invalid or incomplete FIT file.")
            raise ConnectionFailure("COROS returned an invalid or incomplete FIT file.")
        if production:
            try:
                check_fit_frames(data)
            except ValueError as error:
                raise InvalidFit(str(error)) from None
        return data
