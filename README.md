# agent-identity-registry

Async Python SDK + `air` CLI for the [Agent Identity Registry (AIR)](https://agentidentityregistry.org).

AIR is a W3C-aligned identity layer for AI agents: every agent gets a stable identifier (`AIR-XXXX-XXXX-XXXX`), a public-key-backed DID document, and a graduated trust score that counterparties can verify before transacting.

## Install

```bash
pip install agent-identity-registry
```

Requires Python 3.10+. Installs both the Python SDK and the `air` command-line tool.

## `air` CLI

Pretty terminal access to every public endpoint — useful for demos, scripting, and exploration.

```bash
air health                           # Check registry liveness
air list                             # List agents (sorted by trust score)
air list --limit 5                   # Paginate
air lookup AIR-XXXX-XXXX-XXXX        # Full agent record
air score AIR-XXXX-XXXX-XXXX         # Trust-score breakdown with bar chart
air did-doc AIR-XXXX-XXXX-XXXX       # W3C DID Core JSON-LD
air check WeatherBot                 # Is this name taken?
air register --name MyBot --public-key <ed25519-base64url> --open-source
```

Global flags:

| Flag | Effect |
|------|--------|
| `--json` | Output raw JSON instead of human-friendly format (for scripting) |
| `--base-url URL` | Point at a different registry (also `AIR_BASE_URL` env var) |
| `--no-color` | Disable ANSI color codes (also `NO_COLOR` env var) |

Exit codes: `0` on success, `1` on AIR-level error (404, rate-limit, server error, network failure — friendly message printed to stderr), `2` on usage error (argparse).

## Quick start

```python
import asyncio
from agent_identity_registry import AIRClient

async def main():
    async with AIRClient() as client:
        # Public lookup — no auth
        agent = await client.get_agent("AIR-XXXX-XXXX-XXXX")
        print(agent.name, agent.trust_score, agent.trust_grade)

        # Trust-score breakdown (5 components)
        score = await client.get_trust_score("AIR-XXXX-XXXX-XXXX")
        print(score.components.provenance, score.components.behavioral)

        # W3C DID document (publicKeyMultibase + service endpoints)
        did_doc = await client.get_did_document("AIR-XXXX-XXXX-XXXX")
        print(did_doc.id, did_doc.verification_method[0].public_key_multibase)

asyncio.run(main())
```

## Register a new agent

Returns an `agent_secret` you must store yourself — it's shown only once and is required for later updates.

```python
async with AIRClient() as client:
    result = await client.register_agent(
        name="WeatherBot",
        description="Forecasts weather in 70 cities",
        public_key="...43-char-base64url-Ed25519-key...",  # AIR will mint a did:wba
        creator_type="individual",
        capabilities=["weather", "forecast"],
        open_source=True,
        code_repository="https://github.com/example/weatherbot",
    )

    print(result.air_id)          # AIR-XXXX-XXXX-XXXX
    print(result.agent_secret)    # SAVE THIS — required for PUT
    print(result.air_minted_did)  # True if AIR minted a did:wba
```

## Update an agent

Pass the secret you stored at registration.

```python
async with AIRClient() as client:
    update = await client.update_agent(
        air_id="AIR-XXXX-XXXX-XXXX",
        agent_secret="...32-char-hex-secret-from-registration...",
        description="Updated description",
        capabilities=["weather", "forecast", "alerts"],
    )
    print(update.trust_score)  # Recalculated after update
```

## Errors

All errors derive from `AirError`:

| Status | Exception | Notes |
|--------|-----------|-------|
| 400 | `ValidationError` | Bad input (invalid public_key, missing required field) |
| 401 / 403 | `AuthenticationError` | Missing/invalid `agent_secret` or admin key |
| 404 | `AgentNotFoundError` | Unknown AIR ID |
| 409 | `ConflictError` | Rare ID collision — retry registration |
| 429 | `RateLimitedError` | Exposes `retry_after_seconds` |
| 5xx | `ServerError` | Upstream registry issue |
| connection failures | `NetworkError` | httpx layer (timeout, DNS, TLS) |

```python
from agent_identity_registry import AIRClient, AgentNotFoundError, RateLimitedError

async with AIRClient() as client:
    try:
        agent = await client.get_agent("AIR-DOES-NOTX-IST0")
    except AgentNotFoundError as e:
        print(f"No such agent: {e.air_id}")
    except RateLimitedError as e:
        print(f"Slow down. Retry in {e.retry_after_seconds}s")
```

## Retries

Since **v0.3**, the SDK retries transient failures automatically — 3 attempts with exponential backoff and jitter. Retries cover `429`, `502`, `503`, `504`, and network errors on idempotent methods (`GET`, `PUT`, `DELETE`).

Network errors on `POST /agents/register` are **not** retried by default — a partial network failure could otherwise create duplicate registrations. The `register_agent` call still retries when the server returns a retryable response (`503`, `429`) because in that case the server clearly did not accept the request.

When `Retry-After` is present (e.g. on `429`), the SDK respects it instead of its own backoff.

```python
from agent_identity_registry import AIRClient, RetryConfig

# Default — works for most callers, no setup needed
async with AIRClient() as client:
    await client.get_health()

# Tune the policy
async with AIRClient(retry_config=RetryConfig(max_retries=5, base_delay=1.0)) as client:
    await client.get_health()

# Disable retries entirely (v0.2 behavior)
async with AIRClient(retry_config=None) as client:
    await client.get_health()
```

`RetryConfig` is a frozen dataclass — all fields are constructor-only. Knobs: `max_retries`, `base_delay`, `max_delay`, `backoff_multiplier`, `jitter`, `retry_on_status`, `idempotent_only`.

## License

Apache 2.0 — see the registry repo for full text.
