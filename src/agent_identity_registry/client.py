"""Async HTTP client for the AIR registry.

One class — `AIRClient` — covers all 11 endpoints. Async-only for v1
(per build plan §134-143). Use as an async context manager so the
underlying httpx client is closed deterministically:

    async with AIRClient() as client:
        agent = await client.get_agent("AIR-XXXX-XXXX-XXXX")

Constructor knobs:
    base_url      — defaults to the production registry
    admin_key     — required for delete_agent / get_admin_stats / get_admin_recent
    timeout       — per-request HTTP timeout (default 30s)
    transport     — inject httpx.MockTransport in tests; None in production
    retry_config  — retry policy on transient failures. Default ON since v0.3:
                    3 retries with exponential backoff + jitter, retries 429/
                    502/503/504 and network errors on idempotent methods, but
                    NEVER retries network errors on POST (no dupe registrations).
                    Pass `retry_config=None` to disable retries entirely.
                    Pass a customized `RetryConfig(...)` to tune the policy.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any

import httpx

from agent_identity_registry._retry import RetryConfig, RetryTransport
from agent_identity_registry.exceptions import (
    AuthenticationError,
    NetworkError,
    raise_for_status,
)
from agent_identity_registry.models import (
    AdminRecent,
    AdminStats,
    Agent,
    AgentList,
    DeleteResult,
    DidDocument,
    ErrorEnvelope,
    Health,
    NameCheck,
    RegistrationResult,
    TrustScore,
    UpdateResult,
)

DEFAULT_BASE_URL = "https://agentidentityregistry.org"
API_PREFIX = "/api/v1"


class AIRClient:
    """Async client for the Agent Identity Registry HTTP API."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        admin_key: str | None = None,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_config: RetryConfig | None = RetryConfig(),
    ) -> None:
        # Strip trailing slash so URL joining stays predictable.
        self._base_url = base_url.rstrip("/")
        self._admin_key = admin_key
        # Compose retry on top of whatever transport was passed (or default).
        # `retry_config=None` is the explicit opt-out — no wrapper at all.
        # Default = sensible auto-retry (see RetryConfig docstring).
        # Using a frozen-dataclass default is safe because RetryConfig is immutable.
        inner_transport = transport if transport is not None else httpx.AsyncHTTPTransport()
        if retry_config is not None and retry_config.max_retries > 0:
            inner_transport = RetryTransport(inner_transport, retry_config)
        self._client = httpx.AsyncClient(
            base_url=self._base_url + API_PREFIX,
            timeout=timeout,
            transport=inner_transport,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> AIRClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        air_id: str | None = None,
    ) -> dict[str, Any]:
        """Issue one HTTP request, parse JSON, raise on error.

        Returns the parsed JSON body on success (always a dict for this API).
        Raises `NetworkError` on transport failures, or an `AirError` subclass
        per `raise_for_status` on 4xx/5xx.
        """
        try:
            response = await self._client.request(
                method,
                path,
                params=params,
                json=json,
                headers=headers,
            )
        except httpx.HTTPError as e:
            # Transport-layer failure: timeout, DNS, TLS, connection refused, etc.
            # Chain the original exception so callers can introspect via __cause__.
            raise NetworkError(f"Request failed: {e}") from e

        # Parse body. Every AIR endpoint returns JSON (success and error),
        # but be defensive against empty bodies just in case.
        body_text = response.text
        parsed: dict[str, Any] | None
        if body_text:
            try:
                parsed = response.json()
            except ValueError:
                # Non-JSON response — wrap so error handling still gets context.
                parsed = {"error": f"Non-JSON response: {body_text[:200]}"}
        else:
            parsed = None

        if response.status_code >= 400:
            envelope = ErrorEnvelope.model_validate(parsed) if parsed else None
            raise_for_status(response.status_code, envelope, air_id=air_id)

        return parsed or {}

    def _require_admin_key(self) -> dict[str, str]:
        """Build the X-Admin-Key header dict, or raise if no key configured."""
        if not self._admin_key:
            raise AuthenticationError(
                "Admin endpoints require an admin_key. "
                "Pass admin_key= to AIRClient(...).",
                status_code=None,
            )
        return {"X-Admin-Key": self._admin_key}

    # ------------------------------------------------------------------
    # Public endpoints
    # ------------------------------------------------------------------

    async def get_health(self) -> Health:
        """GET /health — registry liveness check."""
        data = await self._request("GET", "/health")
        return Health.model_validate(data)

    async def list_agents(self, *, limit: int = 20, offset: int = 0) -> AgentList:
        """GET /agents — paginated list of active agents, sorted by trust score."""
        data = await self._request(
            "GET",
            "/agents",
            params={"limit": limit, "offset": offset},
        )
        return AgentList.model_validate(data)

    async def get_agent(self, air_id: str) -> Agent:
        """GET /agents/{air_id} — full agent record with trust breakdown."""
        data = await self._request("GET", f"/agents/{air_id}", air_id=air_id)
        return Agent.model_validate(data)

    async def get_trust_score(self, air_id: str) -> TrustScore:
        """GET /agents/{air_id}/trust-score — 5-component breakdown."""
        data = await self._request(
            "GET",
            f"/agents/{air_id}/trust-score",
            air_id=air_id,
        )
        return TrustScore.model_validate(data)

    async def get_did_document(self, air_id: str) -> DidDocument:
        """GET /agents/{air_id}/did-document — W3C DID Core JSON-LD.

        Raises `AgentNotFoundError` if the agent has no public_key on file
        (registration without one means no key to publish).
        """
        data = await self._request(
            "GET",
            f"/agents/{air_id}/did-document",
            air_id=air_id,
        )
        return DidDocument.model_validate(data)

    async def check_name(self, name: str) -> NameCheck:
        """GET /agents/check-name — does an agent with this name already exist?"""
        data = await self._request("GET", "/agents/check-name", params={"name": name})
        return NameCheck.model_validate(data)

    async def register_agent(
        self,
        *,
        name: str,
        description: str = "",
        creator_did: str | None = None,
        public_key: str | None = None,
        creator_name: str = "",
        creator_type: str = "individual",
        capabilities: list[str] | None = None,
        security_certifications: list[str] | None = None,
        open_source: bool = False,
        code_repository: str = "",
        documentation_url: str = "",
    ) -> RegistrationResult:
        """POST /agents/register — register a new agent.

        Either `creator_did` OR `public_key` is required:
          - creator_did present: caller controls the DID elsewhere (did:key, did:wba, did:web)
          - creator_did absent + public_key present: AIR mints a did:wba

        IMPORTANT: The returned `agent_secret` is shown ONLY ONCE. Store it
        before this function returns, or lose update access permanently.

        Rate-limited to 5 registrations per hour per source IP.
        """
        body: dict[str, Any] = {
            "name": name,
            "description": description,
            "creator_name": creator_name,
            "creator_type": creator_type,
            "open_source": open_source,
            "code_repository": code_repository,
            "documentation_url": documentation_url,
        }
        # Only include the identity-anchor fields if provided — the API
        # treats absence vs empty-string differently.
        if creator_did is not None:
            body["creator_did"] = creator_did
        if public_key is not None:
            body["public_key"] = public_key
        if capabilities is not None:
            body["capabilities"] = capabilities
        if security_certifications is not None:
            body["security_certifications"] = security_certifications

        data = await self._request("POST", "/agents/register", json=body)
        return RegistrationResult.model_validate(data)

    async def update_agent(
        self,
        air_id: str,
        *,
        agent_secret: str,
        description: str | None = None,
        capabilities: list[str] | None = None,
        security_certifications: list[str] | None = None,
        open_source: bool | None = None,
        code_repository: str | None = None,
        documentation_url: str | None = None,
    ) -> UpdateResult:
        """PUT /agents/{air_id} — update an existing agent's metadata.

        Requires the `agent_secret` returned at registration. Only the six
        listed fields are updatable (name, creator, public_key are immutable).
        Pass None (default) to leave a field unchanged.
        """
        body: dict[str, Any] = {}
        if description is not None:
            body["description"] = description
        if capabilities is not None:
            body["capabilities"] = capabilities
        if security_certifications is not None:
            body["security_certifications"] = security_certifications
        if open_source is not None:
            body["open_source"] = open_source
        if code_repository is not None:
            body["code_repository"] = code_repository
        if documentation_url is not None:
            body["documentation_url"] = documentation_url

        if not body:
            # Mirror the server's 400; raised client-side so we don't waste
            # an HTTP round-trip on an obvious mistake.
            from agent_identity_registry.exceptions import ValidationError
            raise ValidationError(
                "update_agent() requires at least one field to change.",
                status_code=None,
            )

        data = await self._request(
            "PUT",
            f"/agents/{air_id}",
            json=body,
            headers={"X-Agent-Secret": agent_secret},
            air_id=air_id,
        )
        return UpdateResult.model_validate(data)

    # ------------------------------------------------------------------
    # Admin endpoints (require admin_key)
    # ------------------------------------------------------------------

    async def delete_agent(self, air_id: str) -> DeleteResult:
        """DELETE /agents/{air_id} — admin-only soft-delete."""
        data = await self._request(
            "DELETE",
            f"/agents/{air_id}",
            headers=self._require_admin_key(),
            air_id=air_id,
        )
        return DeleteResult.model_validate(data)

    async def get_admin_stats(self) -> AdminStats:
        """GET /admin/stats — registry-wide counts + grade distribution."""
        data = await self._request(
            "GET",
            "/admin/stats",
            headers=self._require_admin_key(),
        )
        return AdminStats.model_validate(data)

    async def get_admin_recent(self, *, limit: int = 20) -> AdminRecent:
        """GET /admin/recent — most-recent registrations with creator info."""
        data = await self._request(
            "GET",
            "/admin/recent",
            params={"limit": limit},
            headers=self._require_admin_key(),
        )
        return AdminRecent.model_validate(data)
