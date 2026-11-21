"""Withings HTTPS API client: OAuth, token refresh, getmeas pagination."""

from __future__ import annotations

import json
import logging
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

AUTH_URL = "https://account.withings.com/oauth2_user/authorize2"
OAUTH_TOKEN_URL = "https://wbsapi.withings.net/v2/oauth2"
MEASURE_URL = "https://wbsapi.withings.net/measure"
MEASTYPE_WEIGHT = 1
CATEGORY_REAL = 1


class WithingsAPIError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class AuthRevokedError(WithingsAPIError):
    pass


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str
    expires_at: int  # unix epoch seconds
    userid: int | None = None

    def is_expired(self, *, skew_seconds: int = 60) -> bool:
        return time.time() >= (self.expires_at - skew_seconds)

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "userid": self.userid,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TokenSet:
        return cls(
            access_token=str(data["access_token"]),
            refresh_token=str(data["refresh_token"]),
            expires_at=int(data["expires_at"]),
            userid=int(data["userid"]) if data.get("userid") is not None else None,
        )


@dataclass(frozen=True)
class WeightMeasurement:
    grpid: int
    measured_at_utc: int
    weight_kg: float
    source_userid: int | None = None


def weight_kg_from_api(value: int | str, unit: int | str) -> Decimal:
    """Deterministic conversion: weight_kg = value * 10 ** unit."""
    return Decimal(value) * (Decimal(10) ** Decimal(unit))


def atomic_write_json(path: Path, data: dict[str, Any], *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(payload)
        fh.flush()
        try:
            import os

            os.fsync(fh.fileno())
        except OSError:
            pass
    tmp.replace(path)
    try:
        path.chmod(mode)
    except OSError:
        pass


def load_tokens(path: Path) -> TokenSet | None:
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as fh:
        return TokenSet.from_dict(json.load(fh))


def save_tokens(path: Path, tokens: TokenSet) -> None:
    atomic_write_json(path, tokens.to_dict())


def read_secret_file(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


class HttpTransport:
    """Minimal HTTPS POST form client with retries (stdlib)."""

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        max_retries: int = 5,
        sleep: Callable[[float], None] = time.sleep,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.timeout = timeout
        self.max_retries = max_retries
        self.sleep = sleep
        self._opener = opener or urllib.request.urlopen

    def post_form(
        self,
        url: str,
        fields: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if not url.startswith("https://"):
            raise WithingsAPIError(f"refusing non-HTTPS URL: {url}")
        body = urllib.parse.urlencode(
            {k: str(v) for k, v in fields.items() if v is not None}
        ).encode()
        hdrs = {"Content-Type": "application/x-www-form-urlencoded"}
        if headers:
            hdrs.update(headers)

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
            try:
                with self._opener(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8")
                data = json.loads(raw)
                return data
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code in {429, 500, 502, 503, 504} and attempt < self.max_retries:
                    delay = min(60.0, (2**attempt) + (0.1 * attempt))
                    logger.warning(
                        "transient HTTP %s; retry in %.1fs", exc.code, delay
                    )
                    self.sleep(delay)
                    continue
                raise WithingsAPIError(f"HTTP error {exc.code}") from exc
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    delay = min(60.0, (2**attempt) + (0.1 * attempt))
                    logger.warning("transient network error; retry in %.1fs", delay)
                    self.sleep(delay)
                    continue
                raise WithingsAPIError(f"network error: {exc}") from exc
        raise WithingsAPIError(f"exhausted retries: {last_error}")


class OAuthCallbackServer:
    """Short-lived localhost callback for authorization-code flow."""

    def __init__(self, host: str, port: int, path: str) -> None:
        self.host = host
        self.port = port
        self.path = path
        self.code: str | None = None
        self.error: str | None = None
        self._event = threading.Event()
        self._httpd: HTTPServer | None = None

    def start(self) -> None:
        path = self.path
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != path:
                    self.send_response(404)
                    self.end_headers()
                    return
                qs = urllib.parse.parse_qs(parsed.query)
                if "error" in qs:
                    owner.error = qs["error"][0]
                else:
                    owner.code = (qs.get("code") or [None])[0]
                    if not owner.code:
                        owner.error = "missing_code"
                body = b"Authorization complete. You can close this window."
                if owner.error:
                    body = b"Authorization failed. You can close this window."
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                owner._event.set()

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                return

        self._httpd = HTTPServer((self.host, self.port), Handler)
        thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        thread.start()

    def wait(self, timeout: float = 300.0) -> str:
        ok = self._event.wait(timeout)
        self.shutdown()
        if not ok:
            raise WithingsAPIError("timed out waiting for OAuth callback")
        if self.error:
            raise WithingsAPIError(f"OAuth callback error: {self.error}")
        if not self.code:
            raise WithingsAPIError("OAuth callback missing authorization code")
        return self.code

    def shutdown(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None


class WithingsClient:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        transport: HttpTransport | None = None,
        weight_min_kg: float = 20.0,
        weight_max_kg: float = 400.0,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.transport = transport or HttpTransport()
        self.weight_min_kg = weight_min_kg
        self.weight_max_kg = weight_max_kg

    def build_authorize_url(self, *, redirect_uri: str, state: str | None = None) -> str:
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "state": state or secrets.token_urlsafe(16),
            "scope": "user.metrics",
            "redirect_uri": redirect_uri,
        }
        return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    def exchange_code(self, code: str, *, redirect_uri: str) -> TokenSet:
        data = self.transport.post_form(
            OAUTH_TOKEN_URL,
            {
                "action": "requesttoken",
                "grant_type": "authorization_code",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
        return self._parse_token_response(data)

    def refresh(self, refresh_token: str) -> TokenSet:
        data = self.transport.post_form(
            OAUTH_TOKEN_URL,
            {
                "action": "requesttoken",
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": refresh_token,
            },
        )
        try:
            return self._parse_token_response(data)
        except WithingsAPIError as exc:
            # Withings uses application-level status codes; treat auth failures
            # as revoked so callers fail visibly instead of looping.
            if exc.status in {100, 401, 503} or "invalid" in str(exc).lower():
                raise AuthRevokedError(
                    "refresh token rejected; run interactive reauthorization"
                ) from exc
            raise

    def ensure_access_token(
        self, tokens: TokenSet, *, token_path: Path | None = None
    ) -> TokenSet:
        if not tokens.is_expired():
            return tokens
        logger.info("access token expired or near expiry; refreshing")
        refreshed = self.refresh(tokens.refresh_token)
        # Preserve userid if refresh omits it
        if refreshed.userid is None:
            refreshed.userid = tokens.userid
        if token_path is not None:
            save_tokens(token_path, refreshed)
        return refreshed

    def getmeas_page(
        self,
        access_token: str,
        *,
        offset: int = 0,
        lastupdate: int | None = None,
        startdate: int | None = None,
        enddate: int | None = None,
    ) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "action": "getmeas",
            "meastype": MEASTYPE_WEIGHT,
            "category": CATEGORY_REAL,
            "offset": offset,
        }
        if lastupdate is not None:
            fields["lastupdate"] = lastupdate
        if startdate is not None:
            fields["startdate"] = startdate
        if enddate is not None:
            fields["enddate"] = enddate
        data = self.transport.post_form(
            MEASURE_URL,
            fields,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        status = int(data.get("status", -1))
        if status != 0:
            raise WithingsAPIError(
                f"getmeas nonzero status: {status}", status=status
            )
        body = data.get("body")
        if not isinstance(body, dict):
            raise WithingsAPIError("getmeas missing body")
        return body

    def iter_weight_measurements(
        self,
        access_token: str,
        *,
        lastupdate: int | None = None,
        startdate: int | None = None,
        enddate: int | None = None,
        max_pages: int = 10_000,
    ) -> list[WeightMeasurement]:
        """Fetch all pages; raises if pagination loops or pages are malformed."""
        results: list[WeightMeasurement] = []
        offset = 0
        seen_offsets: set[int] = set()
        updatetime: int | None = None

        for _ in range(max_pages):
            if offset in seen_offsets:
                raise WithingsAPIError(f"pagination loop detected at offset={offset}")
            seen_offsets.add(offset)
            body = self.getmeas_page(
                access_token,
                offset=offset,
                lastupdate=lastupdate,
                startdate=startdate,
                enddate=enddate,
            )
            if "updatetime" in body:
                updatetime = int(body["updatetime"])
            groups = body.get("measuregrps")
            if groups is None:
                raise WithingsAPIError("malformed page: missing measuregrps")
            if not isinstance(groups, list):
                raise WithingsAPIError("malformed page: measuregrps not a list")
            for grp in groups:
                parsed = self._parse_group(grp)
                if parsed is not None:
                    results.append(parsed)
            more = int(body.get("more") or 0)
            if not more:
                break
            next_offset = body.get("offset")
            if next_offset is None:
                raise WithingsAPIError("more=1 but offset missing")
            offset = int(next_offset)
        else:
            raise WithingsAPIError("exceeded max_pages during pagination")

        # Attach response-level updatetime for callers via attribute on list? return tuple
        self._last_updatetime = updatetime
        return results

    @property
    def last_updatetime(self) -> int | None:
        return getattr(self, "_last_updatetime", None)

    def _parse_group(self, grp: dict[str, Any]) -> WeightMeasurement | None:
        try:
            grpid = int(grp["grpid"])
            measured_at = int(grp["date"])
            measures = grp.get("measures") or []
        except (KeyError, TypeError, ValueError) as exc:
            raise WithingsAPIError(f"malformed measure group: {exc}") from exc

        weight: Decimal | None = None
        for m in measures:
            if int(m.get("type", -1)) != MEASTYPE_WEIGHT:
                continue
            weight = weight_kg_from_api(m["value"], m["unit"])
            break
        if weight is None:
            return None
        kg = float(weight)
        if kg < self.weight_min_kg or kg > self.weight_max_kg:
            raise WithingsAPIError(f"implausible weight_kg={kg} for grpid={grpid}")
        userid = grp.get("userid")
        return WeightMeasurement(
            grpid=grpid,
            measured_at_utc=measured_at,
            weight_kg=kg,
            source_userid=int(userid) if userid is not None else None,
        )

    def _parse_token_response(self, data: dict[str, Any]) -> TokenSet:
        status = int(data.get("status", -1))
        if status != 0:
            raise WithingsAPIError(
                f"oauth nonzero status: {status}", status=status
            )
        body = data.get("body") or {}
        access = body.get("access_token")
        refresh = body.get("refresh_token")
        expires_in = body.get("expires_in")
        if not access or not refresh or expires_in is None:
            raise WithingsAPIError("oauth response missing token fields")
        userid = body.get("userid")
        return TokenSet(
            access_token=str(access),
            refresh_token=str(refresh),
            expires_at=int(time.time()) + int(expires_in),
            userid=int(userid) if userid is not None else None,
        )
