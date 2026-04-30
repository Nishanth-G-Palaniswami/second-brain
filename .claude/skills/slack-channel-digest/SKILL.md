---
name: slack-channel-digest
description: Pull the history of a specific Slack channel, analyze it, and maintain that channel's knowledge base under channels/<slug>/ in the vault (overview, dated notes, de-duped resources, append-only decisions log). Push user-owned action items into tasks/today.md, tasks/this-week.md, or tasks/backlog.md. Use when the user says "analyze the #<channel>", "digest #<channel>", "summarize what happened in <channel>", "channel recap <channel>", "what's going on in #<channel>", or any variation that names a single Slack channel and asks for analysis. Calls query.py slack channel and memory_search for prior vault context. Never posts back to Slack — read-and-file only.
---

# Slack channel digest

Pull a channel's history, analyze it, and update that channel's knowledge base under `channels/<slug>/`. Action items the user owns flow into the global task pipeline. Read-only against Slack.

## Workflow

1. **Refresh the RAG index** (incremental, cheap no-op if nothing changed):
   ```
   python .claude/scripts/rag/memory_index.py
   ```

2. **Resolve the channel.** Pass the channel reference verbatim to the CLI — it accepts `#name`, `name`, or `C0XXXXXXX` / `G0XXXXXXX`. If the user only gave a keyword, pass it as-is; `_resolve_channel` does substring matching.

3. **Fetch the channel history:**
   ```
   python .claude/scripts/query.py slack channel "<ref>" --limit 500 --json
   ```
   - Default `--limit 500`. For high-volume channels, bump to 2000+; for a recent slice, set `--since <ts>` instead.
   - Keep `--no-threads` *off* by default — decisions often live in thread replies.
   - If the command errors with `missing_scope`, stop and tell the user which user-token scope is missing (the error message names it). Don't retry with a smaller scope.
   - If the command errors with "could not resolve" or "ambiguous match", **stop and ask the user** which channel ID from the error they meant. Don't guess.

4. **Pull prior vault context** about this channel:
   ```
   python .claude/scripts/rag/memory_search.py "<channel name or topic>" --k 5
   ```

5. **Analyze** the channel history. Extract:
   - **Decisions reached** (not just discussed) — architecture calls, priority pivots, rollouts, rollbacks. These are first-class for channels.
   - **Action items** — who committed to what, with owner and due date. Only user-owned items feed the task pipeline.
   - **Open questions** — things left unresolved.
   - **Current focus** — what is this channel working on *right now* that belongs in `overview.md`.
   - **Resources** — URLs, commits, branches, file paths, snippets, technical constraints.
   - **Participants** — who posts here. Count and rank. If a core set of ~3-8 people appears repeatedly, call them out in `overview.md`.
   Keep an engineer-to-engineer voice per `SOUL.md`: terse, no filler, no sycophancy. Label inferences.

   **Preservation rules — NEVER summarize these away:**
   - Every URL shared by any party — full link verbatim.
   - Every commit hash, PR number, issue number, branch name, file path, tag, version string.
   - Every code block, shell command, config snippet, or error message.
   - Every technical constraint flagged (file-size limits, API quotas, deadlines, infra caveats).
   - Every explicit decision ("we're going with X", "rolled back Y", "picking Z over W") — verbatim, with owner and permalink.
   Any message containing one of the above goes into `notes.md` verbatim under Raw excerpts. Pure chatter ("ok", "thanks", "👍") may be collapsed.

6. **Compute the slug**: channel name lowercased, hyphen-separated, `#` stripped, punctuation stripped. `#server-side-saas` → `server-side-saas`. `#example-v2` → `example-v2`.

7. **Write / update the channel's folder** at `${SECOND_BRAIN_VAULT}/channels/<slug>/`. Four files:

   ### 7a. `overview.md` — canonical front page (link target for tasks)

   On first digest, create with this structure:

   ```markdown
   # #<channel-name>

   **Channel ID:** `<C0XXXXXXX>`
   **Visibility:** public | private
   **Project affinity:** [[projects/<slug>/status]]  (or "general topic channel — no specific project")
   **Core participants:** <3-8 names ranked by post count>

   ## Purpose

   <1–3 lines inferred from the channel's activity: what's discussed here, what's out of scope.>

   ## Current focus (as of YYYY-MM-DD)

   - <1–5 bullets of the active threads / priorities. Updated on every digest.>

   ## Files in this folder

   - [[channels/<slug>/notes]] — dated interaction log
   - [[channels/<slug>/resources]] — links, commits, snippets, constraints
   - [[channels/<slug>/decisions]] — append-only decisions log
   ```

   **On first digest only**, ask the user: "does this channel track a specific project?" If yes, wikilink it in the `Project affinity` line. If no, leave the placeholder text. Don't guess.

   On subsequent digests, **update** the `Current focus (as of YYYY-MM-DD)` bullets + refresh the `Core participants` list if it's drifted. Don't overwrite Purpose unless the scope has clearly changed.

   ### 7b. `notes.md` — dated interaction log, newest first

   **Prepend** a new block at the top:

   ```markdown
   ## YYYY-MM-DD — Channel digest (<window start> → <window end>)

   ### Context

   <1–2 lines.>

   ### Narrative

   <Free-form analysis. Bullets fine. Organize by theme or sub-thread, not pure chronology for high-volume windows.>

   ### Raw excerpts

   - [YYYY-MM-DD HH:MM TZ] **<sender>**: <full text> ([Slack](<permalink>))
   ...
   ```

   Every message with a preservation-rule artifact appears verbatim. Chatter can be collapsed.

   ### 7c. `resources.md` — de-duped index, no dates

   Same contract as for people: sections **Links**, **Commits / branches / refs**, **Commands / snippets**, **Technical constraints flagged**. Merge-don't-overwrite: read existing file, append only entries not already present, with source citation `([Slack](<permalink>))`.

   ### 7d. `decisions.md` — append-only, newest first

   This is where channels differ from people. Each decision gets an entry:

   ```markdown
   ## YYYY-MM-DD — <short decision title>

   **Decision:** <what was decided, one sentence>
   **Rationale:** <why — what tradeoff was made>
   **Owner:** <who owns the consequences>
   **Source:** [Slack](<permalink to the deciding message>)
   ```

   Merge rule: before appending, check whether the same decision is already in the file (by title + date proximity). If so, skip. Never duplicate.

8. **Update the task pipeline.** For each action item the user owns:
   - Line format: `- [ ] <action> [[channels/<slug>/overview]]` with optional `— due YYYY-MM-DD` suffix.
   - Due today/tomorrow → `tasks/today.md`. Within 7 days → `tasks/this-week.md`. Beyond or no deadline → `tasks/backlog.md`.
   - Check whether the same action already exists across the three files before appending. Never duplicate.
   - Action items owned by others are NOT added to the pipeline.

9. **Update `MEMORY.md`** *only if* the digest surfaced a genuine keeper — a load-bearing decision, a durable constraint, a recurring pattern. One line:
   ```
   - <one-line hook>. See [[channels/<slug>/overview]].
   ```
   Keep MEMORY.md under 200 lines.

10. **Report back** to the user:
    - Files created or updated under `channels/<slug>/`.
    - Decisions appended to `decisions.md` (count).
    - Task lines added (which bucket, how many).
    - Top 3 decisions or action items verbatim.
    - Whether `MEMORY.md` was updated.

## Never do this

- **Never send a message, reply, reaction, or post to the channel.** Read-only.
- **Never mark messages read or modify any Slack state.**
- **Never guess a channel ID or project affinity.** Ambiguity → stop and ask.
- **Never duplicate a resource entry, decision line, or task.** Merge with existing files.
- **Never add another party's action items to the task pipeline.**
- **Never tick a task checkbox.** Completed tasks are deleted outright by the user.
- **Never write channel digests to `people/`.** Channels are multi-participant; the people pattern doesn't fit.
- **Never write to `meetings/` for a channel digest** — meetings are calendar events.

## Personalization

- Timezone: read from `USER_TIMEZONE` env var for dated filenames and headings.
- The default proactivity stance is **Advisor mode** per `SOUL.md`: read, draft, file. Never act.
- Voice: engineer-to-engineer, terse, no sycophancy. Cite permalinks over narration.
- If `memory_search` returns prior notes about this channel or its topic, flag conflicts rather than silently overwriting.
- UTF-8 markdown, Obsidian wikilinks for vault-internal references.
