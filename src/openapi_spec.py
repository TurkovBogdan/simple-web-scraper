"""OpenAPI document of the public (resident) API; the committed copy is `docs/openapi.json`.

Regenerate it after changing the public API — routes in `server.py`, models in
`schemas.py`, or a FastAPI/pydantic upgrade that changes the output:

    uv run python -m openapi_spec

`tests/test_openapi.py` fails while the committed copy is stale.
"""
from __future__ import annotations

import json
from pathlib import Path

from server import app

SPEC_PATH = Path(__file__).resolve().parents[1] / "docs" / "openapi.json"


def render() -> str:
    """The document as stable, diff-friendly JSON."""
    return json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    SPEC_PATH.parent.mkdir(parents=True, exist_ok=True)
    SPEC_PATH.write_text(render(), encoding="utf-8")
    print(f"wrote {SPEC_PATH}")


if __name__ == "__main__":
    main()
