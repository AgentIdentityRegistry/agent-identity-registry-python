"""Async Python SDK for the Agent Identity Registry (AIR).

Public surface: import from the package root. Internal modules
(client, models, exceptions) are re-exported here so consumers
never need to know the file layout.

Example:
    from agent_identity_registry import AIRClient, AgentNotFoundError

    async with AIRClient() as client:
        agent = await client.get_agent("AIR-XXXX-XXXX-XXXX")
"""

from agent_identity_registry._retry import RetryConfig
from agent_identity_registry.client import AIRClient
from agent_identity_registry.exceptions import (
    AgentNotFoundError,
    AirError,
    AuthenticationError,
    ConflictError,
    NetworkError,
    RateLimitedError,
    ServerError,
    ValidationError,
)
from agent_identity_registry.models import (
    AdminRecentItem,
    AdminStats,
    Agent,
    AgentRecord,
    AgentSummary,
    Attestation,
    AttestationList,
    AttestationResult,
    Creator,
    DidDocument,
    DidVerificationMethod,
    Health,
    NameCheck,
    RecentAttestation,
    RecentAttestations,
    RegistrationResult,
    RevokeResult,
    Service,
    TrustComponents,
    TrustScore,
    UpdateResult,
    VerificationStatus,
    VerifiedStatus,
)
from agent_identity_registry.signing import (
    VALID_ATTESTATION_TYPES,
    canonical_attestation_bytes,
    load_private_key_from_seed,
    sign_attestation,
)

__version__ = "0.5.0"
__all__ = [
    "VALID_ATTESTATION_TYPES",
    "AIRClient",
    "AdminRecentItem",
    "AdminStats",
    "Agent",
    "AgentNotFoundError",
    "AgentRecord",
    "AgentSummary",
    "AirError",
    "Attestation",
    "AttestationList",
    "AttestationResult",
    "AuthenticationError",
    "ConflictError",
    "Creator",
    "DidDocument",
    "DidVerificationMethod",
    "Health",
    "NameCheck",
    "NetworkError",
    "RateLimitedError",
    "RecentAttestation",
    "RecentAttestations",
    "RegistrationResult",
    "RetryConfig",
    "RevokeResult",
    "ServerError",
    "Service",
    "TrustComponents",
    "TrustScore",
    "UpdateResult",
    "ValidationError",
    "VerificationStatus",
    "VerifiedStatus",
    "canonical_attestation_bytes",
    "load_private_key_from_seed",
    "sign_attestation",
]
