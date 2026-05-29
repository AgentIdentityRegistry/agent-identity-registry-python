# Changelog

All notable changes to `agent-identity-registry` are recorded here.

This project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html). The SDK is `0.x` — minor versions may carry small behavior changes; we call them out in **Changed** sections.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.5.0] — 2026-05-29

### Added
- **AIR Verified attestations.** Four new `AIRClient` methods wrapping the Phase 4 attestation API:
  - `create_attestation(...)` — low-level POST; you supply a `signature_multibase` you computed yourself.
  - `attest(...)` — high-level: canonicalizes, Ed25519-signs, and submits in one call (needs the `[signing]` extra).
  - `list_attestations(air_id)` — public audit trail (active + revoked).
  - `recent_attestations(limit=...)` — public firehose.
  - `revoke_attestation(air_id, attestation_id, agent_secret=...)` — soft-delete by the original attester.
- **`agent_identity_registry.signing` module** for client-side signing:
  - `sign_attestation(private_key, ...)` → `signature_multibase` (`z` + base58btc of the 64-byte Ed25519 signature).
  - `canonical_attestation_bytes(...)` — byte-exact RFC 8785 JCS of `{attester_air_id, attestation_type, signed_at, statement, subject_air_id}`, matching the Worker's `jcsCanonicalize` (keys sorted, no whitespace, `statement` always present, no NFC normalization).
  - `load_private_key_from_seed(seed)` — Ed25519 key from a 32-byte seed (hex or raw).
  - `VALID_ATTESTATION_TYPES` — the four lockable types.
- New models: `Attestation`, `AttestationResult`, `AttestationList`, `RecentAttestation`, `RecentAttestations`, `RevokeResult`, `VerifiedStatus`, and `VerificationStatus`.
- `Agent.verification_status` — the attestation-derived Verified breakdown now parses off `GET /agents/{id}` (None on older API responses).
- 18 new unit tests: byte-exact canonicalization vector, full sign→Ed25519-verify roundtrip, and MockTransport coverage for every new client method.

### Changed
- New optional dependency extra **`signing`** (`cryptography>=42`). The core install stays `httpx` + `pydantic` only — attestation signing raises a friendly "install `[signing]`" error if the extra is missing. Read-only consumers are unaffected.

## [0.3.0] — 2026-05-28

### Added
- **Automatic retries with exponential backoff** for transient failures. By default, the SDK now retries `429`, `502`, `503`, `504`, and network errors (3 attempts, 0.5s → 1s → 2s with jitter). Respects `Retry-After` headers on `429`.
- New `RetryConfig` dataclass exported from the package root for tuning the retry policy (`max_retries`, `base_delay`, `max_delay`, `backoff_multiplier`, `jitter`, `retry_on_status`, `idempotent_only`).
- 18 new unit tests covering retry behavior end-to-end (backoff math, status filtering, idempotency rule, `Retry-After` respect, AIRClient integration).

### Changed
- **`AIRClient()` now retries transient failures by default.** This is the only user-visible behavior change in 0.3. To restore the v0.2 behavior (one shot, fail fast), pass `retry_config=None`:
  ```python
  async with AIRClient(retry_config=None) as client:
      ...
  ```
- Tightened constructor docstring to spell out the new retry default.

### Safety
- Network errors on `POST /agents/register` are **not** retried by default (`idempotent_only=True`). Reason: a partial network failure could otherwise create duplicate registrations. The endpoint still retries when the server returns a retryable response (`503`, `429`) — in that case the server clearly didn't accept the request.

## [0.2.0] — 2026-05-27

### Added
- `air` CLI bundled into the package: `air health`, `air list`, `air lookup`, `air score`, `air did-doc`, `air check`, `air register`.
- Human-friendly default output with ANSI colors; `--json` for scripting; `--base-url` and `--no-color` flags.
- 20 unit tests for the CLI.

### Fixed
- Argparse global flag positioning — flags must come after the subcommand (`air health --json`, not `air --json health`).
- CLI error output now goes uniformly to stderr.

## [0.1.0] — 2026-05-26

### Added
- Initial release of the async Python SDK.
- 11 endpoints: `get_health`, `list_agents`, `get_agent`, `get_trust_score`, `get_did_document`, `check_name`, `register_agent`, `update_agent`, `delete_agent`, `get_admin_stats`, `get_admin_recent`.
- Full 7-class exception hierarchy mapped to HTTP status codes.
- 47 unit tests via `httpx.MockTransport`, plus 6 live integration tests gated by `AIR_LIVE_TESTS=1`.
- Apache 2.0 license.

[0.3.0]: https://github.com/AgentIdentityRegistry/agent-identity-registry-python/releases/tag/v0.3.0
[0.2.0]: https://github.com/AgentIdentityRegistry/agent-identity-registry-python/releases/tag/v0.2.0
[0.1.0]: https://github.com/AgentIdentityRegistry/agent-identity-registry-python/releases/tag/v0.1.0
