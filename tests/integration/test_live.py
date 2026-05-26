"""Live integration tests against agentidentityregistry.org.

Skipped by default. Run with: AIR_LIVE_TESTS=1 pytest tests/integration/

Reads only — never registers, updates, or deletes anything. Safe against the
production registry.
"""

from __future__ import annotations

import pytest

from agent_identity_registry import AgentNotFoundError, AIRClient
from tests.conftest import live_only

pytestmark = live_only


async def test_live_health():
    async with AIRClient() as c:
        h = await c.get_health()
    assert h.status == "ok"
    assert h.registry == "AIR"


async def test_live_list_agents():
    async with AIRClient() as c:
        lst = await c.list_agents(limit=5)
    assert lst.total >= 1
    assert len(lst.agents) <= 5


async def test_live_get_first_agent():
    async with AIRClient() as c:
        lst = await c.list_agents(limit=1)
        assert lst.agents, "registry should have at least one agent"
        first = lst.agents[0].air_id
        agent = await c.get_agent(first)
    assert agent.air_id == first
    assert agent.name


async def test_live_trust_score():
    async with AIRClient() as c:
        lst = await c.list_agents(limit=1)
        score = await c.get_trust_score(lst.agents[0].air_id)
    assert 0 <= score.total_score <= 1000
    assert score.components.provenance >= 0


async def test_live_did_document_for_keyed_agent():
    """Find a demo agent with a public_key and verify its DID document parses.

    Per air/session-handoff-2026-05-25, WeatherBot-Demo and NotaryBot-Demo
    were seeded with deterministic Ed25519 keys. They should always be present.
    """
    async with AIRClient() as c:
        lst = await c.list_agents(limit=100)
        keyed = None
        for summary in lst.agents:
            agent = await c.get_agent(summary.air_id)
            if agent.creator.public_key:
                keyed = summary.air_id
                break
        assert keyed, "expected at least one agent with a public_key on file"
        doc = await c.get_did_document(keyed)

    assert doc.id.startswith("did:wba:agentidentityregistry.org:agents:")
    assert doc.verification_method[0].type == "Ed25519VerificationKey2020"
    assert doc.verification_method[0].public_key_multibase.startswith("z")


async def test_live_404():
    async with AIRClient() as c:
        with pytest.raises(AgentNotFoundError) as ei:
            await c.get_agent("AIR-NONE-NONE-NONE")
    assert ei.value.air_id == "AIR-NONE-NONE-NONE"
    assert ei.value.status_code == 404
