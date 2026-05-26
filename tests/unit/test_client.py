"""Tests for AIRClient against httpx.MockTransport — zero network."""

from __future__ import annotations

import httpx
import pytest

from agent_identity_registry import (
    AgentNotFoundError,
    AIRClient,
    AuthenticationError,
    NetworkError,
    RateLimitedError,
    ValidationError,
)

# ----- happy path: every endpoint round-trips through MockTransport -----


async def test_get_health(mock_client):
    client = mock_client(
        {("GET", "/api/v1/health"): (200, {"status": "ok", "version": "0.1.0", "registry": "AIR"})}
    )
    async with client as c:
        h = await c.get_health()
    assert h.status == "ok"


async def test_list_agents_pagination(mock_handler):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={"agents": [], "total": 0, "limit": 50, "offset": 10},
        )

    async with AIRClient(
        base_url="https://test.invalid",
        transport=httpx.MockTransport(handler),
    ) as c:
        lst = await c.list_agents(limit=50, offset=10)
    assert captured["params"] == {"limit": "50", "offset": "10"}
    assert lst.limit == 50


async def test_get_agent_404_propagates_air_id(mock_client):
    client = mock_client(
        {("GET", "/api/v1/agents/AIR-NONE-NONE-NONE"): (404, {"error": "Agent not found"})}
    )
    async with client as c:
        with pytest.raises(AgentNotFoundError) as ei:
            await c.get_agent("AIR-NONE-NONE-NONE")
    assert ei.value.air_id == "AIR-NONE-NONE-NONE"


async def test_check_name(mock_client):
    client = mock_client(
        {
            ("GET", "/api/v1/agents/check-name"): (
                200,
                {"name": "Foo", "exists": False, "count": 0, "existing_agents": []},
            )
        }
    )
    async with client as c:
        nc = await c.check_name("Foo")
    assert nc.exists is False


async def test_register_agent_sends_full_body(mock_handler):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as j
        captured["body"] = j.loads(request.content)
        return httpx.Response(
            201,
            json={
                "air_id": "AIR-XYZ1-ABC2-DEF3",
                "name": "TestBot",
                "creator_did": "did:wba:agentidentityregistry.org:agents:AIR-XYZ1-ABC2-DEF3",
                "air_minted_did": True,
                "status": "active",
                "verification_level": "self-verified",
                "trust_score": 540,
                "trust_grade": "BB",
                "created": "2026-05-25T00:00:00.000Z",
                "agent_secret": "x" * 32,
                "agent_secret_note": "Store this.",
                "message": "ok",
            },
        )

    async with AIRClient(
        base_url="https://test.invalid",
        transport=httpx.MockTransport(handler),
    ) as c:
        result = await c.register_agent(
            name="TestBot",
            public_key="A" * 43,
            capabilities=["test"],
            open_source=True,
        )

    # creator_did omitted → not in body (keyless registration triggers AIR-minted DID)
    assert "creator_did" not in captured["body"]
    assert captured["body"]["public_key"] == "A" * 43
    assert captured["body"]["capabilities"] == ["test"]
    assert captured["body"]["open_source"] is True
    assert result.air_minted_did is True


async def test_update_agent_sends_secret_header(mock_handler):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["secret"] = request.headers.get("X-Agent-Secret")
        captured["body"] = request.content
        return httpx.Response(
            200,
            json={
                "air_id": "AIR-X",
                "updated_fields": 1,
                "trust_score": 700,
                "trust_grade": "A",
                "updated": "2026-05-25T00:00:00.000Z",
                "message": "ok",
            },
        )

    async with AIRClient(
        base_url="https://test.invalid",
        transport=httpx.MockTransport(handler),
    ) as c:
        result = await c.update_agent(
            "AIR-X",
            agent_secret="my-secret-32-chars",
            description="new",
        )

    assert captured["secret"] == "my-secret-32-chars"
    assert b"description" in captured["body"]
    assert result.updated_fields == 1


async def test_update_agent_empty_body_raises_client_side(mock_client):
    """No fields → fail fast without an HTTP round-trip."""
    client = mock_client({})  # No routes — confirms we don't even attempt the request
    async with client as c:
        with pytest.raises(ValidationError):
            await c.update_agent("AIR-X", agent_secret="s")


# ----- admin endpoints require admin_key ---------------------------------


async def test_admin_endpoint_without_key_raises(mock_client):
    client = mock_client({})  # No admin_key passed
    async with client as c:
        with pytest.raises(AuthenticationError):
            await c.get_admin_stats()


async def test_admin_endpoint_sends_x_admin_key_header(mock_handler):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["admin"] = request.headers.get("X-Admin-Key")
        return httpx.Response(
            200,
            json={
                "total_agents": 0,
                "real_agents": 0,
                "demo_agents": 0,
                "verified": 0,
                "unverified": 0,
                "registered_last_7_days": 0,
                "average_trust_score": None,
                "grade_distribution": {},
            },
        )

    async with AIRClient(
        base_url="https://test.invalid",
        admin_key="secret-admin-key",
        transport=httpx.MockTransport(handler),
    ) as c:
        await c.get_admin_stats()

    assert captured["admin"] == "secret-admin-key"


# ----- error mapping wired correctly -------------------------------------


async def test_429_extracts_retry_after_from_envelope(mock_client):
    client = mock_client(
        {
            ("POST", "/api/v1/agents/register"): (
                429,
                {"error": "Rate limit exceeded", "retry_after_seconds": 3600},
            )
        }
    )
    async with client as c:
        with pytest.raises(RateLimitedError) as ei:
            await c.register_agent(name="X", public_key="A" * 43)
    assert ei.value.retry_after_seconds == 3600


async def test_network_error_chained_from_httpx(mock_handler):
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated DNS failure")

    async with AIRClient(
        base_url="https://test.invalid",
        transport=httpx.MockTransport(handler),
    ) as c:
        with pytest.raises(NetworkError) as ei:
            await c.get_health()
    assert isinstance(ei.value.__cause__, httpx.ConnectError)
    assert ei.value.status_code is None
