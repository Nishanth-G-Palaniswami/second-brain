---
name: project-status
description: Resume cold on any tracked repo by producing a status briefing that covers current state, recent decisions, stakeholders, open PRs and issues, and recent daily-log mentions. Use when the user says "status on X", "where are we with X", "resume X", "catch me up on X", "pick up X", "briefing on X", "what was I doing with X", or just names a tracked project. Pulls from projects/<slug>/ vault files, daily logs, query.py github attention, and memory_search.
---

# Project Status

Produce a resume-friendly briefing for a tracked repo. Combines local vault context with live GitHub signals so the user can pick up a project cold.

**Active repos** are listed in `USER.md` → "Active repos" and canonicalized via `projects/<slug>/status.md`. Run `python .claude/scripts/query.py github repos` to see the resolved list.

## Workflow

1. **Identify the project slug.** Try these in order; stop at the first hit:
   a. **Explicit mention** in the user's message (e.g. "status on example-project").
   b. **Current working directory** — if `basename(cwd)` matches a folder under `${SECOND_BRAIN_VAULT}/projects/`, use that slug.
   c. **Ambiguous** — list the active projects from step 0 of this workflow and ask which one.

   Note: project slugs should match the GitHub repo name exactly. If `USER.md` and the on-disk folder differ (e.g. due to a typo in either), prefer the on-disk folder name for vault reads but normalize for matching against GitHub responses.

2. **Read the project's vault files** in this order, skipping any that don't exist:
   - `projects/<slug>/status.md` — current state + "Next up"
   - `projects/<slug>/decisions.md` — decision log
   - `projects/<slug>/stakeholders.md` — who cares + handles
   - `projects/<slug>/runbook.md` — project-specific commands and recipes

3. **Scan recent daily logs.** For the last 7 days (working backward from today), read `daily/YYYY-MM-DD.md` and grep for mentions of the slug (case-insensitive) or any term from the project's `status.md` "Related repos" section. These mentions are the "what happened lately, not yet landed in status.md" signal.

4. **Fetch live GitHub signals:**
   ```
   python .claude/scripts/query.py github attention --json
   ```
   Parse the JSON. The CLI doesn't have a `--repo` filter yet; filter client-side to items whose `repo` field's name part equals the slug (apply normalization — lowercase, strip non-alphanumerics — to handle slug variants). Bucket by `kind`:
   - `pr_review_requested` → "PRs awaiting your review"
   - `pr_open` → "Your open PRs"
   - `issue_assigned` → "Issues assigned to you"

5. **Backup memory sweep.** Run:
   ```
   python .claude/scripts/rag/memory_search.py "<slug>" --k 5
   ```
   to surface anything useful that didn't land in the structured project files (random daily-log entries, meeting notes, runbooks). Include only hits with a score > 0.4 and that aren't already covered by steps 2–4.

6. **Produce the briefing.** Use exactly these headings so the user can scan it:

   ```
   # <slug> — status as of YYYY-MM-DD

   ## Current state
   <2–4 bullets distilled from status.md + any newer daily-log mentions>

   ## Recent decisions
   <last 3 from decisions.md, or "none recorded">

   ## Stakeholders
   <from stakeholders.md, or "none recorded">

   ## Open PRs / issues (GitHub)
   - PR review:  owner/repo#<n> — <title> — <url>
   - Your PR:    owner/repo#<n> — <title> — <url>
   - Issue:      owner/repo#<n> — <title> — <url>
   <or "(none)" per bucket>

   ## Recent daily-log mentions
   <quote each mention with daily/YYYY-MM-DD.md:<line> cite>

   ## Suggested next action
   <one-sentence recommendation based on status.md "Next up" + the newest
    signal from PRs/issues/daily logs>
   ```

   Cite source files as `projects/<slug>/status.md:12` format when quoting so the user can jump to them in Obsidian.

## Personalization Rules

- User's timezone is read from `USER_TIMEZONE` env var — reflect it in the briefing date.
- The default proactivity stance is **Advisor mode** — suggestions only, never "I'll do X next."
- If the project folder is missing entirely (no `projects/<slug>/` directory), say so clearly and offer to scaffold it from the `projects/README.md` template.
- If GitHub returns nothing for the repo, check whether the slug resolved correctly by running `python .claude/scripts/query.py github repos` before reporting "no activity" — an empty result can mean the PAT is scoped wrong.
- Writes to the vault are UTF-8 markdown with Obsidian-compatible wikilink syntax.
