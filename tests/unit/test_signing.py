"""Tests for attestation signing — the bytes here MUST match the Worker.

The canonical-bytes test is the contract: if it ever changes, every signature
the SDK produces will be rejected by the registry. The roundtrip test proves an
independent Ed25519 verifier (same primitive the Worker uses) accepts what we
sign.
"""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent_identity_registry import (
    canonical_attestation_bytes,
    load_private_key_from_seed,
    sign_attestation,
)

# base58btc alphabet — used by the test-local decoder below to invert the
# SDK's encoder so we can hand raw signature bytes to a real Ed25519 verifier.
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58decode(s: str) -> bytes:
    num = 0
    for ch in s:
        num = num * 58 + _B58.index(ch)
    pad = len(s) - len(s.lstrip("1"))
    body = num.to_bytes((num.bit_length() + 7) // 8, "big") if num else b""
    return b"\x00" * pad + body


# ----- canonical bytes: the byte-exact contract with the Worker -----------


def test_canonical_bytes_byte_exact() -> None:
    """Keys sorted, no whitespace, statement included — must match jcsCanonicalize."""
    got = canonical_attestation_bytes(
        attester_air_id="AIR-ATT1-ATT1-ATT1",
        attestation_type="identity_verification",
        signed_at="2026-05-29T00:00:00Z",
        subject_air_id="AIR-SUB1-SUB1-SUB1",
        statement="I verify this agent",
    )
    expected = (
        b'{"attestation_type":"identity_verification",'
        b'"attester_air_id":"AIR-ATT1-ATT1-ATT1",'
        b'"signed_at":"2026-05-29T00:00:00Z",'
        b'"statement":"I verify this agent",'
        b'"subject_air_id":"AIR-SUB1-SUB1-SUB1"}'
    )
    assert got == expected


def test_canonical_bytes_empty_statement_is_present() -> None:
    """An omitted statement defaults to "" and is STILL part of the signed payload."""
    got = canonical_attestation_bytes(
        attester_air_id="AIR-A",
        attestation_type="dependency",
        signed_at="2026-05-29T00:00:00Z",
        subject_air_id="AIR-B",
    )
    assert b'"statement":""' in got


def test_canonical_bytes_unicode_statement_stays_raw() -> None:
    """Non-ASCII is emitted raw (ensure_ascii=False), matching JS JSON.stringify."""
    got = canonical_attestation_bytes(
        attester_air_id="AIR-A",
        attestation_type="safety_review",
        signed_at="2026-05-29T00:00:00Z",
        subject_air_id="AIR-B",
        statement="café ☕ reviewed",
    )
    assert "café ☕ reviewed".encode() in got


# ----- full sign -> verify roundtrip (the gold safety net) ----------------


def test_sign_attestation_roundtrips_through_ed25519_verify() -> None:
    """A real Ed25519 verifier accepts the signature over the canonical bytes."""
    key = load_private_key_from_seed(bytes(range(32)))  # deterministic 32-byte seed
    kwargs = dict(
        attester_air_id="AIR-ATT1-ATT1-ATT1",
        attestation_type="operator_confirmation",
        signed_at="2026-05-29T12:00:00Z",
        subject_air_id="AIR-SUB1-SUB1-SUB1",
        statement="ships in prod",
    )
    sig_mb = sign_attestation(key, **kwargs)

    assert sig_mb.startswith("z")
    sig = _b58decode(sig_mb[1:])
    assert len(sig) == 64  # Ed25519 signatures are always 64 bytes

    # Raises cryptography.exceptions.InvalidSignature if the bytes don't match.
    key.public_key().verify(sig, canonical_attestation_bytes(**kwargs))


def test_sign_attestation_rejects_bad_type() -> None:
    key = load_private_key_from_seed(bytes(range(32)))
    with pytest.raises(ValueError, match="invalid attestation_type"):
        sign_attestation(
            key,
            attester_air_id="AIR-A",
            attestation_type="not_a_real_type",
            signed_at="2026-05-29T00:00:00Z",
            subject_air_id="AIR-B",
        )


# ----- key loading --------------------------------------------------------


def test_load_private_key_from_hex_and_bytes_match() -> None:
    seed = bytes(range(32))
    from_bytes = load_private_key_from_seed(seed)
    from_hex = load_private_key_from_seed(seed.hex())
    # Same seed → same public key.
    assert _pub_raw(from_bytes) == _pub_raw(from_hex)
    assert isinstance(from_bytes, Ed25519PrivateKey)


def test_load_private_key_rejects_wrong_length() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        load_private_key_from_seed(b"too short")


def _pub_raw(key: Ed25519PrivateKey) -> bytes:
    from cryptography.hazmat.primitives import serialization

    return key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
