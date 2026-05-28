"""Pydantic v2 response models — field-mapped to the live API JSON shapes.

Each model corresponds to one endpoint response in api/src/index.js. Wire format
uses snake_case for most fields, BUT the W3C DID document endpoint uses camelCase
per the W3C DID Core spec. Pydantic aliases bridge the two so consumers always
write snake_case Python, regardless of which endpoint produced the data.

`extra="ignore"` lets the API add new fields without breaking existing SDK
versions — forward-compatibility wins over strictness here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class _AirModel(BaseModel):
    """Base config: accept both alias and field-name on input, ignore unknown fields."""

    model_config = ConfigDict(
        populate_by_name=True,
        extra="ignore",
    )


# ============================================================
# HEALTH
# ============================================================


class Health(_AirModel):
    """GET /api/v1/health response."""

    status: str
    version: str
    registry: str


# ============================================================
# TRUST SCORE
# ============================================================


class TrustComponents(_AirModel):
    """The five weighted components of an AIR trust score (0-1000 each).

    Weighting in the API: provenance 0.25, behavioral 0.25, transparency 0.20,
    security 0.15, peer_attestations 0.15. See api/src/index.js calculateInitialTrustScore.
    """

    provenance: int
    behavioral: int
    transparency: int
    security: int
    peer_attestations: int


class TrustScore(_AirModel):
    """GET /api/v1/agents/{air_id}/trust-score response."""

    air_id: str
    total_score: int
    grade: str  # AAA, AA, A, BBB, BB, B, C
    components: TrustComponents
    calculated_at: datetime


# ============================================================
# AGENT (full lookup)
# ============================================================


class Creator(_AirModel):
    """Creator block inside a full agent lookup. All fields may be empty strings or null."""

    did: str | None = None
    name: str | None = None
    type: str | None = None  # "individual" or "organization"
    public_key: str | None = None  # Ed25519, base64url-encoded


class _SecurityInfo(_AirModel):
    """Internal: maps the `security` object inside the agent lookup payload.

    Named with a leading underscore because the same word is also a field name
    in TrustComponents — keeping the class out of the public surface avoids
    import-name confusion for SDK consumers.
    """

    certifications: list[str] = Field(default_factory=list)


class _TransparencyInfo(_AirModel):
    """Internal: maps the `transparency` object inside the agent lookup payload."""

    open_source: bool = False
    code_repository: str | None = None
    documentation_url: str | None = None


class Agent(_AirModel):
    """GET /api/v1/agents/{air_id} response — full agent record with trust breakdown."""

    air_id: str
    name: str
    description: str = ""
    creator: Creator
    capabilities: list[str] = Field(default_factory=list)
    security: _SecurityInfo
    transparency: _TransparencyInfo
    verified: bool = False
    verification_level: str
    is_demo: bool = False
    status: str  # "active" or "deleted"
    created: datetime
    updated: datetime
    trust_score: int | None = None
    trust_grade: str | None = None
    components: TrustComponents | None = None

    # @context and type are JSON-LD framing fields. Capture them by alias
    # so callers can inspect them but Python field names stay valid.
    context: str | None = Field(default=None, alias="@context")
    type_: str | None = Field(default=None, alias="type")


# ============================================================
# AGENT LIST
# ============================================================


class AgentSummary(_AirModel):
    """One row in GET /api/v1/agents list response — fewer fields than a full Agent."""

    air_id: str
    name: str
    description: str = ""
    verified: bool = False
    verification_level: str
    is_demo: bool = False
    trust_score: int | None = None
    trust_grade: str | None = None
    created: datetime


class AgentList(_AirModel):
    """GET /api/v1/agents response — paginated list of AgentSummary."""

    agents: list[AgentSummary]
    total: int
    limit: int
    offset: int


# ============================================================
# DID DOCUMENT (W3C — note camelCase wire format)
# ============================================================


class DidVerificationMethod(_AirModel):
    """One entry in a DID document's verificationMethod array."""

    id: str
    type: str  # e.g. "Ed25519VerificationKey2020"
    controller: str
    public_key_multibase: str = Field(alias="publicKeyMultibase")


class DidService(_AirModel):
    """One entry in a DID document's service array."""

    id: str
    type: str  # e.g. "AIRTrustScore"
    service_endpoint: str = Field(alias="serviceEndpoint")


class DidDocument(_AirModel):
    """GET /api/v1/agents/{air_id}/did-document response — W3C DID Core JSON-LD.

    Wire format uses camelCase per the W3C spec. Python access is snake_case
    via Pydantic aliases.
    """

    context: list[str] = Field(alias="@context")
    id: str
    also_known_as: list[str] | None = Field(default=None, alias="alsoKnownAs")
    verification_method: list[DidVerificationMethod] = Field(alias="verificationMethod")
    authentication: list[str]
    assertion_method: list[str] = Field(alias="assertionMethod")
    service: list[DidService] = Field(default_factory=list)


# ============================================================
# SERVICE (A2A spec §12 — W3C DID Core service entry)
# ============================================================


class Service(BaseModel):
    """A W3C DID Core service entry — describes an endpoint at which an agent can be reached
    for a specific service type.

    Per A2A spec §12, `type` is an open string with reserved prefixes (A2A*, AIR*, DIDComm*).
    Custom service types should use namespaced-URI form to avoid collisions.

    Unlike the rest of the SDK models, `Service` uses `extra='forbid'` to match A2A spec
    strictness — unknown fields in a service entry are a contract violation, not a forward-
    compatibility signal.
    """

    id: Optional[str] = Field(default=None, description="W3C did-document fragment ID (e.g. '#a2a')")
    type: str = Field(description="Service type — e.g. 'A2AInbox', 'AIRTrustScore'")
    serviceEndpoint: HttpUrl = Field(description="The URL at which this service is reached")

    model_config = ConfigDict(extra="forbid")  # strict — reject unknown fields


# ============================================================
# AGENT RECORD (v0.4.0+: service_endpoints field added)
# ============================================================


class AgentRecord(_AirModel):
    """Unified agent record that carries both the core identity fields and, from SDK v0.4.0
    onwards, an optional list of A2A service endpoints.

    `service_endpoints` is absent from JSON when None (serialization_alias not set;
    use `model.model_dump(exclude_none=True)` for clean round-trips).
    """

    air_id: str
    name: str
    service_endpoints: Optional[List[Service]] = Field(
        default=None,
        description="A2A service endpoint list (added in SDK v0.4.0, A2A spec §12).",
    )


# ============================================================
# NAME CHECK
# ============================================================


class ExistingAgent(_AirModel):
    """One match in the name-check response."""

    air_id: str
    name: str


class NameCheck(_AirModel):
    """GET /api/v1/agents/check-name response."""

    name: str
    exists: bool
    count: int
    existing_agents: list[ExistingAgent] = Field(default_factory=list)


# ============================================================
# REGISTRATION
# ============================================================


class RegistrationResult(_AirModel):
    """POST /api/v1/agents/register 201 response.

    `agent_secret` is shown ONLY ONCE — store it before the program exits
    or you'll need an admin DELETE + re-register to recover.
    """

    air_id: str
    name: str
    creator_did: str
    air_minted_did: bool | None = None  # True if AIR generated the did:wba
    status: str  # "active"
    verification_level: str
    trust_score: int
    trust_grade: str
    created: datetime
    public_key: str | None = None
    did_wba_resolved: bool | None = None  # None unless creator_did is did:wba
    agent_secret: str
    agent_secret_note: str
    warnings: list[str] | None = None
    message: str


# ============================================================
# UPDATE
# ============================================================


class UpdateResult(_AirModel):
    """PUT /api/v1/agents/{air_id} response."""

    air_id: str
    updated_fields: int
    trust_score: int
    trust_grade: str
    updated: datetime
    message: str


# ============================================================
# DELETE
# ============================================================


class DeleteResult(_AirModel):
    """DELETE /api/v1/agents/{air_id} response (admin only, soft-delete)."""

    air_id: str
    name: str
    status: str  # "deleted"
    deleted_at: datetime
    message: str


# ============================================================
# ADMIN
# ============================================================


class AdminStats(_AirModel):
    """GET /api/v1/admin/stats response (admin-only)."""

    total_agents: int
    real_agents: int
    demo_agents: int
    verified: int
    unverified: int
    registered_last_7_days: int
    average_trust_score: int | None = None
    grade_distribution: dict[str, int] = Field(default_factory=dict)


class AdminRecentItem(_AirModel):
    """One entry in GET /api/v1/admin/recent response."""

    air_id: str
    name: str
    creator_did: str | None = None
    creator_name: str
    creator_type: str | None = None
    verified: bool = False
    is_demo: bool = False
    verification_level: str
    trust_score: int | None = None
    trust_grade: str | None = None
    registered: datetime


class AdminRecent(_AirModel):
    """GET /api/v1/admin/recent response wrapper."""

    recent_registrations: list[AdminRecentItem]
    count: int


# ============================================================
# ERROR ENVELOPE (used by exceptions, not normally surfaced)
# ============================================================


class ErrorEnvelope(_AirModel):
    """Common error response shape used across all endpoints.

    Not every field appears on every error — `extra="ignore"` on _AirModel
    means the SDK silently absorbs whatever the API ships now or in the future.
    """

    error: str
    message: str | None = None
    air_id: str | None = None
    hint: str | None = None
    retry_after_seconds: int | None = None
    raw: dict[str, Any] | None = None  # Populated by the client when JSON parse fails
