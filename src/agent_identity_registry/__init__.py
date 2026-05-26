"""Async Python SDK for the Agent Identity Registry (AIR).

Public surface: import from the package root. Internal modules
(client, models, exceptions) are re-exported here so consumers
never need to know the file layout.

Example:
    from agent_identity_registry import AIRClient, AgentNotFoundError

    async with AIRClient() as client:
        agent = await client.get_agent("AIR-XXXX-XXXX-XXXX")
"""

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
    AgentSummary,
    Creator,
    DidDocument,
    DidVerificationMethod,
    Health,
    NameCheck,
    RegistrationResult,
    TrustComponents,
    TrustScore,
    UpdateResult,
)

__version__ = "0.1.0"
__all__ = [
    "AIRClient",
    "AdminRecentItem",
    "AdminStats",
    "Agent",
    "AgentNotFoundError",
    "AgentSummary",
    "AirError",
    "AuthenticationError",
    "ConflictError",
    "Creator",
    "DidDocument",
    "DidVerificationMethod",
    "Health",
    "NameCheck",
    "NetworkError",
    "RateLimitedError",
    "RegistrationResult",
    "ServerError",
    "TrustComponents",
    "TrustScore",
    "UpdateResult",
    "ValidationError",
]
