"""Deterministic pre-tool-use guardrails — Phase 8 Layer 2.

Given a Claude Code `PreToolUse` payload (`tool_name`, `tool_input`), decide
whether the action is allowed by the user's USER.md → Security Boundaries.
Return `None` if allowed, or a short reason string if it must be blocked.

The rules table is mechanically derived from the 5 boundaries in USER.md:

    1. Never send emails/messages without approval.
    2. Never post to social media.
    3. Never access financial data / make purchases.
    4. Never delete anything.
    5. Never push code, merge PRs, deploy, or modify production.

Every rule below cites the boundary it enforces. Adding a new rule requires
citing a boundary (or amending USER.md first).

This module is pure-stdlib and has no side effects — it's safe to call from a
hook, from tests, and from the heartbeat.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Rule:
    boundary: str          # one-line summary of which USER.md boundary this enforces
    pattern: re.Pattern[str]
    reason: str            # message returned to Claude when the rule fires


def _rx(pat: str) -> re.Pattern[str]:
    return re.compile(pat, re.IGNORECASE)


# Ordered from most specific to most general. First match wins.
BASH_RULES: list[Rule] = [
    # ---- Boundary 1: never send emails or messages on my behalf ----
    Rule(
        boundary="B1 never send emails/messages",
        pattern=_rx(r"\busers\.messages\.send\b|\bgmail\.send\b"),
        reason="Gmail send is disallowed — use drafts only. (USER.md boundary 1)",
    ),
    Rule(
        # Sibling of the dot-form rule above: catches raw curls to Gmail's REST
        # send endpoint, where the path separator is `/` (or `%2F` when percent-
        # encoded). Without this, e.g. `curl https://gmail.googleapis.com/gmail/
        # v1/users/me/messages/send` would slip through. Defense-in-depth on top
        # of the `gmail.modify`-only OAuth scope.
        boundary="B1 never send emails/messages",
        pattern=_rx(
            r"/users/[^/\s]+/messages/send\b"
            r"|%2Fusers%2F[^/\s%]+%2Fmessages%2Fsend\b"
        ),
        reason="Gmail REST `users/<id>/messages/send` path is disallowed — use drafts only. (USER.md boundary 1)",
    ),
    Rule(
        boundary="B1 never send emails/messages",
        pattern=_rx(r"\bchat\.postMessage\b|\bchat\.scheduleMessage\b|\bchat\.meMessage\b"),
        reason="Slack chat.postMessage is disallowed — draft only. (USER.md boundary 1)",
    ),
    Rule(
        boundary="B1 never send emails/messages",
        pattern=_rx(r"api\.slack\.com/.*\bchat\.(post|schedule|me)"),
        reason="Direct Slack send endpoints are disallowed. (USER.md boundary 1)",
    ),
    Rule(
        boundary="B1 never send emails/messages",
        pattern=_rx(r"\bquery\.py\s+gmail\s+send-draft\b"),
        reason="Creating a Gmail draft is the user's explicit approval step; the agent must not run `gmail send-draft`. (USER.md boundary 1)",
    ),

    # ---- Boundary 2: never post to social media ----
    Rule(
        boundary="B2 never post to social media",
        pattern=_rx(
            r"(?:curl|wget|Invoke-RestMethod|Invoke-WebRequest|fetch|httpx|requests)\b.*"
            r"(?:twitter\.com|x\.com|api\.twitter\.com|api\.x\.com|linkedin\.com|facebook\.com|instagram\.com|threads\.net|bsky\.app|mastodon\.social)"
        ),
        reason="HTTP calls to social-media endpoints are disallowed. (USER.md boundary 2)",
    ),

    # ---- Boundary 3: never access financial data / make purchases ----
    Rule(
        boundary="B3 never access financial data",
        pattern=_rx(r"\b(?:api\.)?(?:stripe|paypal|plaid|checkout)\.com\b"),
        reason="Financial-service domains are disallowed without explicit session allowlist. (USER.md boundary 3)",
    ),
    Rule(
        boundary="B3 never access financial data",
        pattern=_rx(r"/(?:charge|charges|payment(?:s|-intent)?|billing|invoices?|payouts?|transfers?)\b"),
        reason="Payment/billing paths are disallowed. (USER.md boundary 3)",
    ),

    # ---- Boundary 4: never delete anything ----
    Rule(
        boundary="B4 never delete",
        pattern=_rx(r"(?:^|[\s;&|`$(])rm\s+(?!$)"),
        reason="`rm` is disallowed — deletion requires the user. (USER.md boundary 4)",
    ),
    Rule(
        boundary="B4 never delete",
        pattern=_rx(r"(?:^|[\s;&|`$(])del\s+/[sq]"),
        reason="`del /s` / `del /q` is disallowed. (USER.md boundary 4)",
    ),
    Rule(
        boundary="B4 never delete",
        pattern=_rx(r"\bRemove-Item\b|\bri\s+-force\b|\brd\s+/s\b|\brmdir\s+/s\b"),
        reason="Recursive/forced removals are disallowed. (USER.md boundary 4)",
    ),
    Rule(
        boundary="B4 never delete",
        pattern=_rx(r"\bgit\s+branch\s+-D\b|\bgit\s+reset\s+--hard\b|\bgit\s+clean\s+-\w*f\w*\b"),
        reason="Destructive git operations are disallowed. (USER.md boundary 4)",
    ),
    Rule(
        boundary="B4 never delete",
        pattern=_rx(r"\bDROP\s+(?:TABLE|DATABASE|SCHEMA|INDEX|VIEW)\b|\bTRUNCATE\s+TABLE\b|\bDELETE\s+FROM\b"),
        reason="Destructive SQL is disallowed. (USER.md boundary 4)",
    ),
    Rule(
        boundary="B4 never delete",
        pattern=_rx(r"\bmessages\.(?:trash|delete|batchDelete)\b|\bthreads\.(?:trash|delete)\b"),
        reason="Gmail trash/delete endpoints are disallowed. (USER.md boundary 4)",
    ),
    Rule(
        boundary="B4 never delete",
        pattern=_rx(r"\bchat\.delete\b|\bfiles\.delete\b|\bconversations\.(?:archive|close)\b"),
        reason="Slack delete/archive endpoints are disallowed. (USER.md boundary 4)",
    ),

    # ---- Boundary 5: never push, merge, deploy, or modify production ----
    Rule(
        boundary="B5 never push/merge/deploy",
        pattern=_rx(r"\bgit\s+push\b"),
        reason="`git push` is disallowed — push manually after review. (USER.md boundary 5)",
    ),
    Rule(
        boundary="B5 never push/merge/deploy",
        pattern=_rx(r"\bgh\s+(?:pr\s+merge|workflow\s+run|release\s+create|run\s+rerun)\b"),
        reason="`gh pr merge` / `gh workflow run` / `gh release create` are disallowed. (USER.md boundary 5)",
    ),
    Rule(
        boundary="B5 never push/merge/deploy",
        pattern=_rx(r"\bvercel\s+(?:deploy|--prod|promote)\b|\brailway\s+(?:up|deploy)\b|\bsupabase\s+db\s+push\b|\bfly\s+deploy\b|\bnetlify\s+deploy\b|\brender\s+deploy\b"),
        reason="Deploy commands are disallowed. (USER.md boundary 5)",
    ),
    Rule(
        boundary="B5 never push/merge/deploy",
        pattern=_rx(r"\bdocker\s+push\b|\bnpm\s+publish\b|\byarn\s+publish\b|\bpnpm\s+publish\b|\btwine\s+upload\b|\bcargo\s+publish\b"),
        reason="Package / image publish commands are disallowed. (USER.md boundary 5)",
    ),
]


WRITE_DENY_PATH_PATTERNS: list[re.Pattern[str]] = [
    # Never let the agent overwrite `.env` — it holds real secrets.
    re.compile(r"(?:^|[\\/])\.env$", re.IGNORECASE),
    # Never let the agent touch the stored OAuth tokens or API creds.
    re.compile(r"[\\/]creds[\\/].*\.json$", re.IGNORECASE),
]


def check_bash(command: str) -> str | None:
    if not command:
        return None
    for rule in BASH_RULES:
        if rule.pattern.search(command):
            return rule.reason
    return None


def check_write(path: str) -> str | None:
    if not path:
        return None
    for pat in WRITE_DENY_PATH_PATTERNS:
        if pat.search(path):
            return (
                f"Writes to `{path}` are disallowed — this path holds secrets/credentials. "
                "Ask the user to edit it directly."
            )
    return None


def check_tool_call(tool_name: str, tool_input: dict[str, Any] | None) -> str | None:
    """Entry point used by the hook. Returns reason-to-deny or None."""
    tool_input = tool_input or {}
    if tool_name == "Bash":
        return check_bash(tool_input.get("command", "") or "")
    if tool_name in {"Write", "Edit", "NotebookEdit"}:
        return check_write(tool_input.get("file_path", "") or tool_input.get("notebook_path", "") or "")
    return None


# ---------------------------------------------------------------------------
# CLI: smoke-test rules against an arbitrary command.
#   .venv/Scripts/python.exe .claude/scripts/security/guardrails.py "git push origin main"
# ---------------------------------------------------------------------------

def _cli() -> int:
    import sys
    if len(sys.argv) < 2:
        print("usage: guardrails.py <command>  [tool_name]", file=sys.stderr)
        return 2
    cmd = sys.argv[1]
    tool = sys.argv[2] if len(sys.argv) > 2 else "Bash"
    reason = check_tool_call(tool, {"command": cmd, "file_path": cmd})
    if reason is None:
        print(f"ALLOW  tool={tool}  cmd={cmd}")
        return 0
    print(f"DENY   tool={tool}  cmd={cmd}\n       reason: {reason}")
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(_cli())
