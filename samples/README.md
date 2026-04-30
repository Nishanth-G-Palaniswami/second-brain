# Samples

Illustrative artifacts the system produces during normal operation. Values are **synthetic** — fake thread IDs, fake email addresses (`@example.com`), fake names. The schemas match the live runtime so a reader can see the actual shape without running anything.

| File | What it shows |
|---|---|
| [`heartbeat-state.example.json`](heartbeat-state.example.json) | The state file the heartbeat writes after every run — snapshot of inbox/Slack/GitHub/Calendar, habit pillar checks, drafts produced by Claude. |
| [`rag-query.example.md`](rag-query.example.md) | One memory-search query, ranked results, and a short note on the hybrid scoring (vector + keyword). |
| [`guardrail-decision.example.md`](guardrail-decision.example.md) | Two `check_tool_call(...)` evaluations side-by-side — one denied, one allowed — showing how `.claude/scripts/security/guardrails.py` enforces the boundaries in `USER.md`. |

Run the equivalent for real on your own data with:

```
python .claude/scripts/heartbeat.py --dry-run        # writes a real heartbeat-state.json
python .claude/scripts/rag/memory_search.py "<query>" --k 3
python tests/test_guardrails.py                       # exercises the same logic the example shows
```
