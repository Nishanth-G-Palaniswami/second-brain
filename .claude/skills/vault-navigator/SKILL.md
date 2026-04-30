---
name: vault-navigator
description: Teach Claude the folder layout of the second-brain vault so saves land in the right place. Use when the user says "save this", "log this", "remember this", "capture this", "write it down", "where does X go", "file this under", or wants a new runbook, meeting note, project note, decision record, or daily log entry. Covers MEMORY.md index hygiene, Obsidian wikilink conventions, and slug rules.
---

# Vault Navigator

Teach Claude where things go inside the second-brain vault so writes are semantic, Obsidian-friendly, and keep `MEMORY.md` usable as an index.

**Vault root:** `${SECOND_BRAIN_VAULT}` (the env var pointing at the user's vault subfolder, typically inside an Obsidian vault).

## Folder map — pick by intent

| Intent | Target | Notes |
|---|---|---|
| Decision, lesson, or fact worth persisting across sessions | `MEMORY.md` (one-line index entry) + a topic file under the relevant subfolder | Keep `MEMORY.md` under 200 lines — Phase 2 hook lookback truncates past that |
| Scheduled meeting (calendar event) | `meetings/YYYY-MM-DD_<slug>.md` | Use the template at `meetings/_template.md`. Sections: **Attendees**, **Decisions**, **Action Items** (with owners), **Open Questions**. For Slack DM analysis, see the people/ row — DMs are NOT meetings. |
| Per-person knowledge — profile, interaction log, resources they shared, ideas they proposed | `people/<slug>/profile.md` · `notes.md` · `resources.md` · `ideas.md` | Slug: lowercase, hyphen-separated, punctuation stripped (`Alex Chen` → `alex-chen`). `profile.md` is the canonical front page and the link target for tasks. `notes.md` is newest-first; `resources.md` de-duped on append; `ideas.md` grouped by topic. See `slack-dm-digest/SKILL.md` for the write contract. |
| Per-channel knowledge — overview, interaction log, resources, decisions log | `channels/<slug>/overview.md` · `notes.md` · `resources.md` · `decisions.md` | Slug = channel name, lowercase, hyphen-separated, `#` stripped. `overview.md` is the canonical front page and task backlink target. `decisions.md` is append-only, newest first (channels are where decisions happen). See `slack-channel-digest/SKILL.md` for the write contract. |
| Task lists | `tasks/today.md` · `tasks/this-week.md` · `tasks/backlog.md` | Line format: `- [ ] <action> [[people/<slug>/profile]]` or `[[channels/<slug>/overview]]` (context wikilink optional). Manual promotion between buckets; **delete** on completion (no checkbox ticking, no archive). Due today/tomorrow → today; within 7 days → this-week; beyond → backlog. |
| Project-scoped status, decision, or reference | `projects/<slug>/status.md` · `decisions.md` · `stakeholders.md` · `runbook.md` | Slug = lowercase, hyphen-separated, **matches GitHub repo name exactly** (per `projects/README.md`) |
| Cross-project technical how-to (reusable commands, API specs, debugging recipes) | `runbooks/<topic>.md` | Filename lowercase, hyphen-separated |
| Daily working context, open loops, what-I-did notes | `daily/YYYY-MM-DD.md` | Append-only. One file per day. The SessionStart hook loads the last two days automatically |
| Email draft awaiting review | `drafts/active/YYYY-MM-DD_email_<subject-slug>.md` | Use the `triage-inbox` skill for the workflow. Do not write here from `vault-navigator` |
| Prompt / drafts that were actually sent, kept for voice matching | `drafts/sent/` | Populated by the heartbeat after it detects the user sent the reply. Do not write here manually |

## Workflow

1. **Classify the intent** — match the user's request against the folder-map table above. If the intent spans categories (e.g. a meeting that produced a project decision), write the meeting note in `meetings/` and link from `projects/<slug>/decisions.md` using a wikilink. Prefer one canonical home + links over duplication.

2. **Choose a filename** — follow the per-folder filename rule exactly. Dates are `YYYY-MM-DD` in the user's local timezone (read `USER_TIMEZONE` env var). Slugs are lowercase, hyphen-separated. Never include spaces or capitals in filenames.

3. **Write the file** — use the `Write` tool (or `Edit` if appending). All vault files are UTF-8 markdown. For meeting notes, start from `meetings/_template.md`.

4. **Link with Obsidian wikilinks** — inside the vault tree, prefer `[[relative/path/to/note]]` or `[[note-title]]` over markdown `[text](path)` links. Wikilinks make the Obsidian graph view work. Outside the tree (e.g. linking to a GitHub URL), use standard markdown.

5. **Update `MEMORY.md`** — if the new file is the source-of-truth for a persistent decision / rule / lesson, add ONE line to `MEMORY.md` pointing at it, formatted as:
   ```
   - [[projects/example-project/decisions.md#2026-04-19]] — why we picked approach A over B
   ```
   Skip this step for purely transient content (daily logs, meeting summaries, drafts). Keep each line under ~120 chars.

6. **Confirm and cite** — tell the user which file you wrote or updated, including the absolute path. When quoting existing vault content in your response, cite `<relative-path>:<line>` so they can jump to it in Obsidian.

## Personalization Rules

- User's timezone is read from `USER_TIMEZONE` env var — use it for dated filenames.
- Task management lives in **the vault**, not Asana/Linear/Notion. If the user asks "add a task", append to `tasks/today.md`, `tasks/this-week.md`, or `tasks/backlog.md` depending on the deadline (today/tomorrow, within 7 days, or later). If the task came out of a conversation with a specific person, append `[[people/<slug>/profile]]` at the end of the line for backlinks. Never append to a root-level `tasks.md` — that file is deprecated.
- Slug convention is **strict**: lowercase, hyphen-separated, matches the GitHub repo name. When in doubt, run `python .claude/scripts/query.py github repos` for the canonical name.
- Never write to `drafts/sent/` or `drafts/expired/` — those are heartbeat-managed.
- Writes in the vault are UTF-8 markdown with Obsidian wikilink syntax.
