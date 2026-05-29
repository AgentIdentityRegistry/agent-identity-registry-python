"""Ed25519 attestation signing — the client side of AIR Verified (Phase 4).

To create an attestation, the *attester* signs a canonical payload with their
Ed25519 private key. The registry's Worker re-derives the exact same bytes and
verifies the signature against the attester's published public key (Lock 1).
If the SDK produces even one different byte, verification fails.

The contract (must byte-match `jcsCanonicalize` in api/src/index.js):

  1. Build the object {attester_air_id, attestation_type, signed_at, statement,
     subject_air_id}. `statement` is ALWAYS included — empty string if absent.
  2. JCS-canonicalize: sort keys, no whitespace, JSON-escape each string.
     NOTE: unlike the A2A envelope path, the attestation payload is NOT
     NFC-normalized — the Worker's jcsCanonicalize doesn't normalize, so neither
     do we. Matching the Worker is the only correctness criterion.
  3. Ed25519-sign the UTF-8 bytes → 64-byte raw signature.
  4. multibase-encode: "z" + base58btc(signature).

`cryptography` is an OPTIONAL dependency (the `[signing]` extra). Read-only
users keep the tiny httpx+pydantic footprint; only signers pull it in. Every
public function here raises a friendly ImportError if it's missing.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

# Bitcoin/IPFS base58 alphabet — identical to BASE58_ALPHABET in the Worker.
_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

# The five fields that get signed, and the only valid attestation types.
# Kept here so callers can validate before a round-trip (the Worker also checks).
VALID_ATTESTATION_TYPES = frozenset(
    {
        "identity_verification",
        "operator_confirmation",
        "dependency",
        "safety_review",
    }
)


def _require_cryptography() -> None:
    """Raise a friendly, actionable error if the `[signing]` extra isn't installed."""
    try:
        import cryptography  # noqa: F401
    except ImportError as e:  # pragma: no cover - exercised via import shim in tests
        raise ImportError(
            "Attestation signing needs the 'cryptography' package. "
            "Install the signing extra:\n\n"
            "    pip install 'agent-identity-registry[signing]'\n"
        ) from e


def _base58btc_encode(data: bytes) -> str:
    """Encode raw bytes as base58btc (no multibase prefix).

    Mirrors the Worker's base58 so a "z"-prefixed result round-trips through
    its base58Decode(). Leading zero bytes map to leading "1"s.
    """
    if not data:
        return ""
    # Count leading zero bytes — each becomes a literal "1".
    leading_zeros = 0
    for byte in data:
        if byte == 0:
            leading_zeros += 1
        else:
            break
    # base256 -> base58 via big-integer division.
    num = int.from_bytes(data, "big")
    out: list[str] = []
    while num > 0:
        num, rem = divmod(num, 58)
        out.append(_BASE58_ALPHABET[rem])
    out.append("1" * leading_zeros)
    return "".join(reversed(out))


def canonical_attestation_bytes(
    *,
    attester_air_id: str,
    attestation_type: str,
    signed_at: str,
    subject_air_id: str,
    statement: str = "",
) -> bytes:
    """Produce the exact JCS-canonical bytes the attester must sign.

    Byte-for-byte identical to the Worker's
    `jcsCanonicalize({attester_air_id, attestation_type, signed_at, statement,
    subject_air_id})`: keys sorted, no whitespace, each string JSON-escaped with
    non-ASCII emitted raw (ensure_ascii=False, matching JS JSON.stringify).

    All five values are strings, so there are no float/large-int JCS hazards
    here — but we still build the string by hand (rather than json.dumps on the
    whole dict) to guarantee key ordering and separator-free output regardless
    of the local json defaults.
    """
    payload = {
        "attester_air_id": attester_air_id,
        "attestation_type": attestation_type,
        "signed_at": signed_at,
        "statement": statement,
        "subject_air_id": subject_air_id,
    }
    parts = [
        json.dumps(key) + ":" + json.dumps(payload[key], ensure_ascii=False)
        for key in sorted(payload)
    ]
    return ("{" + ",".join(parts) + "}").encode("utf-8")


def sign_attestation(
    private_key: Ed25519PrivateKey,
    *,
    attester_air_id: str,
    attestation_type: str,
    signed_at: str,
    subject_air_id: str,
    statement: str = "",
) -> str:
    """Sign an attestation payload and return the `signature_multibase` string.

    Returns "z" + base58btc(64-byte Ed25519 signature) — the exact format the
    Worker's verifyEd25519Signature() expects.

    Validates `attestation_type` client-side so an obvious typo fails fast
    instead of after a network round-trip + a confusing 400.
    """
    _require_cryptography()
    if attestation_type not in VALID_ATTESTATION_TYPES:
        raise ValueError(
            f"invalid attestation_type {attestation_type!r}; "
            f"must be one of: {', '.join(sorted(VALID_ATTESTATION_TYPES))}"
        )
    payload_bytes = canonical_attestation_bytes(
        attester_air_id=attester_air_id,
        attestation_type=attestation_type,
        signed_at=signed_at,
        subject_air_id=subject_air_id,
        statement=statement,
    )
    signature = private_key.sign(payload_bytes)  # raw 64-byte Ed25519 signature
    return "z" + _base58btc_encode(signature)


def load_private_key_from_seed(seed: bytes | str) -> Ed25519PrivateKey:
    """Load an Ed25519 private key from a 32-byte seed (raw bytes or hex string).

    The seed is the canonical 32-byte Ed25519 private scalar — the same value
    other AIR tooling and the conformance harness use. Hex strings are decoded;
    raw bytes are used as-is.
    """
    _require_cryptography()
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    seed_bytes = bytes.fromhex(seed) if isinstance(seed, str) else seed
    if len(seed_bytes) != 32:
        raise ValueError(f"Ed25519 seed must be 32 bytes, got {len(seed_bytes)}")
    return Ed25519PrivateKey.from_private_bytes(seed_bytes)
