---
name: triage-inbox
description: Triage the user's Gmail inbox and Slack DMs/@-mentions, then write local draft email replies to drafts/active/ for review. Use when the user says "triage my inbox", "triage", "what's in my inbox", "what needs a reply", "catch me up on email", "morning triage", "check unread", or any variation of inbox-review intent. Calls query.py gmail triage, query.py slack attention, and memory_search against drafts/sent/ for voice matching. Never sends or posts; drafts are local markdown files until the user explicitly approves with query.py gmail draft.
---

# Triage Inbox

Produce a review-ready triage list (Gmail + Slack) and, for threads that warrant a reply per the user's drafting criteria, write local draft files to `${SECOND_BRAIN_VAULT}/drafts/active/`. **Never push anything to Gmail or Slack from this skill** — that's the user's approval step.

## Workflow

1. **Refresh the RAG index** (incremental, cheap no-op if nothing changed):
   ```
   python .claude/scripts/rag/memory_index.py
   ```

2. **Fetch signals** — run both of these. Parallel is fine since they hit different APIs:
   ```
   python .claude/scripts/query.py gmail triage --json
   python .claude/scripts/query.py slack attention --json
   ```
   Parse the JSON. Gmail returns `{id, thread_id, sender, to, subject, snippet, date, labels, unread}` per message. Slack returns `{channel, channel_name, ts, user, user_name, text, permalink, is_dm, is_mention}` per message.

3. **Apply the drafting criteria** (read from `USER.md` in your vault):

   **KEEP** (draft candidate) if ALL apply:
   - User is an explicit recipient (`To:` or `Cc:`) **OR** @-mentioned (Slack/GitHub) **OR** the thread is a DM/mpim.

   **DROP** (skip, report in the skipped list) if ANY apply:
   - Newsletter, marketing, transactional email (receipts, shipping).
   - CI bot: Dependabot, Renovate, GitHub Actions notification.
   - Monitoring / observability alert unless it pages the user directly.
   - Thread older than 7 days with no new activity from a human.

4. **For each kept Gmail thread that warrants a reply:**

   a. Fetch full context:
      ```
      python .claude/scripts/query.py gmail thread <thread_id> --json
      ```

   b. Match voice against past sent replies:
      ```
      python .claude/scripts/rag/memory_search.py "<sender name or topic keywords>" --path-prefix drafts/sent --k 3
      ```
      Use the snippets to calibrate tone, greeting, sign-off, and characteristic phrasing.

   c. Compose a plain-text draft body. Keep it concise. Match voice. Don't invent facts — if the thread asks a factual question you can't answer, say so and flag it.

   d. Write the draft to `${SECOND_BRAIN_VAULT}/drafts/active/YYYY-MM-DD_email_<subject-slug>.md` with this exact structure (the heartbeat in Phase 6 parses it):

      ```markdown
      ---
      type: email
      source_id: <gmail-thread-id>
      recipient: <To address — usually thread.latest_from>
      subject: <Re: original subject>
      created: <ISO-8601 UTC timestamp>
      status: active
      ---

      ## Original Message

      From: <sender>
      Date: <date>
      Subject: <subject>

      <snippet or full latest-message body>

      ## Draft Reply

      <your composed reply, plain text>
      ```

      Slug the subject: lowercase, hyphen-separated, strip punctuation, truncate to ~40 chars. If a file with that name exists today, append `-2`, `-3`, etc.

5. **Slack messages** — for this phase, report them in the triage list with the `permalink` but do NOT write draft files. Slack draft generation is Phase 7 chat work. Call out DMs and @-mentions separately so the user can act in Slack directly.

6. **Summarise** at the end of your response:
   - **Drafts written:** bullet list with file paths, each showing subject + recipient.
   - **Slack attention:** bullet list with channel, sender, permalink.
   - **Skipped:** counts grouped by reason (newsletter, CI bot, stale, etc.).
   - **Failed:** any thread where the API call errored — give the ID + error.

## Never do this

- **Never call `python .claude/scripts/query.py gmail draft`** — that's the user's approval step. It lives in a different skill / manual invocation. This skill writes local markdown only.
- **Never auto-send.** The Gmail scope requested is `gmail.modify` (read + draft); the Gmail API itself refuses to send. If a code path surprises you by trying to send, stop and flag it.
- **Never mark messages read or archive** — triage is advisory only.
- **Never write to `drafts/sent/` or `drafts/expired/`** — those are heartbeat-managed folders.

## Personalization Rules

- User's timezone is read from `USER_TIMEZONE` env var for dated filenames.
- The default proactivity stance is **Advisor mode**: draft for review, never send.
- If a thread has multiple candidate draft points, write one draft for the most recent message in the thread and note the earlier points in the Draft Reply preamble.
- If `memory_search --path-prefix drafts/sent` returns no matches (cold start), write a professional, terse, engineer-to-engineer reply and flag "no voice reference available" in the summary.
- Writes to the vault are UTF-8 markdown with Obsidian-compatible wikilink syntax.
