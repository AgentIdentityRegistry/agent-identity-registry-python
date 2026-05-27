"""Command-line interface for the Agent Identity Registry.

Console-script entry point installed as `air` via pyproject.toml. Wraps the
async SDK in a sync argparse interface with human-friendly default output
and a `--json` flag for scripting.

    air health
    air list [--limit N] [--offset N]
    air lookup AIR-XXXX-XXXX-XXXX
    air score AIR-XXXX-XXXX-XXXX
    air did-doc AIR-XXXX-XXXX-XXXX
    air check <name>
    air register --name N --public-key K [more flags]

Global flags:
    --base-url URL      Override registry URL (default: production)
    --json              Output raw JSON instead of human-friendly format
    --no-color          Disable ANSI color codes

Color is auto-disabled when stdout is not a TTY (e.g., piped to a file).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

from agent_identity_registry.client import DEFAULT_BASE_URL, AIRClient
from agent_identity_registry.exceptions import (
    AgentNotFoundError,
    AirError,
    AuthenticationError,
    NetworkError,
    RateLimitedError,
    ServerError,
    ValidationError,
)

# ============================================================
# COLOR HELPERS — minimal ANSI, no external deps
# ============================================================

_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_BLUE = "\033[34m"


def _should_color(args: argparse.Namespace) -> bool:
    """Color is off when --no-color, NO_COLOR env, or stdout isn't a TTY."""
    if getattr(args, "no_color", False):
        return False
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def _c(args: argparse.Namespace, code: str, text: str) -> str:
    """Wrap text in an ANSI code if color is enabled."""
    return f"{code}{text}{_RESET}" if _should_color(args) else text


# ============================================================
# OUTPUT FORMATTERS
# ============================================================


def _emit_json(payload: Any) -> None:
    """Pretty-print JSON for the --json flag (and for inherently nested data)."""
    print(json.dumps(payload, indent=2, default=str))


def _print_kv(args: argparse.Namespace, key: str, value: Any, key_width: int = 18) -> None:
    """Print a left-aligned key/value pair with optional color on the key."""
    print(f"  {_c(args, _DIM, key.ljust(key_width))} {value}")


# ============================================================
# SUBCOMMAND HANDLERS — each is async, takes (args, client)
# ============================================================


async def cmd_health(args: argparse.Namespace, client: AIRClient) -> int:
    h = await client.get_health()
    if args.json:
        _emit_json(h.model_dump(mode="json"))
        return 0
    print(f"  {_c(args, _BOLD, 'Registry')} {_c(args, _CYAN, h.registry)}")
    _print_kv(args, "status", _c(args, _GREEN, h.status))
    _print_kv(args, "version", h.version)
    return 0


async def cmd_list(args: argparse.Namespace, client: AIRClient) -> int:
    lst = await client.list_agents(limit=args.limit, offset=args.offset)
    if args.json:
        _emit_json(lst.model_dump(mode="json"))
        return 0

    if not lst.agents:
        print(f"  {_c(args, _DIM, '(no agents)')}")
        return 0

    # Compute column widths
    id_w = max(len(a.air_id) for a in lst.agents)
    name_w = max(len(a.name) for a in lst.agents)
    name_w = min(name_w, 40)  # cap name column

    # Header
    header = (
        f"  {_c(args, _BOLD, 'AIR ID'.ljust(id_w))}  "
        f"{_c(args, _BOLD, 'NAME'.ljust(name_w))}  "
        f"{_c(args, _BOLD, 'SCORE'.rjust(5))}  "
        f"{_c(args, _BOLD, 'GRADE')}  "
        f"{_c(args, _BOLD, 'VERIFIED')}"
    )
    print(header)
    print(f"  {_c(args, _DIM, '─' * (id_w + name_w + 24))}")

    # Rows
    for a in lst.agents:
        name = a.name[: name_w - 1] + "…" if len(a.name) > name_w else a.name.ljust(name_w)
        score = str(a.trust_score or "-").rjust(5)
        grade = (a.trust_grade or "-").ljust(5)
        verified = _c(args, _GREEN, "✓") if a.verified else _c(args, _DIM, "·")
        print(
            f"  {_c(args, _CYAN, a.air_id.ljust(id_w))}  "
            f"{name}  "
            f"{score}  "
            f"{grade}  "
            f"{verified}"
        )

    print()
    print(f"  {_c(args, _DIM, f'Showing {len(lst.agents)} of {lst.total} (offset {lst.offset})')}")
    return 0


async def cmd_lookup(args: argparse.Namespace, client: AIRClient) -> int:
    agent = await client.get_agent(args.air_id)
    if args.json:
        _emit_json(agent.model_dump(mode="json"))
        return 0

    print(f"  {_c(args, _CYAN, agent.air_id)}  {_c(args, _BOLD, agent.name)}")
    if agent.description:
        print(f"  {_c(args, _DIM, agent.description)}")
    print()

    # Creator block
    print(f"  {_c(args, _BOLD, 'Creator:')} {agent.creator.name or '(none)'} ({agent.creator.type or '?'})")
    if agent.creator.did:
        _print_kv(args, "did", agent.creator.did)
    if agent.creator.public_key:
        _print_kv(args, "public_key", agent.creator.public_key[:16] + "…")
    print()

    # Trust score block
    if agent.trust_score is not None:
        print(f"  {_c(args, _BOLD, 'Trust Score:')} {agent.trust_score} ({agent.trust_grade})")
        if agent.components:
            for label, val in [
                ("provenance", agent.components.provenance),
                ("behavioral", agent.components.behavioral),
                ("transparency", agent.components.transparency),
                ("security", agent.components.security),
                ("peer_attestations", agent.components.peer_attestations),
            ]:
                _print_kv(args, label, val)
        print()

    # Capabilities + transparency + verification
    if agent.capabilities:
        _print_kv(args, "capabilities", ", ".join(agent.capabilities))
    _print_kv(args, "verified", _c(args, _GREEN, "yes") if agent.verified else "no")
    _print_kv(args, "verification_level", agent.verification_level)
    if agent.transparency.open_source:
        _print_kv(args, "open_source", _c(args, _GREEN, "yes"))
        if agent.transparency.code_repository:
            _print_kv(args, "code_repo", agent.transparency.code_repository)
    _print_kv(args, "created", agent.created.strftime("%Y-%m-%d"))
    return 0


async def cmd_score(args: argparse.Namespace, client: AIRClient) -> int:
    score = await client.get_trust_score(args.air_id)
    if args.json:
        _emit_json(score.model_dump(mode="json"))
        return 0

    print(f"  {_c(args, _CYAN, score.air_id)}  Trust Score: {_c(args, _BOLD, str(score.total_score))} ({score.grade})")
    print()

    # Mini bar chart for the 5 components
    components = [
        ("provenance", score.components.provenance, 0.25),
        ("behavioral", score.components.behavioral, 0.25),
        ("transparency", score.components.transparency, 0.20),
        ("security", score.components.security, 0.15),
        ("peer_attestations", score.components.peer_attestations, 0.15),
    ]
    label_w = max(len(label) for label, _, _ in components)
    for label, val, weight in components:
        bar_len = int(val / 1000 * 20)  # max 20 chars wide
        bar = _c(args, _GREEN, "█" * bar_len) + _c(args, _DIM, "░" * (20 - bar_len))
        print(
            f"  {label.ljust(label_w)}  "
            f"{str(val).rjust(4)}  {bar}  "
            f"{_c(args, _DIM, f'weight {weight:.2f}')}"
        )
    print()
    _print_kv(args, "calculated", score.calculated_at.isoformat())
    return 0


async def cmd_did_doc(args: argparse.Namespace, client: AIRClient) -> int:
    """DID documents are always JSON-shaped — too nested for a pretty table."""
    doc = await client.get_did_document(args.air_id)
    _emit_json(doc.model_dump(mode="json", by_alias=True))
    return 0


async def cmd_check(args: argparse.Namespace, client: AIRClient) -> int:
    result = await client.check_name(args.name)
    if args.json:
        _emit_json(result.model_dump(mode="json"))
        return 0

    if not result.exists:
        print(f"  {_c(args, _GREEN, '✓')} {result.name!r} is available")
    else:
        plural = "agent" if result.count == 1 else "agents"
        print(f"  {_c(args, _YELLOW, '⚠')} {result.name!r} already exists ({result.count} {plural})")
        for ea in result.existing_agents:
            print(f"      {_c(args, _CYAN, ea.air_id)}  {ea.name}")
    return 0


async def cmd_register(args: argparse.Namespace, client: AIRClient) -> int:
    result = await client.register_agent(
        name=args.name,
        public_key=args.public_key,
        creator_did=args.creator_did,
        description=args.description or "",
        creator_name=args.creator_name or "",
        creator_type=args.creator_type,
        capabilities=args.capability,
        open_source=args.open_source,
        code_repository=args.code_repo or "",
        documentation_url=args.docs_url or "",
    )
    if args.json:
        _emit_json(result.model_dump(mode="json"))
        return 0

    print(f"  {_c(args, _GREEN, '✓ Registered:')} {_c(args, _BOLD, result.name)}")
    _print_kv(args, "air_id", _c(args, _CYAN, result.air_id))
    minted_note = "  (AIR-minted)" if result.air_minted_did else ""
    _print_kv(args, "creator_did", f"{result.creator_did}{minted_note}")
    _print_kv(args, "trust_score", f"{result.trust_score} ({result.trust_grade})")
    _print_kv(args, "verification", result.verification_level)
    print()
    print(f"  {_c(args, _YELLOW, '⚠ SAVE THIS SECRET — shown only once:')}")
    print(f"      {_c(args, _BOLD, result.agent_secret)}")
    print(f"      {_c(args, _DIM, 'Store securely. Required to update this agent. Not retrievable later.')}")
    return 0


# ============================================================
# ERROR RENDERING
# ============================================================


def _print_error(args: argparse.Namespace, e: AirError) -> None:
    """Render an AirError as a friendly terminal message.

    All error output goes to stderr so callers piping stdout to a file
    (e.g. `air list --json > agents.json`) get clean data on stdout and
    diagnostic noise on stderr.
    """
    if isinstance(e, AgentNotFoundError):
        print(f"  {_c(args, _RED, '✗ Not found:')} no agent with that AIR ID", file=sys.stderr)
        if e.air_id:
            print(
                f"  {_c(args, _DIM, 'air_id'.ljust(18))} {e.air_id}",
                file=sys.stderr,
            )
    elif isinstance(e, RateLimitedError):
        retry = e.retry_after_seconds or "?"
        print(
            f"  {_c(args, _YELLOW, '⏸  Rate limited:')} "
            f"too many requests. Retry in {retry}s.",
            file=sys.stderr,
        )
    elif isinstance(e, AuthenticationError):
        print(f"  {_c(args, _RED, '✗ Auth failed:')} {e}", file=sys.stderr)
    elif isinstance(e, ValidationError):
        print(f"  {_c(args, _RED, '✗ Invalid input:')} {e}", file=sys.stderr)
    elif isinstance(e, ServerError):
        print(f"  {_c(args, _YELLOW, '⚠ Server error:')} {e}", file=sys.stderr)
    elif isinstance(e, NetworkError):
        print(f"  {_c(args, _YELLOW, '⚠ Network error:')} {e}", file=sys.stderr)
    else:
        print(f"  {_c(args, _RED, '✗ Error:')} {e}", file=sys.stderr)


# ============================================================
# ARGPARSE WIRING
# ============================================================


def build_parser() -> argparse.ArgumentParser:
    # Common flags inherited by every subparser via parents=[common].
    # This makes `--json`, `--no-color`, and `--base-url` work AFTER the
    # subcommand as well as before (the way gh, kubectl, docker accept them).
    # argparse merges the namespaces — the position is invisible to handlers.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--base-url",
        default=os.environ.get("AIR_BASE_URL", DEFAULT_BASE_URL),
        help=f"Registry base URL (default: {DEFAULT_BASE_URL}, or $AIR_BASE_URL)",
    )
    common.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON instead of human-friendly format.",
    )
    common.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI color codes. Also honored: NO_COLOR env var.",
    )

    p = argparse.ArgumentParser(
        prog="air",
        description="Command-line interface for the Agent Identity Registry.",
        epilog=(
            "Global flags (--json, --no-color, --base-url) come AFTER the subcommand, "
            "e.g. `air health --json`. For session-wide preferences use env vars: "
            "NO_COLOR=1, AIR_BASE_URL=https://staging.example.com."
        ),
    )

    sub = p.add_subparsers(dest="command", required=True, metavar="<command>")

    # health
    sub_health = sub.add_parser("health", parents=[common], help="Check registry liveness")
    sub_health.set_defaults(func=cmd_health)

    # list
    sub_list = sub.add_parser(
        "list", parents=[common], help="List agents (sorted by trust score)"
    )
    sub_list.add_argument("--limit", type=int, default=20, help="Max agents to return (default 20)")
    sub_list.add_argument("--offset", type=int, default=0, help="Pagination offset (default 0)")
    sub_list.set_defaults(func=cmd_list)

    # lookup
    sub_lookup = sub.add_parser("lookup", parents=[common], help="Get full record for one agent")
    sub_lookup.add_argument("air_id", help="Format: AIR-XXXX-XXXX-XXXX")
    sub_lookup.set_defaults(func=cmd_lookup)

    # score
    sub_score = sub.add_parser(
        "score", parents=[common], help="Get 5-component trust-score breakdown"
    )
    sub_score.add_argument("air_id", help="Format: AIR-XXXX-XXXX-XXXX")
    sub_score.set_defaults(func=cmd_score)

    # did-doc
    sub_did = sub.add_parser(
        "did-doc", parents=[common], help="Get W3C DID Core JSON-LD document"
    )
    sub_did.add_argument("air_id", help="Format: AIR-XXXX-XXXX-XXXX")
    sub_did.set_defaults(func=cmd_did_doc)

    # check
    sub_check = sub.add_parser(
        "check", parents=[common], help="Check whether a name is already taken"
    )
    sub_check.add_argument("name", help="Agent name to check")
    sub_check.set_defaults(func=cmd_check)

    # register
    sub_reg = sub.add_parser("register", parents=[common], help="Register a new agent")
    sub_reg.add_argument("--name", required=True, help="Agent name (required)")
    sub_reg.add_argument(
        "--public-key",
        help="Ed25519 public key (base64url, 32 bytes). One of --public-key or --creator-did required.",
    )
    sub_reg.add_argument("--creator-did", help="Pre-existing DID (did:wba, did:key, did:web)")
    sub_reg.add_argument("--description", help="Agent description")
    sub_reg.add_argument("--creator-name", help="Creator name (org or individual)")
    sub_reg.add_argument(
        "--creator-type",
        default="individual",
        choices=["individual", "organization"],
        help="Creator type (default: individual)",
    )
    sub_reg.add_argument(
        "--capability",
        action="append",
        help="Capability tag (repeatable, e.g. --capability weather --capability forecast)",
    )
    sub_reg.add_argument("--open-source", action="store_true", help="Mark as open source")
    sub_reg.add_argument("--code-repo", help="Code repository URL")
    sub_reg.add_argument("--docs-url", help="Documentation URL")
    sub_reg.set_defaults(func=cmd_register)

    return p


async def _run(args: argparse.Namespace) -> int:
    """Build the AIRClient, dispatch to the subcommand, handle AirErrors."""
    async with AIRClient(base_url=args.base_url) as client:
        try:
            return await args.func(args, client)
        except AirError as e:
            _print_error(args, e)
            return 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
