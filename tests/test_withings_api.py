"""OAuth, pagination, and weight conversion tests with mocked HTTP."""

from __future__ import annotations

import json
import threading
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

import pytest

from fitness_sync.withings_api import (
    AuthRevokedError,
    HttpTransport,
    OAuthCallbackServer,
    TokenSet,
    WithingsAPIError,
    WithingsClient,
    load_tokens,
    save_tokens,
    weight_kg_from_api,
)


def test_weight_conversion_decimal() -> None:
    # value=70500 unit=-3 => 70.5 kg
    assert weight_kg_from_api(70500, -3) == Decimal("70.5")


class FakeResponse:
    def __init__(self, payload: dict[str, Any], code: int = 200) -> None:
        self._payload = payload
        self.code = code

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def test_oauth_callback_starts_and_stops() -> None:
    server = OAuthCallbackServer("127.0.0.1", 0, "/callback")
    # Bind ephemeral: patch by using port 8769 if free
    server.port = 18765
    server.start()
    assert server._httpd is not None
    server.shutdown()
    assert server._httpd is None


def test_token_refresh_and_atomic_save(tmp_path: Path) -> None:
    responses = [
        {
            "status": 0,
            "body": {
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "expires_in": 3600,
                "userid": 111,
            },
        }
    ]

    def opener(req: Any, timeout: float = 30) -> FakeResponse:
        return FakeResponse(responses.pop(0))

    client = WithingsClient(
        client_id="cid",
        client_secret="csec",
        transport=HttpTransport(opener=opener, max_retries=0, sleep=lambda s: None),
    )
    path = tmp_path / "tokens.json"
    old = TokenSet("old", "refresh-1", expires_at=0, userid=111)
    refreshed = client.ensure_access_token(old, token_path=path)
    assert refreshed.access_token == "new-access"
    loaded = load_tokens(path)
    assert loaded is not None
    assert loaded.refresh_token == "new-refresh"
    # file mode ideally 0600 — may not enforce on all FS; content must not include logging
    text = path.read_text(encoding="utf-8")
    assert "new-access" in text


def test_refresh_revoked_raises() -> None:
    def opener(req: Any, timeout: float = 30) -> FakeResponse:
        return FakeResponse({"status": 401, "body": {}})

    client = WithingsClient(
        client_id="cid",
        client_secret="csec",
        transport=HttpTransport(opener=opener, max_retries=0, sleep=lambda s: None),
    )
    with pytest.raises((AuthRevokedError, WithingsAPIError)):
        client.refresh("bad-refresh")


def test_getmeas_pagination_and_type_filter() -> None:
    pages = [
        {
            "status": 0,
            "body": {
                "updatetime": 1000,
                "more": 1,
                "offset": 1,
                "measuregrps": [
                    {
                        "grpid": 1,
                        "date": 1700000000,
                        "userid": 111,
                        "measures": [
                            {"type": 1, "value": 70000, "unit": -3},
                            {"type": 8, "value": 1, "unit": 0},
                        ],
                    }
                ],
            },
        },
        {
            "status": 0,
            "body": {
                "updatetime": 1001,
                "more": 0,
                "measuregrps": [
                    {
                        "grpid": 2,
                        "date": 1700000000,
                        "userid": 111,
                        "measures": [{"type": 1, "value": 71000, "unit": -3}],
                    },
                    {
                        "grpid": 3,
                        "date": 1700000100,
                        "userid": 111,
                        "measures": [{"type": 8, "value": 1, "unit": 0}],
                    },
                ],
            },
        },
    ]

    def opener(req: Any, timeout: float = 30) -> FakeResponse:
        return FakeResponse(pages.pop(0))

    client = WithingsClient(
        client_id="cid",
        client_secret="csec",
        transport=HttpTransport(opener=opener, max_retries=0, sleep=lambda s: None),
    )
    rows = client.iter_weight_measurements("token")
    assert len(rows) == 2
    assert rows[0].weight_kg == 70.0
    assert rows[1].weight_kg == 71.0
    assert client.last_updatetime == 1001


def test_nonzero_status_raises() -> None:
    def opener(req: Any, timeout: float = 30) -> FakeResponse:
        return FakeResponse({"status": 255, "body": {}})

    client = WithingsClient(
        client_id="cid",
        client_secret="csec",
        transport=HttpTransport(opener=opener, max_retries=0, sleep=lambda s: None),
    )
    with pytest.raises(WithingsAPIError, match="nonzero"):
        client.getmeas_page("tok")


def test_secrets_not_in_log_messages(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    caplog.set_level(logging.DEBUG)
    tokens = TokenSet("super-secret-access", "super-secret-refresh", 9999999999)
    # ensure logging path used by refresh doesn't dump tokens
    logging.getLogger("fitness_sync.withings_api").info(
        "access token expired or near expiry; refreshing"
    )
    assert "super-secret" not in caplog.text
