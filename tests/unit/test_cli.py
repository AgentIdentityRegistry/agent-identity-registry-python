"""Tests for the `air` CLI command.

Uses httpx.MockTransport injected at the cli module level. Each test passes
--no-color so assertions can match plain text without ANSI codes.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from agent_identity_registry import AIRClient
from agent_identity_registry.cli import main

# ============================================================
# FIXTURES
# ============================================================


@pytest.fixture
def mock_air_cli(monkeypatch) -> Callable[..., None]:
    """Patch cli.AIRClient with a factory that returns a MockTransport-backed client.

    Each route is `(method, path) -> (status, body_dict)`.
    """

    def _setup(routes: dict[tuple[str, str], tuple[int, dict[str, Any]]]) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            key = (request.method, request.url.path)
            if key not in routes:
                return httpx.Response(404, json={"error": f"no mock for {key!r}"})
            status, body = routes[key]
            return httpx.Response(
                status,
                content=json.dumps(body),
                headers={"Content-Type": "application/json"},
            )

        def make_client(*, base_url: str = "", **_kw: Any) -> AIRClient:
            return AIRClient(
                base_url="https://test.invalid",
                transport=httpx.MockTransport(handler),
            )

        monkeypatch.setattr("agent_identity_registry.cli.AIRClient", make_client)

    return _setup


# Common payload fixtures
HEALTH = {"status": "ok", "version": "0.2.0", "registry": "AIR"}
TRUST = {
    "air_id": "AIR-XXXX-YYYY-ZZZZ",
    "total_score": 942,
    "grade": "AAA",
    "components": {
        "provenance": 960,
        "behavioral": 900,
        "transparency": 950,
        "security": 920,
        "peer_attestations": 930,
    },
    "calculated_at": "2026-05-24T12:34:56.000Z",
}


# ============================================================
# COMMAND-LEVEL TESTS
# ============================================================


def test_health_default_output(mock_air_cli, capsys):
    mock_air_cli({("GET", "/api/v1/health"): (200, HEALTH)})
    rc = main(["health", "--no-color"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Registry" in out
    assert "AIR" in out
    assert "ok" in out
    assert "0.2.0" in out


def test_health_json_emits_raw(mock_air_cli, capsys):
    mock_air_cli({("GET", "/api/v1/health"): (200, HEALTH)})
    rc = main(["health", "--json"])  # flag AFTER subcommand (convention)
    out = capsys.readouterr().out
    assert rc == 0
    parsed = json.loads(out)
    assert parsed["status"] == "ok"
    assert parsed["version"] == "0.2.0"


def test_list_empty(mock_air_cli, capsys):
    mock_air_cli(
        {("GET", "/api/v1/agents"): (200, {"agents": [], "total": 0, "limit": 20, "offset": 0})}
    )
    rc = main(["list", "--no-color"])
    assert rc == 0
    assert "(no agents)" in capsys.readouterr().out


def test_list_with_agents_renders_table(mock_air_cli, capsys):
    body = {
        "agents": [
            {
                "air_id": "AIR-AAAA-BBBB-CCCC",
                "name": "TestBot",
                "description": "",
                "verified": True,
                "verification_level": "kyc-verified",
                "is_demo": False,
                "trust_score": 850,
                "trust_grade": "AA",
                "created": "2026-04-01T00:00:00.000Z",
            },
            {
                "air_id": "AIR-DDDD-EEEE-FFFF",
                "name": "AnotherBot",
                "description": "",
                "verified": False,
                "verification_level": "self-verified",
                "is_demo": True,
                "trust_score": 600,
                "trust_grade": "BBB",
                "created": "2026-04-02T00:00:00.000Z",
            },
        ],
        "total": 2,
        "limit": 20,
        "offset": 0,
    }
    mock_air_cli({("GET", "/api/v1/agents"): (200, body)})
    rc = main(["list", "--no-color"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "AIR ID" in out
    assert "NAME" in out
    assert "SCORE" in out
    assert "AIR-AAAA-BBBB-CCCC" in out
    assert "TestBot" in out
    assert "AIR-DDDD-EEEE-FFFF" in out
    assert "AnotherBot" in out
    assert "Showing 2 of 2" in out


def test_list_pagination_passed_through(mock_air_cli, capsys):
    """--limit and --offset should reach the API."""
    captured = {}

    def make_client(*, base_url: str = "", **_kw: Any) -> AIRClient:
        def handler(request: httpx.Request) -> httpx.Response:
            captured["params"] = dict(request.url.params)
            return httpx.Response(
                200,
                json={"agents": [], "total": 0, "limit": 5, "offset": 10},
            )

        return AIRClient(
            base_url="https://test.invalid",
            transport=httpx.MockTransport(handler),
        )

    import agent_identity_registry.cli as cli_mod
    original = cli_mod.AIRClient
    cli_mod.AIRClient = make_client
    try:
        main(["list", "--limit", "5", "--offset", "10", "--no-color"])
    finally:
        cli_mod.AIRClient = original

    assert captured["params"] == {"limit": "5", "offset": "10"}


def test_lookup_happy_path(mock_air_cli, capsys):
    body = {
        "@context": "https://agentidentityregistry.org/v1",
        "type": "AgentIdentity",
        "air_id": "AIR-AAAA-BBBB-CCCC",
        "name": "TestBot",
        "description": "Test description",
        "creator": {
            "did": "did:web:example.com",
            "name": "Example",
            "type": "organization",
            "public_key": "A" * 43,
        },
        "capabilities": ["test", "demo"],
        "security": {"certifications": []},
        "transparency": {
            "open_source": True,
            "code_repository": "https://github.com/example/test",
            "documentation_url": "https://example.com/docs",
        },
        "verified": True,
        "verification_level": "kyc-verified",
        "is_demo": False,
        "status": "active",
        "created": "2026-04-01T00:00:00.000Z",
        "updated": "2026-04-01T00:00:00.000Z",
        "trust_score": 850,
        "trust_grade": "AA",
        "components": {
            "provenance": 800,
            "behavioral": 900,
            "transparency": 850,
            "security": 800,
            "peer_attestations": 900,
        },
    }
    mock_air_cli({("GET", "/api/v1/agents/AIR-AAAA-BBBB-CCCC"): (200, body)})
    rc = main(["lookup", "AIR-AAAA-BBBB-CCCC", "--no-color"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "AIR-AAAA-BBBB-CCCC" in out
    assert "TestBot" in out
    assert "Test description" in out
    assert "Trust Score:" in out
    assert "850" in out
    assert "AA" in out
    assert "kyc-verified" in out
    assert "github.com/example/test" in out


def test_lookup_404_returns_exit_1_with_friendly_message(mock_air_cli, capsys):
    mock_air_cli(
        {("GET", "/api/v1/agents/AIR-NONE-NONE-NONE"): (404, {"error": "Agent not found"})}
    )
    rc = main(["lookup", "AIR-NONE-NONE-NONE", "--no-color"])
    captured = capsys.readouterr()
    assert rc == 1
    # Friendly message goes to stderr
    assert "Not found" in captured.err
    assert "AIR-NONE-NONE-NONE" in captured.err


def test_score_renders_components_with_bars(mock_air_cli, capsys):
    mock_air_cli({("GET", "/api/v1/agents/AIR-XXXX-YYYY-ZZZZ/trust-score"): (200, TRUST)})
    rc = main(["score", "AIR-XXXX-YYYY-ZZZZ", "--no-color"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "942" in out
    assert "AAA" in out
    assert "provenance" in out
    assert "behavioral" in out
    # bar character should appear
    assert "█" in out or "░" in out
    # weight labels
    assert "weight" in out


def test_did_doc_always_json(mock_air_cli, capsys):
    """DID documents are too nested for pretty printing — always JSON."""
    body = {
        "@context": ["https://www.w3.org/ns/did/v1"],
        "id": "did:wba:agentidentityregistry.org:agents:AIR-X",
        "verificationMethod": [
            {
                "id": "did:wba:...#key-1",
                "type": "Ed25519VerificationKey2020",
                "controller": "did:wba:...",
                "publicKeyMultibase": "z6Mk1234",
            }
        ],
        "authentication": ["did:wba:...#key-1"],
        "assertionMethod": ["did:wba:...#key-1"],
        "service": [],
    }
    mock_air_cli({("GET", "/api/v1/agents/AIR-X/did-document"): (200, body)})
    rc = main(["did-doc", "AIR-X", "--no-color"])
    out = capsys.readouterr().out
    assert rc == 0
    parsed = json.loads(out)
    # camelCase preserved per W3C spec (model_dump uses by_alias=True)
    assert "verificationMethod" in parsed
    assert "publicKeyMultibase" in parsed["verificationMethod"][0]


def test_check_available(mock_air_cli, capsys):
    mock_air_cli(
        {
            ("GET", "/api/v1/agents/check-name"): (
                200,
                {"name": "Fresh", "exists": False, "count": 0, "existing_agents": []},
            )
        }
    )
    rc = main(["check", "Fresh", "--no-color"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "available" in out
    assert "Fresh" in out


def test_check_taken_shows_existing(mock_air_cli, capsys):
    mock_air_cli(
        {
            ("GET", "/api/v1/agents/check-name"): (
                200,
                {
                    "name": "TestBot",
                    "exists": True,
                    "count": 2,
                    "existing_agents": [
                        {"air_id": "AIR-AAAA-BBBB-CCCC", "name": "TestBot"},
                        {"air_id": "AIR-DDDD-EEEE-FFFF", "name": "TestBot"},
                    ],
                },
            )
        }
    )
    rc = main(["check", "TestBot", "--no-color"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "already exists" in out
    assert "AIR-AAAA-BBBB-CCCC" in out
    assert "AIR-DDDD-EEEE-FFFF" in out


def test_register_shows_secret_warning(mock_air_cli, capsys):
    """The agent_secret is shown once — must be visually highlighted."""
    mock_air_cli(
        {
            ("POST", "/api/v1/agents/register"): (
                201,
                {
                    "air_id": "AIR-XYZ1-ABC2-DEF3",
                    "name": "TestBot",
                    "creator_did": "did:wba:agentidentityregistry.org:agents:AIR-XYZ1-ABC2-DEF3",
                    "air_minted_did": True,
                    "status": "active",
                    "verification_level": "self-verified",
                    "trust_score": 540,
                    "trust_grade": "BB",
                    "created": "2026-05-25T00:00:00.000Z",
                    "agent_secret": "abc123" * 5 + "ab",  # 32 chars
                    "agent_secret_note": "Save this.",
                    "message": "ok",
                },
            )
        }
    )
    rc = main(
        ["register", "--name", "TestBot", "--public-key", "A" * 43, "--open-source", "--no-color"]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "AIR-XYZ1-ABC2-DEF3" in out
    assert "SAVE THIS SECRET" in out
    assert "abc123abc123abc123abc123abc123ab" in out  # the actual secret value
    assert "AIR-minted" in out


def test_rate_limit_renders_retry_after(mock_air_cli, capsys):
    mock_air_cli(
        {
            ("POST", "/api/v1/agents/register"): (
                429,
                {"error": "Rate limit exceeded", "retry_after_seconds": 1800},
            )
        }
    )
    rc = main(
        ["register", "--name", "X", "--public-key", "A" * 43, "--no-color"]
    )
    err = capsys.readouterr().err
    assert rc == 1
    assert "Rate limited" in err
    assert "1800" in err


def test_network_error_friendly(monkeypatch, capsys):
    """Transport failure (e.g. DNS) — friendly stderr, exit 1."""

    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated DNS")

    def make_client(*, base_url: str = "", **_kw: Any) -> AIRClient:
        return AIRClient(
            base_url="https://test.invalid",
            transport=httpx.MockTransport(handler),
        )

    monkeypatch.setattr("agent_identity_registry.cli.AIRClient", make_client)
    rc = main(["health", "--no-color"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "Network error" in err


# ============================================================
# ARGPARSE LEVEL TESTS
# ============================================================


def test_no_subcommand_exits_2(capsys):
    """argparse requires a subcommand; missing one is a usage error."""
    with pytest.raises(SystemExit) as ei:
        main([])
    assert ei.value.code == 2


def test_unknown_subcommand_exits_2(capsys):
    with pytest.raises(SystemExit) as ei:
        main(["completely-fake-command"])
    assert ei.value.code == 2


def test_base_url_env_var_default(monkeypatch):
    """build_parser should pick up AIR_BASE_URL from env."""
    from agent_identity_registry.cli import build_parser
    monkeypatch.setenv("AIR_BASE_URL", "https://staging.example.com")
    # Re-build parser AFTER setting env — defaults are resolved at parser-build time
    p = build_parser()
    args = p.parse_args(["health"])
    assert args.base_url == "https://staging.example.com"


def test_no_color_flag_disables_color(monkeypatch):
    """Confirms --no-color makes _should_color() return False."""
    from agent_identity_registry.cli import _should_color, build_parser
    p = build_parser()
    args = p.parse_args(["health", "--no-color"])
    assert _should_color(args) is False


def test_global_flag_accepted_after_subcommand():
    """`air health --json`, `air list --no-color`, etc. must work.

    Catches the live-test bug where `air health --json` errored with
    "unrecognized arguments". Fix: parents=[common] on every subparser
    (without parents= on the top-level parser, to avoid argparse's
    "subparser default overwrites parent value" footgun).
    """
    from agent_identity_registry.cli import build_parser
    p = build_parser()

    # Each flag on each subcommand position
    args = p.parse_args(["health", "--json"])
    assert args.json is True

    args = p.parse_args(["lookup", "AIR-X", "--no-color"])
    assert args.no_color is True
    assert args.air_id == "AIR-X"

    args = p.parse_args(["list", "--base-url", "https://x.example", "--limit", "5"])
    assert args.base_url == "https://x.example"
    assert args.limit == 5


def test_global_flag_before_subcommand_rejected():
    """Top-level position is intentionally NOT supported — env vars cover
    that use case (NO_COLOR=1, AIR_BASE_URL=...). Documented in --help epilog."""
    from agent_identity_registry.cli import build_parser
    p = build_parser()
    with pytest.raises(SystemExit) as ei:
        p.parse_args(["--json", "health"])
    assert ei.value.code == 2  # argparse usage error
