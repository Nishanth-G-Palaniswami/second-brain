---
name: slack-dm-digest
description: Pull the full DM history with a specific person from Slack, analyze it, and update that person's folder under people/ in the vault (profile, interaction log, resources, ideas). Push action items the user owns into tasks/today.md, tasks/this-week.md, or tasks/backlog.md. Use when the user says "analyze my DMs with <person>", "digest my Slack with <person>", "summarize my chat with <person>", "what have <person> and I been talking about", "slack recap <person>", or any variation that names a single person and asks for conversation analysis. Calls query.py slack dm and memory_search for prior vault context. Never posts back to Slack — this is an Advisor-mode read-and-file operation only.
---

# Slack DM digest

Pull the DMs with one specific person, analyze them, and update that person's knowledge base under `people/<slug>/`. Action items the user owns go into the global task pipeline (`tasks/today.md`, `tasks/this-week.md`, `tasks/backlog.md`). Read-only against Slack; all output lands in the vault for review.

## Workflow

1. **Refresh the RAG index** (incremental, cheap no-op if nothing changed):
   ```
   python .claude/scripts/rag/memory_index.py
   ```

2. **Resolve the person.** If the user said a name, pass it straight through — `query.py slack dm` matches against open IMs. If they gave a Slack user ID (`U0XXXXXXX`), pass that verbatim.

3. **Fetch the DM history:**
   ```
   python .claude/scripts/query.py slack dm "<user_ref>" --limit 200 --json
   ```
   - Default `--limit 200`. If the user says "last week" or "today", translate to a Unix timestamp and pass `--since <ts>` to avoid pulling ancient history.
   - Keep `--no-threads` *off* by default — thread context usually holds the substance of the conversation.
   - If the command exits non-zero with "could not resolve" or "ambiguous match", **stop and ask the user** which Slack user ID from the error message they meant. Don't guess.

4. **Pull prior vault context** about this person:
   ```
   python .claude/scripts/rag/memory_search.py "<person name>" --k 5
   ```

5. **Analyze** the DM history. Extract:
   - **Decisions** that were actually reached (not just discussed).
   - **Action items** — who committed to what, with owner and due date if mentioned. Flag which items the user (not the other party) owns; only those go into the task pipeline.
   - **Open questions** — things left unresolved.
   - **Current focus** — what is this person working on *right now* that's worth updating their profile with.
   - **New ideas** — proposals or thinking that weren't already captured.
   - **New resources** — URLs, commits, branches, file paths, snippets, technical constraints they shared.
   Keep an engineer-to-engineer voice per `SOUL.md`: terse, no filler, no sycophancy. Label inferences.

   **Preservation rules — NEVER summarize these away:**
   - Every URL shared by either party — keep the full link verbatim.
   - Every commit hash, PR number, issue number, branch name, file path, tag, version string.
   - Every code block, shell command, config snippet, or error message.
   - Every technical constraint the other party flagged (file-size limits, API quotas, deadlines, infra caveats).
   - Every explicit ask or commitment with a specific artifact ("push to `<branch>`", "grant me access to X").
   Any message containing one of the above goes into `notes.md` verbatim under Raw excerpts, even if the surrounding conversation is chatty. Pure chatter ("ok", "thanks", "👍") may be collapsed.

6. **Compute the slug** for the person's folder: lowercase, hyphen-separated, punctuation stripped. `Alex Chen` → `alex-chen`. `Sarah O'Brien` → `sarah-obrien`. Truncate to ~40 chars if needed.

7. **Write / update the person's folder** at `${SECOND_BRAIN_VAULT}/people/<slug>/`. Four files, each with a specific contract:

   ### 7a. `profile.md` — canonical front page (link target for tasks)

   If the folder is new, create it with this structure:

   ```markdown
   # <Display Name>

   **Slack:** `<U0XXXXXXX>`
   **Role:** <role, inferred from context>
   **Projects:** [[projects/<slug>/status]]  (omit if no related project)

   ## Who

   <1–3 lines on who they are, derived from role + context.>

   ## How to work with them

   - <Observed behavior / preferences from the DMs.>

   ## Current focus (as of YYYY-MM-DD)

   - <1–5 bullets of what they're working on right now. Updated on every digest.>

   ## Files in this folder

   - [[people/<slug>/notes]] — dated interaction log
   - [[people/<slug>/resources]] — links, commits, branches, technical constraints
   - [[people/<slug>/ideas]] — their technical proposals and thinking
   ```

   If the folder exists, **update** the `## Current focus (as of YYYY-MM-DD)` section — replace the date and bullets with what the latest DMs reveal. Also update **How to work with them** if a new observation deserves adding (don't bloat it — 5 bullets max).

   ### 7b. `notes.md` — dated interaction log, newest first

   **Prepend** (do not append) a new block at the top:

   ```markdown
   ## YYYY-MM-DD — Slack DM (<window start> → <window end> window)

   ### Context

   <1–2 lines on what this conversation was about.>

   ### Narrative

   <Free-form analysis. Bullets fine. Capture reasoning, not just conclusions.>

   ### Raw excerpts

   - [YYYY-MM-DD HH:MM TZ] **<sender>**: <full text> ([Slack](<permalink>))
   ...
   ```

   Every message containing a preservation-rule artifact appears verbatim. Chatter can be collapsed.

   ### 7c. `resources.md` — de-duped index, no dates

   Sections: **Links**, **Commits / branches / refs**, **Commands / snippets**, **Technical constraints flagged**.

   **Merge, don't overwrite.** On every run:
   1. Read the existing file (if any).
   2. For each new URL / SHA / branch / file path / constraint / snippet from the DMs, check whether it already appears in the file.
   3. Append only the new ones, under the correct section, with a source citation: `([Slack](<permalink>))` or `(source: [[path/to/note]])`.
   4. Do NOT re-sort or reformat existing entries — that creates noisy diffs.

   ### 7d. `ideas.md` — proposals and thinking, grouped by topic

   Each topic gets a level-2 heading. New ideas go under a new heading (or get appended to an existing matching topic). Cite the source inline: `Source: [[people/<slug>/notes#YYYY-MM-DD]]` or `Source: [[meetings/<slug>]]`.

   Same merge-don't-overwrite rule: leave old ideas alone unless the DMs explicitly revise or retract them.

8. **Update the task pipeline.** For each action item the user (not the other party) owns:
   - Line format: `- [ ] <action> [[people/<slug>/profile]]` with optional `— due YYYY-MM-DD` suffix.
   - If due today or tomorrow → append to `tasks/today.md`.
   - If due within the next 7 days → append to `tasks/this-week.md`.
   - If due more than 7 days out, or no deadline → append to `tasks/backlog.md`.
   - Before appending, check whether the same action is already in any of the three files. If so, either update the existing line (e.g. promote today's deadline to today.md) or skip. Never duplicate.
   - Action items owned by the other party are **not** added to the task pipeline. They can be mentioned in `profile.md` under Current focus if relevant, but the pipeline is the user's commitments only.

9. **Update `MEMORY.md`** *only if* the digest surfaced a genuine keeper — a decision, a durable fact, or a constraint worth remembering across sessions. One line:
   ```
   - <one-line hook>. See [[people/<slug>/profile]].
   ```
   Keep MEMORY.md under 200 lines; `memory_reflect.py` prunes past that.

10. **Report back** to the user with:
    - Files created or updated under `people/<slug>/`.
    - Task lines added (which bucket, how many).
    - The top 3 decisions or action items verbatim.
    - Whether `MEMORY.md` was updated and the line added.

## Never do this

- **Never send a Slack message, reply, DM, or reaction.** This skill only reads.
- **Never mark messages read or modify any Slack state.**
- **Never write Slack drafts to `drafts/active/`.** That folder is for outbound email replies; Slack drafts are a separate future skill.
- **Never guess a user ID.** If `query.py slack dm` returns ambiguity, stop and ask.
- **Never write to `meetings/` for a DM digest.** Slack DMs belong in `people/<slug>/`. Real scheduled meetings (calendar events) still live in `meetings/`.
- **Never duplicate a resource entry or a task line.** Merge with existing files, don't overwrite or re-append.
- **Never add the other party's action items to the task pipeline.** The pipeline is the user's commitments only.
- **Never tick a task checkbox.** Completed tasks are deleted outright — the user will do this manually.

## Personalization

- Timezone for dated filenames and headings: read from `USER_TIMEZONE` env var.
- The default proactivity stance is **Advisor mode** per `SOUL.md`: read, draft, file. Never act.
- Voice: engineer-to-engineer, terse, no sycophancy. Cite `file:line` or permalink rather than narrating.
- If `memory_search` returns prior notes about this person, flag any conflicts explicitly rather than silently overwriting.
- UTF-8 markdown, Obsidian wikilinks (`[[path/to/note]]`) for vault-internal references. Standard markdown for external URLs.
