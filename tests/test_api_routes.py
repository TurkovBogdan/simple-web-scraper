"""Unit tests for the resident's public routes (no worker, no browser).

The worker manager is faked, so these check only the API surface: which route reaches
which operation, the bearer token and the same-origin rule.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import auth
import server
from config import Settings

KEY = "test-key"
AUTH = {"Authorization": f"Bearer {KEY}"}


class _FakeBrowser:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def scrap(self, payload: dict) -> dict:
        self.calls.append(("scrap", payload["url"]))
        return {"outcome": "ok", "url": payload["url"]}

    async def scrap_batch(self, payload: dict) -> dict:
        self.calls.append(("scrap_batch", payload["urls"]))
        return {"results": [], "elapsed_ms": 0}

    async def delete_profile(self) -> dict:
        self.calls.append(("delete_profile", None))
        return {"ok": True}


@pytest.fixture
def api(monkeypatch):
    """A client of the real app, without its lifespan, over a fake worker manager."""
    fake = _FakeBrowser()
    monkeypatch.setattr(server.app.state, "services", SimpleNamespace(browser=fake), raising=False)
    monkeypatch.setattr(auth, "get_settings", lambda: Settings(_env_file=None, scraper_api_key=KEY))
    return TestClient(server.app), fake


def test_scrap_zone_reaches_the_scrapes(api) -> None:
    client, fake = api

    one = client.post("/api/1.0/scrap", json={"url": "https://a"}, headers=AUTH)
    many = client.post("/api/1.0/scrap/batch", json={"urls": ["https://a", ""]}, headers=AUTH)

    assert one.status_code == 200 and many.status_code == 200
    assert fake.calls == [("scrap", "https://a"), ("scrap_batch", ["https://a"])]


@pytest.mark.parametrize(
    ("timeout", "status"),
    [("1", 200), ("60", 200), ("0", 422), ("61", 422), ("-1", 422), ("2.5", 422),
     ("Infinity", 422), ("NaN", 422)],
)
def test_timeout_is_whole_seconds_from_1_to_60(api, timeout, status) -> None:
    client, fake = api
    body = f'{{"url": "https://a", "timeout": {timeout}}}'  # raw: json= cannot send NaN

    r = client.post("/api/1.0/scrap", content=body, headers={**AUTH, "Content-Type": "application/json"})

    assert r.status_code == status
    assert len(fake.calls) == (1 if status == 200 else 0)


def test_profile_zone_deletes_with_delete_only(api) -> None:
    client, fake = api

    assert client.delete("/api/1.0/profile", headers=AUTH).json() == {"ok": True}
    assert client.post("/api/1.0/profile", headers=AUTH).status_code == 405
    assert fake.calls == [("delete_profile", None)]


def test_pre_split_paths_are_gone(api) -> None:
    client, fake = api

    batch = client.post("/api/1.0/scrap-batch", json={"urls": ["https://a"]}, headers=AUTH)
    clear = client.post("/api/1.0/clear-profile", headers=AUTH)

    assert batch.status_code == 404 and clear.status_code == 404
    assert fake.calls == []


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/1.0/scrap", {"url": "https://a"}),
        ("POST", "/api/1.0/scrap/batch", {"urls": ["https://a"]}),
        ("DELETE", "/api/1.0/profile", None),
    ],
)
def test_cross_origin_browser_requests_are_refused(api, method, path, body) -> None:
    client, fake = api
    headers = {**AUTH, "Origin": "https://evil.example"}

    r = client.request(method, path, json=body, headers=headers)

    assert r.status_code == 403
    assert r.json()["error"]["code"] == "origin_refused"
    assert fake.calls == []  # refused before the operation ran


def test_same_origin_and_originless_requests_pass(api) -> None:
    client, fake = api

    same = client.delete("/api/1.0/profile", headers={**AUTH, "Origin": "http://testserver"})
    plain = client.delete("/api/1.0/profile", headers=AUTH)

    assert same.status_code == 200 and plain.status_code == 200
    assert len(fake.calls) == 2


def test_token_is_required_when_a_key_is_set(api) -> None:
    client, fake = api

    missing = client.delete("/api/1.0/profile")
    wrong = client.delete("/api/1.0/profile", headers={"Authorization": "Bearer nope"})

    assert missing.status_code == 401 and wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "invalid_api_key"
    assert fake.calls == []


def test_health_needs_no_token(api) -> None:
    client, _ = api
    assert client.get("/health").json() == {"ok": True}
