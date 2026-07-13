# Guardrail decision — two examples

`.claude/scripts/security/guardrails.py` is the deterministic pre-tool-use check that runs before Claude Code executes any tool. It evaluates the proposed `(tool_name, tool_input)` against the rules derived mechanically from `USER.md → Security Boundaries`.

Below are two real evaluations. They are exactly what `tests/test_guardrails.py` exercises — the test passes in CI, so this is what's running in production.

---

## Case 1 — denied: send Gmail via REST

**Proposed call:**

```python
tool_name  = "Bash"
tool_input = {
    "command": "curl -X POST https://gmail.googleapis.com/gmail/v1/users/me/messages/send \\\n"
               "  -H 'Authorization: Bearer ya29.a0...' \\\n"
               "  -H 'Content-Type: application/json' \\\n"
               "  -d '{\"raw\":\"…\"}'"
}
```

**Result:**

```
DENIED — Gmail REST `users/<id>/messages/send` path is disallowed; use drafts only. (USER.md boundary 1)
```

**Why:** Boundary 1 — "Never send emails/messages without approval." The rule pattern catches both the SDK-style call (`service.users().messages().send(...)`) and the REST URL form (`/users/me/messages/send`, including `%2F`-encoded variants). A regression test in `tests/test_guardrails.py` locks in the slash-form + `%2F` cases, after an earlier version of the dot-form regex was found not to match the URL form.

---

## Case 2 — allowed: create Gmail draft

**Proposed call:**

```python
tool_name  = "Bash"
tool_input = {
    "command": "curl -X POST https://gmail.googleapis.com/gmail/v1/users/me/drafts \\\n"
               "  -H 'Authorization: Bearer ya29.a0...' \\\n"
               "  -H 'Content-Type: application/json' \\\n"
               "  -d '{\"message\":{\"raw\":\"…\"}}'"
}
```

**Result:**

```
ALLOWED
```

**Why:** Drafts are explicitly permitted by the design — the agent's whole job is to compose drafts for human review. The Gmail OAuth scope used by `integrations/gmail.py` is `gmail.modify`, which the Gmail API itself refuses to send from. So even if the agent somehow drifted past the regex check, the API layer would reject the send. Two layers, both deterministic.

---

## What this design gives you

- **No prompt-level rules.** The check is a regex table in Python, not "please don't send emails." A drift in agent behavior cannot bypass it.
- **Cited boundaries.** Every rule names the `USER.md` boundary it enforces. Adding a new rule means amending `USER.md` first and then citing it in code — the doc and the code can't drift apart.
- **Tested.** `tests/test_guardrails.py` exercises 6 cases (4 denied, 2 allowed). Adding a new boundary means adding a new test row. CI catches regressions.

See [`.claude/scripts/security/guardrails.py`](../.claude/scripts/security/guardrails.py) for the full rule table and [`tests/test_guardrails.py`](../tests/test_guardrails.py) for the regression suite.
