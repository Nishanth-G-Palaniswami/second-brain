---
name: morning-plan
description: Write a prioritized plan for the day in today's daily log, using the latest heartbeat snapshot, open Gmail drafts awaiting review, today's Google Calendar events, and GitHub PRs/issues needing attention. Use when the user says "morning plan", "plan my day", "what should I do today", "morning briefing", "kick off the day", "today's plan", or first thing in the morning. Reads .claude/data/state/heartbeat-state.json, drafts/active/, query.py calendar next, query.py github attention, and writes to daily/YYYY-MM-DD.md.
---

# Morning Plan

Write today's daily-log header and a prioritized plan by combining:
- the latest heartbeat snapshot (`.claude/data/state/heartbeat-state.json`)
- open drafts awaiting the user's review (`${SECOND_BRAIN_VAULT}/drafts/active/*.md`)
- today's Google Calendar events
- GitHub PRs/issues needing the user's attention
- yesterday's "Open loops" from the daily log

## Workflow

1. **Compute today's date** in the user's timezone (read `USER_TIMEZONE` env var, default `UTC`) as `YYYY-MM-DD`. All file names below use it.

2. **Read the heartbeat state.** Load `.claude/data/state/heartbeat-state.json`. The useful keys are `snapshot.gmail`, `snapshot.slack`, `snapshot.github`, `snapshot.calendar`, `habits`, `last_run_at`, `drafts_written`. Skip gracefully if the file is missing — the first morning plan may run before the heartbeat has.

3. **Fetch fresh calendar + GitHub signals** (the snapshot can be up to 30 min stale):
   ```
   python .claude/scripts/query.py calendar next --lookahead 840 --json
   python .claude/scripts/query.py github attention --json
   ```
   840 minutes ≈ 14 hours, covering a typical 8am–10pm working window.

4. **List open drafts.** Read filenames in `${SECOND_BRAIN_VAULT}/drafts/active/`. For each, extract `subject` and `recipient` from the YAML frontmatter — this is the "please review and send" queue.

5. **Read yesterday's open loops.** Read `${SECOND_BRAIN_VAULT}/daily/YYYY-MM-DD.md` for yesterday (today − 1) and extract any `## Open loops` section. These carry forward.

6. **Write today's daily log.** Use the `Write` tool on `${SECOND_BRAIN_VAULT}/daily/<today>.md` with this structure:

   ```markdown
   ---
   date: <today>
   ---

   # <today> — plan

   ## Today's calendar
   - HH:MM  <event summary>  [link if hangout]
   - …

   ## Priorities (top 3)
   1. <most important thing, sourced from PR reviews / DMs / open loops>
   2. …
   3. …

   ## Review queue
   - drafts/active/<file>.md  —  <subject>  →  <recipient>
   - GitHub: <repo>#<n>  <title>  <url>

   ## Carryover from yesterday
   <bullets from yesterday's open loops, or "(none)">

   ## Habits — <today>
   - [ ] main-project
   - [ ] learning
   - [ ] inbox-size

   ## Notes
   <blank — the user fills this as the day goes>

   ## Open loops
   <blank — populated as the day goes>
   ```

   If the file already exists (someone opened the vault and typed something), **append** a new "## Plan generated at HH:MM" section above the first existing content instead of overwriting. Never clobber user edits.

7. **Prioritise sensibly.** The "Priorities" list should pull from, in order of urgency:
   - Calendar events within the next 2 hours that require prep.
   - PR review requests with `updated_at` within the last 24 h.
   - Open drafts that are about to expire (created > 20 h ago).
   - Open loops from yesterday.
   - Your own open PRs that haven't been updated in > 2 days.
   Only 3 items. Merge related items so the top 3 are genuine priorities.

8. **Summarise to the user** in the response: the absolute path of today's daily log, the 3 priorities, and the review queue counts. Cite `file:line` for anything quoted.

## Personalization Rules

- User's timezone is read from the `USER_TIMEZONE` env var (e.g., `America/New_York`, `Europe/London`). Compute today in local time.
- The default proactivity stance is **Advisor mode** — the plan is advisory. Do not mark any habit pillar checked; that's the user's call.
- Do not fetch Gmail triage here — the heartbeat already owns inbox signals. Use `snapshot.gmail` (possibly stale) and note the staleness if `last_run_at` is more than 45 min old.
- If the heartbeat-state file is missing, say so clearly and offer to run `python .claude/scripts/heartbeat.py --force-llm --no-toast` first.
- Writes to the vault are UTF-8 markdown with Obsidian-compatible wikilink syntax.
