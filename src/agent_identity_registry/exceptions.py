"""Exception hierarchy for the AIR SDK.

Design rules (from the build plan + reviews):

* Every exception inherits from `AirError`. Callers can `except AirError` to
  catch anything the SDK throws without also swallowing `KeyError`, `ValueError`,
  etc. that originate in user code.

* Each subclass corresponds to a class of failure that callers handle differently:
  - `ValidationError`  → fix the input and retry (4xx, body-validation)
  - `AuthenticationError` → re-check the secret/admin key (401, 403)
  - `AgentNotFoundError` → AIR ID doesn't exist (404 on /agents/* lookup)
  - `ConflictError` → rare ID collision (409) — caller should retry
  - `RateLimitedError` → back off `retry_after_seconds` (429)
  - `ServerError` → upstream registry failure (5xx)
  - `NetworkError` → couldn't reach the API at all (httpx-layer)

* Every exception carries the raw `status_code` and the parsed `ErrorEnvelope`
  (when one is available) so debugging callers can drill in without re-issuing
  the request.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_identity_registry.models import ErrorEnvelope


class AirError(Exception):
    """Base class for every SDK-raised exception.

    `status_code` is the HTTP status code from the registry, or None for
    transport-layer failures (`NetworkError`). `envelope` is the parsed
    error body when the API returned structured JSON, otherwise None.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        envelope: ErrorEnvelope | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.envelope = envelope


class ValidationError(AirError):
    """400 — request body or query parameters rejected by the API.

    The `envelope.error` string contains the human-readable reason, e.g.
    "Invalid public_key: must be 43 or 44 base64url chars".
    """


class AuthenticationError(AirError):
    """401 or 403 — missing or invalid `X-Agent-Secret` / `X-Admin-Key`."""


class AgentNotFoundError(AirError):
    """404 on an agent-scoped endpoint (lookup, trust-score, did-document, update).

    Exposes `air_id` for convenience — callers commonly want to log which
    agent went missing without unpacking the envelope.
    """

    def __init__(
        self,
        message: str,
        *,
        air_id: str | None = None,
        status_code: int | None = 404,
        envelope: ErrorEnvelope | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code, envelope=envelope)
        self.air_id = air_id


class ConflictError(AirError):
    """409 — AIR ID collision on registration. Caller should retry."""


class RateLimitedError(AirError):
    """429 — rate limit hit on /agents/register (5 per hour per IP).

    Exposes `retry_after_seconds` so callers can back off correctly.
    """

    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: int | None = None,
        status_code: int | None = 429,
        envelope: ErrorEnvelope | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code, envelope=envelope)
        self.retry_after_seconds = retry_after_seconds


class ServerError(AirError):
    """5xx — upstream registry failure. Caller should retry with backoff."""


class NetworkError(AirError):
    """Transport-layer failure (timeout, DNS, TLS, connection refused).

    `status_code` is always None — the request never reached an HTTP status.
    The `__cause__` will be the originating httpx exception for callers
    that need to inspect it.
    """


# ============================================================
# STATUS-CODE → EXCEPTION MAPPING
# ============================================================
#
# TODO(peter): Implement raise_for_status() below.
#
# This is the one function in the SDK where you decide the error UX.
# It receives:
#   - status: HTTP status code from the response
#   - envelope: parsed ErrorEnvelope (may be None if body wasn't valid JSON)
#   - air_id:  the agent ID from the request URL, or None for non-agent endpoints
#
# It must raise the appropriate AirError subclass when status >= 400, and
# return None when status < 400 (the client treats that as success).
#
# Trade-offs to consider:
#
# 1) Exact-code vs range matching:
#    Exact (status == 404) is precise but you have to enumerate every code.
#    Range (400 <= status < 500) is broad but might miss a specific code like
#    409 (conflict) that warrants its own class.
#    Recommended: hybrid — exact match for the special codes (404, 409, 429),
#    range fallback for the rest. Keeps the function short AND specific.
#
# 2) Where the human message comes from:
#    The envelope's `error` and `message` are server-provided. Prefer those
#    over a hard-coded SDK string — the API knows more about the specific
#    failure than the SDK does. Fall back to a generic string only when
#    envelope is None or envelope.error is empty.
#
# 3) AgentNotFoundError vs generic 404:
#    Pass `air_id` through so callers can `except AgentNotFoundError as e:
#    log.warning("missing %s", e.air_id)` without unpacking the envelope.
#
# 4) RateLimitedError needs retry_after_seconds — pull it from envelope.
#
# Reference: see exception classes above for which fields each one accepts.


def raise_for_status(
    status: int,
    envelope: ErrorEnvelope | None,
    air_id: str | None = None,
) -> None:
    """Raise the appropriate AirError subclass for a non-2xx response.

    Returns None when status indicates success (< 400). The client calls
    this on every response before attempting to parse the success body.

    Pattern: exact-match the special codes first (404, 409, 429, 401/403),
    then range-fallback for the rest (other 4xx → ValidationError,
    5xx → ServerError). The final catch-all is unreachable in practice
    but keeps the type-checker happy and protects against future weirdness
    (e.g. a 3xx redirect leaking through).
    """
    if status < 400:
        return None

    # Prefer the server's human message; fall back to a generic one.
    # This is how SDK callers automatically get richer errors when the
    # API improves its messaging — zero SDK changes required.
    message = envelope.error if envelope and envelope.error else f"HTTP {status}"

    if status == 404:
        raise AgentNotFoundError(message, air_id=air_id, envelope=envelope)
    if status == 409:
        raise ConflictError(message, status_code=status, envelope=envelope)
    if status == 429:
        retry_after = envelope.retry_after_seconds if envelope else None
        raise RateLimitedError(
            message,
            retry_after_seconds=retry_after,
            status_code=status,
            envelope=envelope,
        )
    if status in (401, 403):
        raise AuthenticationError(message, status_code=status, envelope=envelope)
    if 400 <= status < 500:
        raise ValidationError(message, status_code=status, envelope=envelope)
    if status >= 500:
        raise ServerError(message, status_code=status, envelope=envelope)

    # Unreachable: every status >= 400 matched above. Keeps mypy happy and
    # protects against future surprises (e.g. a 3xx leaking past the client).
    raise AirError(message, status_code=status, envelope=envelope)
