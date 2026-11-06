"""Contract test: the OpenAPI document is snapshotted so breaking changes are deliberate (spec §13).

Regenerate after an intentional API change:  UPDATE_OPENAPI_SNAPSHOT=1 uv run pytest tests/test_openapi.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

SNAPSHOT = Path(__file__).parent / "snapshots" / "openapi.json"


async def test_openapi_snapshot(client: httpx.AsyncClient) -> None:
    current: dict[str, Any] = (await client.get("/openapi.json")).json()
    rendered = json.dumps(current, indent=2, sort_keys=True) + "\n"
    if os.environ.get("UPDATE_OPENAPI_SNAPSHOT") == "1" or not SNAPSHOT.exists():
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_text(rendered)
    assert json.loads(SNAPSHOT.read_text()) == current, (
        "OpenAPI changed. If intentional, run: UPDATE_OPENAPI_SNAPSHOT=1 uv run pytest tests/test_openapi.py"
    )


async def test_openapi_documents_auth_schemes(client: httpx.AsyncClient) -> None:
    spec = (await client.get("/openapi.json")).json()
    schemes = spec["components"]["securitySchemes"]
    assert {"Authorization", "DriverId", "ProvisioningKey"} <= set(schemes)
    assert "ErrorResponse" in spec["components"]["schemas"]
