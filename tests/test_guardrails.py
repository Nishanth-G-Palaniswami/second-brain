"""Regression: guardrails must block both dot-form and REST-URL-form of Gmail send.

The 2026-04-19 smoke test found that `\\busers\\.messages\\.send\\b` matches the
SDK-style `service.users().messages().send(...)` but not a raw curl to
`https://gmail.googleapis.com/gmail/v1/users/me/messages/send` — the URL uses
slashes, not dots, so the `\\.` boundaries fail. This test locks in coverage
for both forms (and the %2F-encoded variant).

Run:
    .venv\\Scripts\\python.exe tests\\test_guardrails.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / ".claude" / "scripts"))

from security.guardrails import check_tool_call  # noqa: E402


def _deny(label: str, tool: str, inp: dict, expect_substring: str = "boundary 1") -> bool:
    reason = check_tool_call(tool, inp)
    if reason is None:
        print(f"FAIL  deny  {label:<44} -> (allowed!!)")
        return False
    if expect_substring and expect_substring.lower() not in reason.lower():
        print(f"FAIL  deny  {label:<44} -> denied but wrong boundary: {reason!r}")
        return False
    print(f"PASS  deny  {label:<44} -> {reason}")
    return True


def _allow(label: str, tool: str, inp: dict) -> bool:
    reason = check_tool_call(tool, inp)
    if reason is not None:
        print(f"FAIL  allow {label:<44} -> unexpectedly denied: {reason!r}")
        return False
    print(f"PASS  allow {label:<44} -> (allowed)")
    return True


def main() -> int:
    results: list[bool] = []

    # Dot-form (already covered before this fix — keep as regression anchor).
    # The regex `\busers\.messages\.send\b` requires the contiguous literal;
    # it matches API-batch rpc names, log lines, grep targets, etc. It does
    # NOT match `users().messages().send(...)` because of the parens.
    results.append(_deny(
        "dot-form literal users.messages.send",
        "Bash",
        {"command": "grep users.messages.send ./logs/gmail.log"},
    ))

    # REST slash-form — the gap this fix closes.
    results.append(_deny(
        "REST curl /users/me/messages/send",
        "Bash",
        {"command": "curl -X POST 'https://gmail.googleapis.com/gmail/v1/users/me/messages/send' -H 'Authorization: Bearer $T' -d '{}'"},
    ))

    # REST slash-form with an email address as the user id.
    results.append(_deny(
        "REST curl /users/<email>/messages/send",
        "Bash",
        {"command": "curl https://gmail.googleapis.com/gmail/v1/users/foo%40example.com/messages/send"},
    ))

    # Fully %2F-encoded variant (unusual but possible if someone reconstructs the URL).
    results.append(_deny(
        "REST %2F-encoded users%2F...%2Fsend",
        "Bash",
        {"command": "curl 'https://gmail.googleapis.com/gmail%2Fv1%2Fusers%2Fme%2Fmessages%2Fsend'"},
    ))

    # Allowed: drafts endpoint is the legitimate path for Advisor mode.
    results.append(_allow(
        "REST curl /users/me/drafts (create draft)",
        "Bash",
        {"command": "curl -X POST 'https://gmail.googleapis.com/gmail/v1/users/me/drafts'"},
    ))

    # Allowed: listing messages is read-only.
    results.append(_allow(
        "REST curl /users/me/messages (list)",
        "Bash",
        {"command": "curl 'https://gmail.googleapis.com/gmail/v1/users/me/messages?q=is:unread'"},
    ))

    passed = sum(results)
    total = len(results)
    print(f"\n{passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
