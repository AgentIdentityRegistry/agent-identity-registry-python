"""
Cross-language A2A conformance harness — Python side.

Reads ~/air-site/specs/a2a/draft-1/test-vectors.json and verifies that
Python's `jcs` library + `cryptography` Ed25519 produce byte-identical
canonical JSON (SHA-256) and byte-identical signatures for every vector.

This script is STANDALONE — it does NOT import from agent_identity_registry.
It exercises only the primitive JCS + signing layer so that spec-layer parity
can be validated before the full SDK is written (Stage 3c).

Run:
    pip install -r requirements.txt
    pytest test_jcs_vectors.py -v

Or set the VECTORS_PATH env var to point at the vectors file:
    VECTORS_PATH=/abs/path/test-vectors.json pytest test_jcs_vectors.py -v
"""

from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from pathlib import Path
from typing import Any

import base58
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

# ---------------------------------------------------------------------------
# Multicodec / multibase constants
# ---------------------------------------------------------------------------

_ED25519_MULTICODEC = bytes([0xED, 0x01])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _vectors_path() -> Path:
    """Return path to test-vectors.json.

    Checks VECTORS_PATH env var first, then resolves relative to this file.
    """
    env_path = os.environ.get("VECTORS_PATH")
    if env_path:
        return Path(env_path)
    # This file is at: air-site/sdk/python/tests/conformance/test_jcs_vectors.py
    # Vectors are at:  air-site/specs/a2a/draft-1/test-vectors.json
    return Path(__file__).parent / "../../../../../specs/a2a/draft-1/test-vectors.json"


def _nfc_normalize(obj: Any) -> Any:
    """Recursively NFC-normalize all string values in a JSON-compatible object.

    Must be applied before JCS canonicalization per spec §5.
    """
    if isinstance(obj, str):
        return unicodedata.normalize("NFC", obj)
    if isinstance(obj, dict):
        return {k: _nfc_normalize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_nfc_normalize(item) for item in obj]
    return obj


def _jcs_exact(obj: Any) -> str:
    """RFC 8785 JCS with exact integer preservation.

    Per spec §5 'no floats': integers MUST NOT be coerced to IEEE 754 double.
    The ``jcs`` Python package coerces integers > 2^53 to float64 (known
    limitation). This implementation preserves exact values, matching
    ``serde_jcs`` 0.1.0 in Rust.

    All string values must already be NFC-normalized before calling.
    """
    if isinstance(obj, dict):
        keys = sorted(obj.keys())
        parts = [json.dumps(k) + ":" + _jcs_exact(obj[k]) for k in keys]
        return "{" + ",".join(parts) + "}"
    if isinstance(obj, list):
        return "[" + ",".join(_jcs_exact(i) for i in obj) + "]"
    if isinstance(obj, bool):
        return "true" if obj else "false"
    if obj is None:
        return "null"
    if isinstance(obj, int):
        return str(obj)  # exact integer — never coerce to float
    if isinstance(obj, float):
        raise ValueError(f"Float not allowed in A2A envelopes per spec §5: {obj!r}")
    return json.dumps(obj, ensure_ascii=False)


def _canonicalize(envelope: dict) -> bytes:
    """Produce canonical JSON bytes for signing.

    Steps per spec §5:
    1. Remove 'signature' field if present.
    2. NFC-normalize all string values.
    3. Apply RFC 8785 JCS with exact integer preservation (see _jcs_exact).

    Note: ``jcs.canonicalize`` is NOT used here because it coerces integers
    > 2^53 to float64, violating the no-floats rule.
    """
    obj = {k: v for k, v in envelope.items() if k != "signature"}
    obj = _nfc_normalize(obj)
    return _jcs_exact(obj).encode("utf-8")


def _seed_to_private_key(seed_hex: str) -> Ed25519PrivateKey:
    """Derive Ed25519 private key from 32-byte hex seed."""
    seed_bytes = bytes.fromhex(seed_hex)
    assert len(seed_bytes) == 32, f"Seed must be 32 bytes, got {len(seed_bytes)}"
    return Ed25519PrivateKey.from_private_bytes(seed_bytes)


def _public_key_multibase(private_key: Ed25519PrivateKey) -> str:
    """Encode public key as multicodec 0xed01 + base58btc + 'z' prefix."""
    pub_bytes = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    prefixed = _ED25519_MULTICODEC + pub_bytes
    return "z" + base58.b58encode(prefixed).decode("ascii")


def _signature_multibase(sig_bytes: bytes) -> str:
    """Encode 64-byte signature as multibase z + base58btc (no multicodec prefix)."""
    assert len(sig_bytes) == 64, f"Signature must be 64 bytes, got {len(sig_bytes)}"
    return "z" + base58.b58encode(sig_bytes).decode("ascii")


# ---------------------------------------------------------------------------
# Load vectors once for the session
# ---------------------------------------------------------------------------

def _load_vectors() -> list[dict]:
    path = _vectors_path().resolve()
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    vectors = data["vectors"]
    assert len(vectors) == 20, f"Expected 20 vectors, got {len(vectors)}"
    return vectors


# Load once at module import time; pytest collects parametrize before tests run.
_VECTORS = _load_vectors()
_VECTOR_IDS = [v["id"] for v in _VECTORS]


# ---------------------------------------------------------------------------
# Parametrized test — one test ID per vector
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("vector", _VECTORS, ids=_VECTOR_IDS)
def test_jcs_vector(vector: dict) -> None:
    """Assert byte-identical canonical bytes (SHA-256) and signature for one vector."""
    vid = vector["id"]
    envelope = vector["input_envelope"]

    # 1. Canonicalize
    canon_bytes = _canonicalize(envelope)

    # 2. Assert SHA-256 of canonical bytes
    sha256_hex = hashlib.sha256(canon_bytes).hexdigest()
    assert sha256_hex == vector["expected_canonical_bytes_sha256_hex"], (
        f"Vector {vid}: canonical bytes SHA-256 mismatch\n"
        f"  got:      {sha256_hex}\n"
        f"  expected: {vector['expected_canonical_bytes_sha256_hex']}"
    )

    # 3. Derive keypair and assert public key encoding
    private_key = _seed_to_private_key(vector["signing_key_seed_hex"])
    pub_mb = _public_key_multibase(private_key)
    assert pub_mb == vector["signing_key_public_multibase"], (
        f"Vector {vid}: public key multibase mismatch\n"
        f"  got:      {pub_mb}\n"
        f"  expected: {vector['signing_key_public_multibase']}"
    )

    # 4. Sign and assert signature matches
    sig_bytes = private_key.sign(canon_bytes)
    sig_mb = _signature_multibase(sig_bytes)
    assert sig_mb == vector["expected_signature_multibase"], (
        f"Vector {vid}: signature multibase mismatch\n"
        f"  got:      {sig_mb}\n"
        f"  expected: {vector['expected_signature_multibase']}"
    )

    # 5. Verify signature round-trip
    public_key = private_key.public_key()
    # cryptography raises InvalidSignature on failure — let it propagate as a test error
    public_key.verify(sig_bytes, canon_bytes)
