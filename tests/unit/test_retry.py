"""Tests for the RetryTransport.

The transport's job is purely "decide whether to retry, sleep, and re-issue."
We exercise it directly via httpx.MockTransport so each scenario stays sub-
millisecond. Sleep is mocked to a no-op so we never actually wait.

Two test surfaces:
  1. RetryTransport in isolation — fine-grained: backoff, status filter,
     idempotency, Retry-After, sleep accounting.
  2. AIRClient with retry_config wired in — end-to-end: the retry actually
     translates into a successful AIRClient call after transient flakes.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from agent_identity_registry import (
    AIRClient,
    RateLimitedError,
    RetryConfig,
    ServerError,
)
from agent_identity_registry._retry import NO_RETRY, RetryTransport, _parse_retry_after

# ---- helpers -----------------------------------------------------------


def _json_response(status: int, body: dict[str, Any], **headers: str) -> httpx.Response:
    return httpx.Response(
        status,
        content=json.dumps(body),
        headers={"Content-Type": "application/json", **headers},
    )


def _make_flaky_transport(responses: list) -> httpx.MockTransport:
    """Build a MockTransport that yields one response per call.

    Each entry is either:
      * (status, body_dict[, headers_dict]) — returns the canned response
      * Exception instance — raised on that call
    Once the list is exhausted, the LAST entry repeats indefinitely.
    """
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        i = min(call_count["n"], len(responses) - 1)
        call_count["n"] += 1
        item = responses[i]
        if isinstance(item, BaseException):
            raise item
        if len(item) == 2:
            status, body = item
            return _json_response(status, body)
        status, body, headers = item
        return _json_response(status, body, **headers)

    transport = httpx.MockTransport(handler)
    transport.calls = call_count  # type: ignore[attr-defined]
    return transport


@pytest.fixture
def no_sleep():
    """Pass-through sleep that records calls without ever waiting."""
    calls: list[float] = []

    async def _sleep(delay: float) -> None:
        calls.append(delay)

    _sleep.calls = calls  # type: ignore[attr-defined]
    return _sleep


# ---- RetryTransport in isolation ---------------------------------------


async def test_no_retry_when_max_retries_zero(no_sleep):
    """NO_RETRY sentinel = transport returns the first response, no sleeps."""
    inner = _make_flaky_transport([(503, {"error": "down"})])
    rt = RetryTransport(inner, NO_RETRY, sleep=no_sleep)
    resp = await rt.handle_async_request(
        httpx.Request("GET", "https://test.invalid/x")
    )
    assert resp.status_code == 503
    assert inner.calls["n"] == 1
    assert no_sleep.calls == []


async def test_retries_503_until_success(no_sleep):
    inner = _make_flaky_transport(
        [
            (503, {"error": "down"}),
            (503, {"error": "down"}),
            (200, {"ok": True}),
        ]
    )
    rt = RetryTransport(inner, RetryConfig(max_retries=3, jitter=False), sleep=no_sleep)
    resp = await rt.handle_async_request(
        httpx.Request("GET", "https://test.invalid/x")
    )
    assert resp.status_code == 200
    assert inner.calls["n"] == 3
    # Two sleeps for two retries; jitter off → deterministic.
    assert no_sleep.calls == [0.5, 1.0]


async def test_gives_up_after_max_retries_and_returns_last_response(no_sleep):
    inner = _make_flaky_transport([(503, {"error": "down"})])  # always 503
    rt = RetryTransport(inner, RetryConfig(max_retries=2, jitter=False), sleep=no_sleep)
    resp = await rt.handle_async_request(
        httpx.Request("GET", "https://test.invalid/x")
    )
    assert resp.status_code == 503
    assert inner.calls["n"] == 3  # initial + 2 retries
    assert len(no_sleep.calls) == 2


async def test_does_not_retry_non_retryable_status(no_sleep):
    """404 isn't in retry_on_status → single call, no retry."""
    inner = _make_flaky_transport([(404, {"error": "not found"})])
    rt = RetryTransport(inner, RetryConfig(max_retries=3, jitter=False), sleep=no_sleep)
    resp = await rt.handle_async_request(
        httpx.Request("GET", "https://test.invalid/x")
    )
    assert resp.status_code == 404
    assert inner.calls["n"] == 1
    assert no_sleep.calls == []


async def test_retries_network_error_on_idempotent_method(no_sleep):
    inner = _make_flaky_transport(
        [
            httpx.ConnectError("conn refused"),
            (200, {"ok": True}),
        ]
    )
    rt = RetryTransport(inner, RetryConfig(max_retries=2, jitter=False), sleep=no_sleep)
    resp = await rt.handle_async_request(
        httpx.Request("GET", "https://test.invalid/x")
    )
    assert resp.status_code == 200
    assert inner.calls["n"] == 2
    assert no_sleep.calls == [0.5]


async def test_does_NOT_retry_network_error_on_POST_when_idempotent_only(no_sleep):
    """Default idempotent_only=True: POST + network error must surface immediately
    so callers don't accidentally double-register an agent."""
    inner = _make_flaky_transport([httpx.ConnectError("conn refused")])
    rt = RetryTransport(inner, RetryConfig(max_retries=3, jitter=False), sleep=no_sleep)
    with pytest.raises(httpx.ConnectError):
        await rt.handle_async_request(httpx.Request("POST", "https://test.invalid/x"))
    assert inner.calls["n"] == 1
    assert no_sleep.calls == []


async def test_DOES_retry_POST_when_idempotent_only_disabled(no_sleep):
    inner = _make_flaky_transport(
        [
            httpx.ConnectError("conn refused"),
            (201, {"id": "AIR-x"}),
        ]
    )
    rt = RetryTransport(
        inner,
        RetryConfig(max_retries=2, jitter=False, idempotent_only=False),
        sleep=no_sleep,
    )
    resp = await rt.handle_async_request(
        httpx.Request("POST", "https://test.invalid/x")
    )
    assert resp.status_code == 201
    assert inner.calls["n"] == 2


async def test_retries_POST_on_503_even_when_idempotent_only(no_sleep):
    """The idempotent_only rule only gates NETWORK errors. A 503 response means
    the server clearly didn't accept the POST — safe to retry."""
    inner = _make_flaky_transport(
        [
            (503, {"error": "down"}),
            (201, {"id": "AIR-x"}),
        ]
    )
    rt = RetryTransport(inner, RetryConfig(max_retries=2, jitter=False), sleep=no_sleep)
    resp = await rt.handle_async_request(
        httpx.Request("POST", "https://test.invalid/x")
    )
    assert resp.status_code == 201
    assert inner.calls["n"] == 2


async def test_retry_after_header_overrides_backoff(no_sleep):
    """Server says wait 7s. Our exponential would be 0.5s. Honor the server."""
    inner = _make_flaky_transport(
        [
            (429, {"error": "slow down"}, {"retry-after": "7"}),
            (200, {"ok": True}),
        ]
    )
    rt = RetryTransport(inner, RetryConfig(max_retries=2, jitter=False), sleep=no_sleep)
    resp = await rt.handle_async_request(
        httpx.Request("GET", "https://test.invalid/x")
    )
    assert resp.status_code == 200
    assert no_sleep.calls == [7.0]  # Retry-After won, not 0.5


async def test_backoff_caps_at_max_delay(no_sleep):
    inner = _make_flaky_transport([(503, {"error": "down"})])
    rt = RetryTransport(
        inner,
        RetryConfig(max_retries=5, base_delay=2.0, max_delay=3.0, jitter=False),
        sleep=no_sleep,
    )
    await rt.handle_async_request(httpx.Request("GET", "https://test.invalid/x"))
    # base=2, mult=2: would-be sleeps 2, 4, 8, 16, 32 — all capped at 3.
    assert no_sleep.calls == [2.0, 3.0, 3.0, 3.0, 3.0]


async def test_exponential_growth_without_jitter(no_sleep):
    inner = _make_flaky_transport([(503, {"error": "down"})])
    rt = RetryTransport(
        inner,
        RetryConfig(max_retries=4, base_delay=0.1, backoff_multiplier=3.0, jitter=False),
        sleep=no_sleep,
    )
    await rt.handle_async_request(httpx.Request("GET", "https://test.invalid/x"))
    assert no_sleep.calls == pytest.approx([0.1, 0.3, 0.9, 2.7])


# ---- Retry-After parser edge cases ------------------------------------


def test_retry_after_missing():
    resp = httpx.Response(429)
    assert _parse_retry_after(resp) is None


def test_retry_after_integer():
    resp = httpx.Response(429, headers={"Retry-After": "42"})
    assert _parse_retry_after(resp) == 42.0


def test_retry_after_unparseable_returns_none():
    """HTTP-date form (e.g. 'Wed, 21 Oct 2026...') isn't supported yet — must not crash."""
    resp = httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
    assert _parse_retry_after(resp) is None


# ---- End-to-end: AIRClient with retry_config wired in ------------------


async def test_airclient_retries_503_then_succeeds(monkeypatch):
    """SDK call survives a transient 503 when retry_config is set."""
    monkeypatch.setattr(asyncio, "sleep", _no_op_sleep)
    inner = _make_flaky_transport(
        [
            (503, {"error": "down"}),
            (200, {"status": "ok", "version": "0.3.0", "registry": "AIR"}),
        ]
    )
    async with AIRClient(
        base_url="https://test.invalid",
        transport=inner,
        retry_config=RetryConfig(max_retries=2, jitter=False),
    ) as client:
        h = await client.get_health()
    assert h.status == "ok"
    assert inner.calls["n"] == 2


async def test_airclient_surfaces_rate_limited_after_exhausting_retries(monkeypatch):
    """After max_retries 429s, the SDK raises RateLimitedError with retry_after."""
    monkeypatch.setattr(asyncio, "sleep", _no_op_sleep)
    inner = _make_flaky_transport(
        [(429, {"error": "rate", "retry_after_seconds": 60}, {"retry-after": "60"})]
    )
    async with AIRClient(
        base_url="https://test.invalid",
        transport=inner,
        retry_config=RetryConfig(max_retries=2, jitter=False),
    ) as client:
        with pytest.raises(RateLimitedError) as ei:
            await client.get_health()
    assert ei.value.status_code == 429
    assert inner.calls["n"] == 3  # initial + 2 retries


async def test_airclient_with_retry_config_none_does_not_retry(monkeypatch):
    """Explicit opt-out: passing retry_config=None disables the retry wrapper.

    Note: as of v0.3 the *default* is retry-on (RetryConfig()), so this test
    proves the explicit opt-out path, not the constructor default.
    """
    monkeypatch.setattr(asyncio, "sleep", _no_op_sleep)
    inner = _make_flaky_transport([(503, {"error": "down"})])
    async with AIRClient(
        base_url="https://test.invalid",
        transport=inner,
        retry_config=None,
    ) as client:
        with pytest.raises(ServerError):
            await client.get_health()
    assert inner.calls["n"] == 1  # no retry attempted


async def test_airclient_default_constructor_retries(monkeypatch):
    """Bare AIRClient() picks up the v0.3 default retry policy."""
    monkeypatch.setattr(asyncio, "sleep", _no_op_sleep)
    inner = _make_flaky_transport(
        [
            (503, {"error": "down"}),
            (200, {"status": "ok", "version": "0.3.0", "registry": "AIR"}),
        ]
    )
    async with AIRClient(base_url="https://test.invalid", transport=inner) as client:
        h = await client.get_health()
    assert h.status == "ok"
    assert inner.calls["n"] == 2  # initial + 1 retry, default policy kicked in


async def _no_op_sleep(_: float) -> None:
    return None
