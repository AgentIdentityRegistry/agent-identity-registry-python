"""Tests for Pydantic model round-trips against the real API response shapes.

Each fixture below is a verbatim sample of what api/src/index.js actually
emits. If the API ever changes shape, these break first and tell us exactly
where in the contract things diverged.
"""

from __future__ import annotations

from datetime import datetime

from agent_identity_registry.models import (
    AdminRecent,
    AdminStats,
    Agent,
    AgentList,
    DidDocument,
    ErrorEnvelope,
    Health,
    NameCheck,
    RegistrationResult,
    TrustScore,
    UpdateResult,
)


def test_health():
    h = Health.model_validate({"status": "ok", "version": "0.1.0", "registry": "AIR"})
    assert h.status == "ok"
    assert h.version == "0.1.0"


def test_trust_score_with_components():
    payload = {
        "air_id": "AIR-XXXX-YYYY-ZZZZ",
        "total_score": 942,
        "grade": "AAA",
        "components": {
            "provenance": 960,
            "behavioral": 900,
            "transparency": 950,
            "security": 920,
            "peer_attestations": 930,
        },
        "calculated_at": "2026-05-24T12:34:56.000Z",
    }
    ts = TrustScore.model_validate(payload)
    assert ts.total_score == 942
    assert ts.components.provenance == 960
    assert isinstance(ts.calculated_at, datetime)


def test_agent_full_lookup():
    payload = {
        "@context": "https://agentidentityregistry.org/v1",
        "type": "AgentIdentity",
        "air_id": "AIR-7F3K-M9JQ-X2PL",
        "name": "DataProcessor-v3",
        "description": "Processes structured data",
        "creator": {
            "did": "did:web:example.com",
            "name": "Example Inc",
            "type": "organization",
            "public_key": "AAAA" * 11,  # placeholder 44-char base64url
        },
        "capabilities": ["etl", "validation"],
        "security": {"certifications": ["SOC2"]},
        "transparency": {
            "open_source": True,
            "code_repository": "https://github.com/example/dp",
            "documentation_url": "https://example.com/docs",
        },
        "verified": True,
        "verification_level": "kyc-verified",
        "is_demo": False,
        "status": "active",
        "created": "2026-04-01T00:00:00.000Z",
        "updated": "2026-05-01T00:00:00.000Z",
        "trust_score": 942,
        "trust_grade": "AAA",
        "components": {
            "provenance": 960,
            "behavioral": 900,
            "transparency": 950,
            "security": 920,
            "peer_attestations": 930,
        },
    }
    a = Agent.model_validate(payload)
    assert a.air_id == "AIR-7F3K-M9JQ-X2PL"
    assert a.creator.type == "organization"
    assert a.transparency.open_source is True
    assert a.security.certifications == ["SOC2"]
    assert a.components.security == 920
    # JSON-LD framing fields preserved via alias
    assert a.context == "https://agentidentityregistry.org/v1"
    assert a.type_ == "AgentIdentity"


def test_agent_list_pagination():
    payload = {
        "agents": [
            {
                "air_id": "AIR-AAAA-BBBB-CCCC",
                "name": "Bot A",
                "description": "",
                "verified": False,
                "verification_level": "self-verified",
                "is_demo": True,
                "trust_score": 700,
                "trust_grade": "A",
                "created": "2026-04-01T00:00:00.000Z",
            }
        ],
        "total": 1,
        "limit": 20,
        "offset": 0,
    }
    lst = AgentList.model_validate(payload)
    assert lst.total == 1
    assert lst.agents[0].is_demo is True


def test_did_document_camelcase_aliases():
    """DID Core spec uses camelCase on the wire; we expose snake_case to Python."""
    payload = {
        "@context": [
            "https://www.w3.org/ns/did/v1",
            "https://w3id.org/security/suites/ed25519-2020/v1",
        ],
        "id": "did:wba:agentidentityregistry.org:agents:AIR-X",
        "alsoKnownAs": ["did:web:example.com"],
        "verificationMethod": [
            {
                "id": "did:wba:...#key-1",
                "type": "Ed25519VerificationKey2020",
                "controller": "did:wba:...",
                "publicKeyMultibase": "z6Mk...",
            }
        ],
        "authentication": ["did:wba:...#key-1"],
        "assertionMethod": ["did:wba:...#key-1"],
        "service": [
            {
                "id": "#trust-score",
                "type": "AIRTrustScore",
                "serviceEndpoint": "https://agentidentityregistry.org/api/v1/agents/AIR-X/trust-score",
            }
        ],
    }
    doc = DidDocument.model_validate(payload)
    assert doc.id.startswith("did:wba:")
    assert doc.also_known_as == ["did:web:example.com"]
    assert doc.verification_method[0].public_key_multibase == "z6Mk..."
    assert doc.assertion_method == ["did:wba:...#key-1"]
    assert doc.service[0].service_endpoint.endswith("/trust-score")


def test_did_document_without_optional_also_known_as():
    payload = {
        "@context": ["https://www.w3.org/ns/did/v1"],
        "id": "did:wba:agentidentityregistry.org:agents:AIR-Y",
        "verificationMethod": [
            {
                "id": "#k",
                "type": "Ed25519VerificationKey2020",
                "controller": "did:wba:...",
                "publicKeyMultibase": "z6Mk...",
            }
        ],
        "authentication": ["#k"],
        "assertionMethod": ["#k"],
        "service": [],
    }
    doc = DidDocument.model_validate(payload)
    assert doc.also_known_as is None


def test_name_check_with_matches():
    payload = {
        "name": "WeatherBot",
        "exists": True,
        "count": 2,
        "existing_agents": [
            {"air_id": "AIR-W-1", "name": "WeatherBot"},
            {"air_id": "AIR-W-2", "name": "WeatherBot"},
        ],
    }
    nc = NameCheck.model_validate(payload)
    assert nc.exists is True
    assert len(nc.existing_agents) == 2


def test_registration_result_with_air_minted_did():
    payload = {
        "air_id": "AIR-XYZ1-ABC2-DEF3",
        "name": "TestBot",
        "creator_did": "did:wba:agentidentityregistry.org:agents:AIR-XYZ1-ABC2-DEF3",
        "air_minted_did": True,
        "status": "active",
        "verification_level": "self-verified",
        "trust_score": 540,
        "trust_grade": "BB",
        "created": "2026-05-25T00:00:00.000Z",
        "public_key": "A" * 43,
        "agent_secret": "abc123" * 5 + "ab",  # 32 chars
        "agent_secret_note": "Store agent_secret securely.",
        "message": "Agent registered successfully.",
    }
    r = RegistrationResult.model_validate(payload)
    assert r.air_minted_did is True
    assert len(r.agent_secret) == 32
    assert r.warnings is None


def test_update_result():
    payload = {
        "air_id": "AIR-X",
        "updated_fields": 2,
        "trust_score": 800,
        "trust_grade": "A",
        "updated": "2026-05-25T00:00:00.000Z",
        "message": "Agent updated successfully.",
    }
    u = UpdateResult.model_validate(payload)
    assert u.updated_fields == 2


def test_admin_stats_with_grade_distribution():
    payload = {
        "total_agents": 9,
        "real_agents": 7,
        "demo_agents": 2,
        "verified": 5,
        "unverified": 4,
        "registered_last_7_days": 2,
        "average_trust_score": 765,
        "grade_distribution": {"AAA": 1, "AA": 2, "A": 3, "BBB": 2, "BB": 1},
    }
    s = AdminStats.model_validate(payload)
    assert s.total_agents == 9
    assert s.grade_distribution["AAA"] == 1


def test_admin_recent():
    payload = {
        "recent_registrations": [
            {
                "air_id": "AIR-NEW1-XXXX-YYYY",
                "name": "FreshBot",
                "creator_did": "did:wba:example.com",
                "creator_name": "Example",
                "creator_type": "organization",
                "verified": False,
                "is_demo": False,
                "verification_level": "self-verified",
                "trust_score": 600,
                "trust_grade": "BBB",
                "registered": "2026-05-25T00:00:00.000Z",
            }
        ],
        "count": 1,
    }
    r = AdminRecent.model_validate(payload)
    assert r.count == 1
    assert r.recent_registrations[0].creator_name == "Example"


def test_error_envelope_with_retry_after():
    e = ErrorEnvelope.model_validate(
        {"error": "Rate limit exceeded", "retry_after_seconds": 3600}
    )
    assert e.retry_after_seconds == 3600


def test_error_envelope_ignores_unknown_fields():
    """extra='ignore' protects us when the API adds new error fields."""
    e = ErrorEnvelope.model_validate(
        {"error": "boom", "some_future_field": "ignored", "another": 42}
    )
    assert e.error == "boom"
