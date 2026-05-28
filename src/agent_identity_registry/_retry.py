"""Retry transport for the AIR SDK.

Wraps an `httpx.AsyncBaseTransport` and retries transient failures with
exponential backoff + jitter. Sits between `AIRClient._request()` and the
network so the rest of the SDK doesn't need to know retries exist.

Why a transport wrapper instead of a loop inside `_request()`:
* Separation of concerns — transport decides WHEN to retry, `_request`
  decides HOW to parse the eventual response.
* Composes with any other transport (mocks in tests, custom proxies in
  production) — `RetryTransport(MockTransport(...))` just works.
* Idiomatic httpx — this is the pattern their docs recommend.

What we retry:
* **Network-layer errors** (timeouts, DNS failures, connection refused, TLS
  errors) — anything that surfaces as `httpx.HTTPError`. The request may
  or may not have reached the server.
* **HTTP responses with retryable status codes** — by default 429 (rate
  limited), 502/503/504 (gateway/upstream). Configurable per-client.

What we DON'T retry:
* 4xx other than 429 — caller bug (validation, auth, not-found). Retrying
  won't help.
* 500 (generic server error) by default — could be a real bug being
  triggered every request. Don't hammer. Opt in via `retry_on_status`.
* Non-idempotent methods (POST) on network errors when `idempotent_only`
  is True — see the doc on `RetryConfig.idempotent_only` for the trade-off.

Retry-After header on 429:
* Always respected. The server is explicitly telling you when to retry.
* If present and longer than our computed backoff, we sleep that long.
* If absent, we use the normal exponential schedule.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx

# Methods that are safe to retry blindly on network errors.
# Per RFC 9110 §9.2.2 — these are defined to have no extra side effect on
# the server beyond the request itself, so re-issuing them is safe.
_IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})

# Type alias for the sleep function — injectable so tests can no-op it.
_SleepFn = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class RetryConfig:
    """Policy for the `RetryTransport`.

    Mutate by constructing a new instance — the dataclass is frozen so an
    `AIRClient` can't have its retry policy changed mid-flight without
    making the change explicit.

    Attributes:
        max_retries: Number of RETRIES (not total attempts). `max_retries=3`
            means up to 4 total tries — the original + 3 retries.
        base_delay: Initial backoff in seconds before the first retry.
        max_delay: Cap on any single backoff sleep. Without this, late
            attempts on a stuck request could sleep for minutes.
        backoff_multiplier: Each attempt's delay is multiplied by this.
            With base=0.5, mult=2.0: sleeps are roughly 0.5s, 1s, 2s, 4s, ...
            (capped at max_delay).
        jitter: Multiply each sleep by random(0.5, 1.5) to avoid the
            "thundering herd" — N clients hitting the same outage and
            retrying in perfect sync. Default True.
        retry_on_status: HTTP status codes that trigger a retry. 4xx codes
            other than these are caller bugs — retrying won't help.
        idempotent_only: When True (default), POST requests are NOT retried
            on network errors. Reason: a network error after the server
            already processed the POST would create a duplicate
            registration. POSTs that get a retryable RESPONSE (e.g. 503)
            ARE still retried — the server clearly didn't accept those.
    """

    max_retries: int = 3
    base_delay: float = 0.5
    max_delay: float = 10.0
    backoff_multiplier: float = 2.0
    jitter: bool = True
    retry_on_status: tuple[int, ...] = field(default=(429, 502, 503, 504))
    idempotent_only: bool = True


# Sentinel for "no retry behavior" — equivalent to not wrapping at all.
NO_RETRY = RetryConfig(max_retries=0)

# Module-level default singleton. Lives here (not at the call site) so the
# constructor signature can read `retry_config: RetryConfig | None = DEFAULT_RETRY`
# without ruff's B008 firing on a function call in a default. Safe to share
# across clients because RetryConfig is frozen + immutable.
DEFAULT_RETRY = RetryConfig()


class RetryTransport(httpx.AsyncBaseTransport):
    """Async httpx transport that retries transient failures.

    Use via `AIRClient(retry_config=RetryConfig(...))` — the client wires
    this up around whatever inner transport you pass (or `AsyncHTTPTransport`
    by default).
    """

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport,
        config: RetryConfig,
        *,
        sleep: _SleepFn | None = None,
    ) -> None:
        self._inner = inner
        self._config = config
        # Injectable sleep so tests don't actually wait. Production = asyncio.sleep.
        self._sleep: _SleepFn = sleep or asyncio.sleep

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        last_exc: Exception | None = None
        last_response: httpx.Response | None = None

        # Total attempts = 1 (initial) + max_retries.
        for attempt in range(self._config.max_retries + 1):
            try:
                response = await self._inner.handle_async_request(request)
            except httpx.HTTPError as exc:
                # Network-layer failure (timeout, DNS, TLS, conn refused).
                if not self._should_retry_network_error(request, attempt):
                    raise
                last_exc = exc
                await self._sleep_for_attempt(attempt, retry_after=None)
                continue

            # Got a response. Decide whether to retry based on status.
            if response.status_code not in self._config.retry_on_status:
                return response
            if attempt >= self._config.max_retries:
                # Out of retries — surface the final response so the SDK
                # raises the normal RateLimitedError / ServerError for it.
                return response

            last_response = response
            retry_after = _parse_retry_after(response)
            # The response body has to be consumed before we can re-issue
            # the request — otherwise httpx may hold the connection.
            await response.aclose()
            await self._sleep_for_attempt(attempt, retry_after=retry_after)

        # Unreachable in practice — the loop either returns or raises above.
        # Defensive fallback for type-checker + future-proofing.
        if last_response is not None:
            return last_response
        assert last_exc is not None
        raise last_exc

    async def aclose(self) -> None:
        await self._inner.aclose()

    # ---- policy helpers --------------------------------------------------

    def _should_retry_network_error(self, request: httpx.Request, attempt: int) -> bool:
        if attempt >= self._config.max_retries:
            return False
        # POST on network error: server MIGHT have accepted it. Don't retry —
        # caller will get NetworkError and decide consciously. Equivalent to:
        # `if idempotent_only and method is non-idempotent: return False; return True`
        return not (
            self._config.idempotent_only
            and request.method.upper() not in _IDEMPOTENT_METHODS
        )

    async def _sleep_for_attempt(self, attempt: int, *, retry_after: float | None) -> None:
        """Sleep before retry #attempt+1. Honors server Retry-After when present."""
        delay = min(
            self._config.max_delay,
            self._config.base_delay * (self._config.backoff_multiplier**attempt),
        )
        if self._config.jitter:
            # Full jitter in the [0.5x, 1.5x] band — small enough to keep the
            # exponential shape, big enough to desync many clients.
            delay *= random.uniform(0.5, 1.5)
        if retry_after is not None and retry_after > delay:
            # Server explicitly told us when to retry. Don't out-clever it.
            delay = retry_after
        await self._sleep(delay)


def _parse_retry_after(response: httpx.Response) -> float | None:
    """Parse the Retry-After header. Returns seconds, or None if absent/invalid.

    Per RFC 9110 §10.2.3, the value is either:
      * delta-seconds (e.g. "120")
      * HTTP-date (e.g. "Wed, 21 Oct 2026 07:28:00 GMT")
    We honor the integer form; HTTP-date is rare in practice and adds parsing
    surface for little gain. Treat unparseable as absent.
    """
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return float(raw.strip())
    except ValueError:
        # Could be HTTP-date — for now we ignore and fall back to our schedule.
        return None
