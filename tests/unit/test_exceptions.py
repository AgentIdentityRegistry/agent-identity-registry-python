"""Tests for the status-code → exception mapping."""

from __future__ import annotations

import pytest

from agent_identity_registry.exceptions import (
    AgentNotFoundError,
    AirError,
    AuthenticationError,
    ConflictError,
    RateLimitedError,
    ServerError,
    ValidationError,
    raise_for_status,
)
from agent_identity_registry.models import ErrorEnvelope


def env(error: str = "boom", **kw) -> ErrorEnvelope:
    return ErrorEnvelope(error=error, **kw)


class TestRaiseForStatus:
    def test_success_returns_none(self):
        assert raise_for_status(200, None) is None
        assert raise_for_status(201, None) is None
        assert raise_for_status(204, None) is None
        assert raise_for_status(399, None) is None

    def test_404_with_air_id(self):
        with pytest.raises(AgentNotFoundError) as ei:
            raise_for_status(404, env("Agent not found"), air_id="AIR-AAAA-BBBB-CCCC")
        assert ei.value.air_id == "AIR-AAAA-BBBB-CCCC"
        assert ei.value.status_code == 404
        assert "Agent not found" in str(ei.value)

    def test_404_without_envelope_uses_fallback_message(self):
        with pytest.raises(AgentNotFoundError) as ei:
            raise_for_status(404, None, air_id="AIR-X")
        assert "HTTP 404" in str(ei.value)
        assert ei.value.envelope is None

    def test_409_conflict(self):
        with pytest.raises(ConflictError) as ei:
            raise_for_status(409, env("ID collision"))
        assert ei.value.status_code == 409

    def test_429_with_retry_after(self):
        with pytest.raises(RateLimitedError) as ei:
            raise_for_status(429, env("Rate limit exceeded", retry_after_seconds=3600))
        assert ei.value.retry_after_seconds == 3600
        assert ei.value.status_code == 429

    def test_429_without_retry_after(self):
        with pytest.raises(RateLimitedError) as ei:
            raise_for_status(429, env("Rate limit"))
        assert ei.value.retry_after_seconds is None

    def test_401_authentication(self):
        with pytest.raises(AuthenticationError) as ei:
            raise_for_status(401, env("Missing X-Agent-Secret"))
        assert ei.value.status_code == 401

    def test_403_authentication(self):
        with pytest.raises(AuthenticationError) as ei:
            raise_for_status(403, env("Forbidden"))
        assert ei.value.status_code == 403

    @pytest.mark.parametrize("status", [400, 405, 415, 422])
    def test_other_4xx_validation(self, status):
        with pytest.raises(ValidationError) as ei:
            raise_for_status(status, env(f"bad {status}"))
        assert ei.value.status_code == status

    @pytest.mark.parametrize("status", [500, 502, 503, 504])
    def test_5xx_server_error(self, status):
        with pytest.raises(ServerError) as ei:
            raise_for_status(status, None)
        assert ei.value.status_code == status

    def test_envelope_message_preferred_over_fallback(self):
        with pytest.raises(ValidationError) as ei:
            raise_for_status(400, env("Invalid public_key: must be 32 bytes"))
        assert "Invalid public_key" in str(ei.value)
        assert "HTTP 400" not in str(ei.value)


class TestExceptionHierarchy:
    """Every subclass must catch as AirError so callers can use a single except."""

    @pytest.mark.parametrize(
        "exc_cls",
        [
            AgentNotFoundError,
            ConflictError,
            RateLimitedError,
            AuthenticationError,
            ValidationError,
            ServerError,
        ],
    )
    def test_all_subclass_air_error(self, exc_cls):
        assert issubclass(exc_cls, AirError)
