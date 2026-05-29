"""Tests for the attestation client methods against httpx.MockTransport."""

from __future__ import annotations

import json as _json

import httpx

from agent_identity_registry import AIRClient, load_private_key_from_seed
from agent_identity_registry.signing import canonical_attestation_bytes

# Reusable verified_status block (raw computeVerifiedStatus shape).
_VS = {
    "verified": False,
    "verification_score": 120,
    "distinct_whois_roots": 1,
    "attestation_count": 1,
}


def _b58decode(s: str) -> bytes:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    num = 0
    for ch in s:
        num = num * 58 + alphabet.index(ch)
    pad = len(s) - len(s.lstrip("1"))
    return b"\x00" * pad + (num.to_bytes((num.bit_length() + 7) // 8, "big") if num else b"")


async def test_create_attestation_sends_body_and_attester_secret() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["secret"] = request.headers.get("X-Agent-Secret")
        captured["body"] = _json.loads(request.content)
        return httpx.Response(
            201,
            json={
                "attestation_id": 7,
                "subject_air_id": "AIR-SUB1-SUB1-SUB1",
                "attester_air_id": "AIR-ATT1-ATT1-ATT1",
                "attestation_type": "identity_verification",
                "statement": "verified",
                "signed_at": "2026-05-29T00:00:00Z",
                "attester_whois_root": "example.com",
                "attester_trust_at_issue": 600,
                "tenure_multiplier_at_issue": 1.0,
                "weight": 600.0,
                "verified_status": _VS,
            },
        )

    async with AIRClient(
        base_url="https://test.invalid", transport=httpx.MockTransport(handler)
    ) as c:
        result = await c.create_attestation(
            "AIR-SUB1-SUB1-SUB1",
            attester_air_id="AIR-ATT1-ATT1-ATT1",
            attestation_type="identity_verification",
            signed_at="2026-05-29T00:00:00Z",
            signature_multibase="zSIGNATURE",
            agent_secret="attester-secret",
            statement="verified",
        )

    assert captured["secret"] == "attester-secret"
    assert captured["body"]["attester_air_id"] == "AIR-ATT1-ATT1-ATT1"
    assert captured["body"]["signature_multibase"] == "zSIGNATURE"
    assert captured["body"]["statement"] == "verified"
    assert result.attestation_id == 7
    assert result.weight == 600.0
    assert result.verified_status.verification_score == 120


async def test_attest_signs_canonical_bytes_and_submits() -> None:
    """High-level attest(): the signature in the POST body must verify."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = _json.loads(request.content)
        captured["path"] = request.url.path
        return httpx.Response(
            201,
            json={
                "attestation_id": 1,
                "subject_air_id": "AIR-SUB1-SUB1-SUB1",
                "attester_air_id": "AIR-ATT1-ATT1-ATT1",
                "attestation_type": "dependency",
                "statement": "",
                "signed_at": captured.get("signed_at", "2026-05-29T00:00:00Z"),
                "attester_whois_root": "example.com",
                "attester_trust_at_issue": 600,
                "tenure_multiplier_at_issue": 1.0,
                "weight": 600.0,
                "verified_status": _VS,
            },
        )

    key = load_private_key_from_seed(bytes(range(32)))
    async with AIRClient(
        base_url="https://test.invalid", transport=httpx.MockTransport(handler)
    ) as c:
        await c.attest(
            "AIR-SUB1-SUB1-SUB1",
            attester_air_id="AIR-ATT1-ATT1-ATT1",
            attestation_type="dependency",
            private_key=key,
            agent_secret="s",
            signed_at="2026-05-29T00:00:00Z",
        )

    body = captured["body"]
    assert captured["path"] == "/api/v1/agents/AIR-SUB1-SUB1-SUB1/attestations"
    assert body["signature_multibase"].startswith("z")
    # The signature the SDK sent must verify over the canonical bytes.
    sig = _b58decode(body["signature_multibase"][1:])
    canonical = canonical_attestation_bytes(
        attester_air_id="AIR-ATT1-ATT1-ATT1",
        attestation_type="dependency",
        signed_at="2026-05-29T00:00:00Z",
        subject_air_id="AIR-SUB1-SUB1-SUB1",
        statement="",
    )
    key.public_key().verify(sig, canonical)  # raises if mismatch


async def test_attest_defaults_signed_at_to_now_utc() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = _json.loads(request.content)
        return httpx.Response(
            201,
            json={
                "attestation_id": 1,
                "subject_air_id": "AIR-B",
                "attester_air_id": "AIR-A",
                "attestation_type": "safety_review",
                "statement": "",
                "signed_at": captured["body"]["signed_at"],
                "attester_whois_root": None,
                "attester_trust_at_issue": 500,
                "tenure_multiplier_at_issue": 0.5,
                "weight": 250.0,
                "verified_status": _VS,
            },
        )

    key = load_private_key_from_seed(bytes(range(32)))
    async with AIRClient(
        base_url="https://test.invalid", transport=httpx.MockTransport(handler)
    ) as c:
        await c.attest(
            "AIR-B",
            attester_air_id="AIR-A",
            attestation_type="safety_review",
            private_key=key,
            agent_secret="s",
        )

    # Default is ISO-8601 UTC with a trailing Z.
    assert captured["body"]["signed_at"].endswith("Z")
    assert "T" in captured["body"]["signed_at"]


async def test_list_attestations_parses_audit_trail(mock_client) -> None:
    client = mock_client(
        {
            ("GET", "/api/v1/agents/AIR-SUB1-SUB1-SUB1/attestations"): (
                200,
                {
                    "subject_air_id": "AIR-SUB1-SUB1-SUB1",
                    "attestations": [
                        {
                            "id": 1,
                            "attester_air_id": "AIR-ATT1-ATT1-ATT1",
                            "attester_whois_root": "example.com",
                            "attestation_type": "identity_verification",
                            "statement": "ok",
                            "signed_payload": "{}",
                            "signature_multibase": "zABC",
                            "signed_at": "2026-05-29T00:00:00Z",
                            "attester_trust_at_issue": 600,
                            "tenure_multiplier_at_issue": 1.0,
                            "weight": 600.0,
                            "revoked_at": None,
                            "is_active": True,
                            "created_at": "2026-05-29T00:00:01Z",
                        }
                    ],
                    "total": 1,
                    "active": 1,
                    "verified_status": _VS,
                },
            )
        }
    )
    async with client as c:
        lst = await c.list_attestations("AIR-SUB1-SUB1-SUB1")
    assert lst.total == 1
    assert lst.attestations[0].is_active is True
    assert lst.attestations[0].weight == 600.0


async def test_recent_attestations_sends_limit(mock_handler) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"attestations": [], "total": 0, "limit": 200})

    async with AIRClient(
        base_url="https://test.invalid", transport=httpx.MockTransport(handler)
    ) as c:
        recent = await c.recent_attestations(limit=200)
    assert captured["params"] == {"limit": "200"}
    assert recent.limit == 200


async def test_revoke_attestation_sends_secret(mock_handler) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["secret"] = request.headers.get("X-Agent-Secret")
        captured["path"] = request.url.path
        return httpx.Response(
            200,
            json={
                "revoked": True,
                "attestation_id": 7,
                "subject_air_id": "AIR-SUB1-SUB1-SUB1",
                "revoked_at": "2026-05-29T01:00:00Z",
                "verified_status": _VS,
            },
        )

    async with AIRClient(
        base_url="https://test.invalid", transport=httpx.MockTransport(handler)
    ) as c:
        result = await c.revoke_attestation(
            "AIR-SUB1-SUB1-SUB1", 7, agent_secret="attester-secret"
        )

    assert captured["method"] == "DELETE"
    assert captured["secret"] == "attester-secret"
    assert captured["path"] == "/api/v1/agents/AIR-SUB1-SUB1-SUB1/attestations/7"
    assert result.revoked is True


async def test_get_agent_parses_verification_status(mock_client) -> None:
    """The extended GET /agents/{id} verification_status block round-trips."""
    client = mock_client(
        {
            ("GET", "/api/v1/agents/AIR-X"): (
                200,
                {
                    "air_id": "AIR-X",
                    "name": "Bot",
                    "creator": {"did": None, "name": None, "type": None, "public_key": None},
                    "capabilities": [],
                    "security": {"certifications": []},
                    "transparency": {"open_source": False},
                    "verified": True,
                    "verification_level": "attested",
                    "verification_status": {
                        "verified": True,
                        "score": 420,
                        "score_required": 300,
                        "attestation_count": 3,
                        "distinct_whois_roots": 3,
                        "distinct_whois_roots_required": 3,
                    },
                    "is_demo": False,
                    "status": "active",
                    "created": "2026-05-29T00:00:00Z",
                    "updated": "2026-05-29T00:00:00Z",
                },
            )
        }
    )
    async with client as c:
        agent = await c.get_agent("AIR-X")
    assert agent.verification_status is not None
    assert agent.verification_status.verified is True
    assert agent.verification_status.score == 420
    assert agent.verification_status.distinct_whois_roots_required == 3
