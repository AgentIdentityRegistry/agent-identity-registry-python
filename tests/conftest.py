"""Shared pytest fixtures for the AIR SDK tests.

Unit tests use `mock_handler` to stage canned responses via httpx.MockTransport
without ever touching the network. Integration tests in tests/integration/
hit the live registry and are skipped unless AIR_LIVE_TESTS=1 is set.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from agent_identity_registry import AIRClient


@pytest.fixture
def mock_handler() -> Callable[..., httpx.MockTransport]:
    """Build an httpx.MockTransport from a (method, path) → (status, body) map.

    Usage:
        transport = mock_handler({
            ("GET", "/api/v1/health"): (200, {"status": "ok", ...}),
            ("GET", "/api/v1/agents/AIR-X"): (404, {"error": "Agent not found"}),
        })
        async with AIRClient(transport=transport) as client:
            ...
    """

    def _make(routes: dict[tuple[str, str], tuple[int, dict[str, Any]]]) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            key = (request.method, request.url.path)
            if key not in routes:
                return httpx.Response(
                    404,
                    json={"error": f"No mock route for {key}"},
                )
            status, body = routes[key]
            return httpx.Response(
                status,
                content=json.dumps(body),
                headers={"Content-Type": "application/json"},
            )

        return httpx.MockTransport(handler)

    return _make


@pytest.fixture
def mock_client(mock_handler):
    """Factory: returns an AIRClient bound to a MockTransport with the given routes."""

    def _make(
        routes: dict[tuple[str, str], tuple[int, dict[str, Any]]],
        *,
        admin_key: str | None = None,
    ) -> AIRClient:
        return AIRClient(
            base_url="https://test.invalid",
            admin_key=admin_key,
            transport=mock_handler(routes),
        )

    return _make


# ---- Integration test gate ----------------------------------------------

LIVE_ENABLED = os.environ.get("AIR_LIVE_TESTS") == "1"

live_only = pytest.mark.skipif(
    not LIVE_ENABLED,
    reason="Set AIR_LIVE_TESTS=1 to hit the live registry",
)
