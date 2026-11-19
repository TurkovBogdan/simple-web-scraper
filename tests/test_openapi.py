"""The committed OpenAPI document (`docs/openapi.json`) must match the code (no browser)."""
from __future__ import annotations

import json

import openapi_spec


def test_committed_spec_matches_the_code() -> None:
    committed = openapi_spec.SPEC_PATH.read_text(encoding="utf-8")
    assert committed == openapi_spec.render(), (
        "docs/openapi.json is stale — regenerate it: uv run python -m openapi_spec"
    )


def test_spec_holds_only_the_public_api() -> None:
    # The worker's own routes (/healthz, /scrap, …) are an internal contract and never
    # appear here; a new public route must be added on purpose.
    paths = json.loads(openapi_spec.render())["paths"]
    routes = {
        (method.upper(), path, op.get("deprecated", False))
        for path, ops in paths.items()
        for method, op in ops.items()
    }
    assert routes == {
        ("GET", "/health", False),
        ("POST", "/api/1.0/scrap", False),
        ("POST", "/api/1.0/scrap/batch", False),
        ("DELETE", "/api/1.0/profile", False),
    }
