"""Heartbeat — scheduled every 30 min, 8am–10pm EST on weekdays.

Pipeline (see Phase 6 of the PRD for the rationale):

  1. Sweep the draft lifecycle (promote sent drafts, expire stale ones).
  2. Build a snapshot by fetching Gmail / Slack / GitHub / Calendar in parallel.
  3. Diff against the previous snapshot — stable ids only, no LLM call.
  4. If the diff is empty, persist state and exit. This is the crucial cost guard.
  5. Otherwise: compute habit signals, render a markdown context blob, and
     invoke the Claude Agent SDK with that context. Claude reasons and writes
     local draft files under `drafts/active/` — it never sends to Gmail/Slack.
  6. Parse the response, send a Windows toast summary, persist state.

Python does the fetch, Claude only reasons. Do NOT wire MCP tools into the SDK
options for fetching — it's ~8× more expensive per run.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_ROOT = PROJECT_ROOT / ".claude" / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))

from integrations import gmail, slack, github as gh, calendar as cal  # noqa: E402
from notifier import notify  # noqa: E402
from security.sanitize import escape as _sx, wrap_external  # noqa: E402

VAULT_ROOT = Path(os.environ.get("SECOND_BRAIN_VAULT", str(Path.home() / "second-brain-vault")))
STATE_FILE = PROJECT_ROOT / ".claude" / "data" / "state" / "heartbeat-state.json"
SOUL_FILE = VAULT_ROOT / "SOUL.md"
DAILY_DIR = VAULT_ROOT / "daily"
HABITS_FILE = VAULT_ROOT / "HABITS.md"
DRAFTS_ACTIVE = VAULT_ROOT / "drafts" / "active"
RAG_INDEX_SCRIPT = PROJECT_ROOT / ".claude" / "scripts" / "rag" / "memory_index.py"
MEMORY_DB = PROJECT_ROOT / ".claude" / "data" / "memory.db"

FETCH_TIMEOUT_SECONDS = 45
CLAUDE_TIMEOUT_SECONDS = 300  # 5 minutes
RAG_INDEX_TIMEOUT_SECONDS = 600  # cold-start full index can take minutes
JOBS_REFRESH_INTERVAL_HOURS = 6   # job postings don't change fast; skip if refreshed recently
LATE_DAY_HOUR_LOCAL = 18      # 6pm — trigger habit nudge after this


def _venv_env() -> dict[str, str]:
    """Env for ClaudeAgentOptions — prepends the running interpreter's Scripts
    dir to PATH so Bash-tool invocations pick up the venv Python (PyGithub,
    sqlite-vec, fastembed) instead of the system one."""
    env = dict(os.environ)
    scripts_dir = str(Path(sys.executable).parent)
    env["PATH"] = scripts_dir + os.pathsep + env.get("PATH", "")
    return env


def refresh_job_listings(state: dict[str, Any]) -> None:
    """Refresh job-search/companies/<slug>/jobs.md from free ATS endpoints.

    Rate-limited: skips unless JOBS_REFRESH_INTERVAL_HOURS have passed since the last
    successful refresh (recorded in state['last_jobs_refresh_at']). Never raises —
    job-search data is nice-to-have, not load-bearing for heartbeat.
    """
    last = state.get("last_jobs_refresh_at") or ""
    if last:
        try:
            last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
            age_h = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
            if age_h < JOBS_REFRESH_INTERVAL_HOURS:
                return
        except ValueError:
            pass  # malformed timestamp → treat as never-run
    try:
        from integrations import jobs as jobs_mod  # local import to keep heartbeat cold-path cheap
    except ImportError as exc:
        sys.stderr.write(f"[jobs] import failed: {exc}\n")
        return
    try:
        slugs = jobs_mod.list_tracked_companies()
        total_open = 0
        total_match = 0
        covered = 0
        for slug in slugs:
            postings = jobs_mod.fetch_jobs(slug)
            if not postings:
                continue
            matches = [p for p in postings if jobs_mod.matches_profile(p)]
            jobs_mod.write_jobs_md(slug, postings)
            total_open += len(postings)
            total_match += len(matches)
            covered += 1
        state["last_jobs_refresh_at"] = datetime.now(timezone.utc).isoformat()
        print(
            f"[jobs] refreshed {covered} companies, {total_open} postings, "
            f"{total_match} profile matches"
        )
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"[jobs] refresh failed: {exc}\n")


def refresh_memory_index() -> None:
    """Incrementally re-index the vault; full rebuild on first run. Never raises."""
    if not RAG_INDEX_SCRIPT.is_file():
        return
    args = [sys.executable, str(RAG_INDEX_SCRIPT)]
    if not MEMORY_DB.exists() or MEMORY_DB.stat().st_size == 0:
        args.append("--full")
        print("[rag] cold start — running full index (this may take a few minutes)")
    try:
        result = subprocess.run(
            args,
            cwd=PROJECT_ROOT,
            timeout=RAG_INDEX_TIMEOUT_SECONDS,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            sys.stderr.write(f"[rag] memory_index exit={result.returncode}: {result.stderr[-500:]}\n")
        elif result.stdout.strip():
            last_line = result.stdout.strip().splitlines()[-1]
            print(f"[rag] {last_line}")
    except subprocess.TimeoutExpired:
        sys.stderr.write(f"[rag] memory_index timed out after {RAG_INDEX_TIMEOUT_SECONDS}s\n")
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"[rag] memory_index failed: {exc}\n")


# ---------------------------------------------------------------------------
# Snapshot building
# ---------------------------------------------------------------------------

def _as_json_list(items) -> list[dict]:
    return [i.to_json() if hasattr(i, "to_json") else i for i in (items or [])]


def build_snapshot() -> dict[str, Any]:
    """Parallel fetch from every integration. Failures surface as `{'_error': ...}`."""
    def _gmail():
        return _as_json_list(gmail.list_triage())

    def _slack():
        # Ephemeral fetch — don't advance slack's own last_run_ts from the heartbeat;
        # user-invoked `query.py slack attention` is what owns that state.
        return _as_json_list(slack.list_attention(update_state=False))

    def _github():
        return _as_json_list(gh.list_attention())

    def _calendar():
        return _as_json_list(cal.next_events())

    jobs = {"gmail": _gmail, "slack": _slack, "github": _github, "calendar": _calendar}
    out: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {name: ex.submit(fn) for name, fn in jobs.items()}
        for name, fut in futs.items():
            try:
                out[name] = fut.result(timeout=FETCH_TIMEOUT_SECONDS)
            except FutureTimeout:
                out[name] = {"_error": f"timeout after {FETCH_TIMEOUT_SECONDS}s"}
            except Exception as exc:  # noqa: BLE001
                out[name] = {"_error": f"{type(exc).__name__}: {exc}"}
    out["_built_at"] = datetime.now(timezone.utc).isoformat()
    return out


# ---------------------------------------------------------------------------
# Diffing — stable ids per integration
# ---------------------------------------------------------------------------

def _prev_key_set(prev_list, key_fn) -> set:
    if not isinstance(prev_list, list):
        return set()
    out = set()
    for item in prev_list:
        if isinstance(item, dict):
            try:
                out.add(key_fn(item))
            except (KeyError, TypeError):
                continue
    return out


def diff_snapshot(prev: dict, now: dict) -> dict[str, list[dict]]:
    diff: dict[str, list[dict]] = {"gmail": [], "slack": [], "github": [], "calendar": []}

    prev_gmail = _prev_key_set(prev.get("gmail"), lambda m: m["thread_id"])
    for m in now.get("gmail", []) or []:
        if isinstance(m, dict) and m.get("thread_id") and m["thread_id"] not in prev_gmail:
            diff["gmail"].append(m)

    prev_slack = _prev_key_set(prev.get("slack"), lambda m: (m["channel"], m["ts"]))
    for s in now.get("slack", []) or []:
        if isinstance(s, dict) and (s.get("channel"), s.get("ts")) not in prev_slack:
            diff["slack"].append(s)

    prev_gh = _prev_key_set(
        prev.get("github"),
        lambda g: (g["repo"], g["kind"], g["number"], g["updated_at"]),
    )
    for g in now.get("github", []) or []:
        if isinstance(g, dict):
            key = (g.get("repo"), g.get("kind"), g.get("number"), g.get("updated_at"))
            if key not in prev_gh:
                diff["github"].append(g)

    # Calendar: surface events starting within the next 30 minutes (fresh urgency window)
    soon_cutoff = datetime.now(timezone.utc) + timedelta(minutes=30)
    prev_cal_ids = _prev_key_set(prev.get("calendar"), lambda e: e["id"])
    for e in now.get("calendar", []) or []:
        if not isinstance(e, dict):
            continue
        start = e.get("start", "")
        try:
            dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if dt <= soon_cutoff and e.get("id") not in prev_cal_ids:
            diff["calendar"].append(e)

    return diff


def is_empty_diff(diff: dict) -> bool:
    return not any(v for v in diff.values() if v)


# ---------------------------------------------------------------------------
# Habits (Advisor mode — detect, nudge, never auto-check subjective pillars)
# ---------------------------------------------------------------------------

def _today_local_date() -> str:
    # Use local clock for "today" — heartbeat runs scheduled locally, so the
    # process timezone matches the user's. Set TZ via your USER.md / OS settings.
    return datetime.now().strftime("%Y-%m-%d")


def _today_utc_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


INBOX_SIZE_TARGET = 10  # triage count below this = pillar checked


def check_habits(snapshot: dict) -> dict[str, Any]:
    """Return {pillar: {checked: bool, source: str}} for the three pillars.

    `main-project` — auto-checks if any GitHub attention item for an active repo
                     updated today (UTC).
    `learning`     — suggests only: true if today's daily log contains #learning.
                     Never auto-check (subjective per PRD).
    `inbox-size`   — auto-checks if today's triage count is below
                     INBOX_SIZE_TARGET. Renamed from `inbox-zero` because the
                     threshold is a soft cap, not literal zero.
    """
    today_utc = _today_utc_start()

    gh_items = snapshot.get("github", []) if isinstance(snapshot.get("github"), list) else []
    main_project_active = False
    for item in gh_items:
        try:
            dt = datetime.fromisoformat(item["updated_at"].replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= today_utc:
                main_project_active = True
                break
        except (KeyError, ValueError, AttributeError):
            continue

    daily_path = DAILY_DIR / f"{_today_local_date()}.md"
    learning_flag = False
    if daily_path.is_file():
        try:
            text = daily_path.read_text(encoding="utf-8", errors="replace")
            learning_flag = "#learning" in text.lower()
        except OSError:
            pass

    gmail_items = snapshot.get("gmail", []) if isinstance(snapshot.get("gmail"), list) else []
    inbox_size_ok = len(gmail_items) < INBOX_SIZE_TARGET

    return {
        "main-project": {"checked": main_project_active, "source": "github.updated_at today"},
        "learning":     {"checked": learning_flag,       "source": "#learning tag in today's daily log"},
        "inbox-size":   {"checked": inbox_size_ok,       "source": f"triage count = {len(gmail_items)} (target < {INBOX_SIZE_TARGET})"},
    }


def late_day_nudge_due() -> bool:
    now = datetime.now()
    return now.hour >= LATE_DAY_HOUR_LOCAL and now.weekday() < 5


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if not STATE_FILE.is_file():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Context rendering for Claude
# ---------------------------------------------------------------------------

def render_context(diff: dict[str, list[dict]], habits: dict) -> str:
    # Phase 8 layer 1: every free-text field that originated outside this machine
    # (email body, slack text, github titles, calendar summaries) is passed through
    # `_sx` (sanitize.escape) to defang fenced-code / zero-width / control chars.
    # Each source section is then wrapped in `<external-content>` so the model
    # treats the whole block as data, not as instructions.
    parts: list[str] = [f"_Heartbeat at {datetime.now(timezone.utc).isoformat()}._\n"]

    if diff.get("gmail"):
        body: list[str] = ["## New Gmail threads\n"]
        for m in diff["gmail"]:
            body.append(
                f"- **thread_id**: `{_sx(m.get('thread_id',''))}` — "
                f"**From**: {_sx(m.get('sender',''))} — "
                f"**Subject**: {_sx(m.get('subject',''))}"
            )
            snip = (m.get("snippet") or "").strip()
            if snip:
                body.append(f"  Snippet: {_sx(snip)}")
            to = m.get("to") or []
            if to:
                body.append(f"  To: {_sx(', '.join(to))}")
        parts.append(wrap_external("\n".join(body), source="gmail"))
        parts.append("")

    if diff.get("slack"):
        body = ["## New Slack DMs / @-mentions\n"]
        for s in diff["slack"]:
            tag = "DM" if s.get("is_dm") else "@-mention"
            chan = s.get("channel_name") or s.get("channel") or ""
            text = (s.get("text") or "").replace("\n", " ")[:300]
            body.append(f"- [{tag}] #{_sx(chan)} — {_sx(s.get('user_name',''))}: {_sx(text)}")
            if s.get("permalink"):
                body.append(f"  {s['permalink']}")  # permalinks are ours, skip escape
        parts.append(wrap_external("\n".join(body), source="slack"))
        parts.append("")

    if diff.get("github"):
        body = ["## GitHub attention\n"]
        for g in diff["github"]:
            body.append(
                f"- [{g.get('kind','')}] {_sx(g.get('repo',''))}#{g.get('number','')} — "
                f"{_sx(g.get('title',''))}"
            )
            if g.get("url"):
                body.append(f"  {g['url']}  — {_sx(g.get('reason',''))}")
        parts.append(wrap_external("\n".join(body), source="github"))
        parts.append("")

    if diff.get("calendar"):
        body = ["## Calendar events starting within 30 min\n"]
        for e in diff["calendar"]:
            body.append(f"- {e.get('start','')}  {_sx(e.get('summary',''))}")
            if e.get("hangout_link"):
                body.append(f"  {e['hangout_link']}")
            elif e.get("location"):
                body.append(f"  {_sx(e['location'])}")
        parts.append(wrap_external("\n".join(body), source="calendar"))
        parts.append("")

    parts.append("## Habit signals\n")
    for name, v in habits.items():
        mark = "✓" if v["checked"] else "·"
        parts.append(f"- {mark} **{name}** — {v['source']}")
    if late_day_nudge_due():
        parts.append("\n_Past 6pm — surface any uncompleted habit pillars in the toast summary._")
    parts.append("")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Claude invocation
# ---------------------------------------------------------------------------

HEARTBEAT_SYSTEM_PROMPT = """{soul}

You are running in **heartbeat mode** (a scheduled background pass). Your job:

1. Read the diff below. It lists only new items since the last tick — be brief,
   don't restate them.
2. For Gmail threads that match USER.md drafting criteria, write a **local**
   draft file to
   `${SECOND_BRAIN_VAULT}/drafts/active/YYYY-MM-DD_email_<slug>.md`
   with this exact frontmatter:
     type: email
     source_id: <gmail thread_id>
     recipient: <To address>
     subject: <plain subject, no Re:>
     created: <ISO-8601 UTC timestamp, e.g. 2026-04-19T13:30:00+00:00>
     status: active
   Body: `## Original Message` then `## Draft Reply`. The Draft Reply is plain text.
3. Before composing, run
   `.venv/Scripts/python.exe .claude/scripts/rag/memory_search.py "<sender or topic keywords>" --path-prefix drafts/sent --k 3`
   to sample past voice, then match it. Keep drafts terse and engineer-to-engineer.
   Always use the venv interpreter path `.venv/Scripts/python.exe` — bare
   `python` on this box resolves to system Python and misses our dependencies.
4. Do NOT run `.venv/Scripts/python.exe .claude/scripts/query.py gmail draft`
   or `gmail send-draft` under any circumstance — those push to Gmail, which
   is the user's explicit approval step. You only write local markdown.
5. Do NOT invoke any skill — you are running inside the heartbeat and the
   skills exist for interactive use.
6. Finish your response with a single line:
     DRAFTS_WRITTEN: <comma-separated relative paths or "none">
   Keep everything above it under 12 lines. Brevity matters — this is background.
"""


async def invoke_claude(context_blob: str) -> str:
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ClaudeSDKClient,
        TextBlock,
    )

    soul = SOUL_FILE.read_text(encoding="utf-8") if SOUL_FILE.is_file() else ""
    system_prompt = HEARTBEAT_SYSTEM_PROMPT.format(soul=soul)

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        setting_sources=["project"],          # loads .claude/settings.json hooks
        allowed_tools=["Read", "Write", "Bash"],
        permission_mode="acceptEdits",         # draft writes are safe inside the vault
        cwd=str(PROJECT_ROOT),
        env=_venv_env(),
        skills=[],                             # heartbeat should not auto-invoke skills
        include_partial_messages=False,
        max_turns=8,
    )

    prompt = (
        "Here is what's new since the last heartbeat. Decide what needs attention.\n\n"
        + context_blob
    )

    chunks: list[str] = []
    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in (msg.content or []):
                    if isinstance(block, TextBlock):
                        chunks.append(block.text)
    return "\n".join(chunks).strip()


_DRAFTS_LINE_RE = re.compile(r"^DRAFTS_WRITTEN:\s*(?P<val>.+?)\s*$", re.MULTILINE)


def parse_drafts_written(response: str) -> list[str]:
    if not response:
        return []
    m = _DRAFTS_LINE_RE.search(response)
    if not m:
        return []
    val = m.group("val").strip()
    if val.lower() in {"none", "(none)", "-"}:
        return []
    return [p.strip() for p in val.split(",") if p.strip()]


# ---------------------------------------------------------------------------
# Toast
# ---------------------------------------------------------------------------

def summarise_for_toast(diff: dict, drafts_written: list[str], habits: dict) -> tuple[str, str]:
    n_mail = len(diff.get("gmail") or [])
    n_slack = len(diff.get("slack") or [])
    n_gh = len(diff.get("github") or [])
    n_cal = len(diff.get("calendar") or [])
    n_drafts = len(drafts_written)

    title_bits: list[str] = []
    if n_mail:  title_bits.append(f"{n_mail} mail")
    if n_slack: title_bits.append(f"{n_slack} slack")
    if n_gh:    title_bits.append(f"{n_gh} gh")
    if n_cal:   title_bits.append(f"{n_cal} cal")
    title = "SecondBrain — " + (" · ".join(title_bits) if title_bits else "quiet")

    top = ""
    if diff.get("gmail"):
        m = diff["gmail"][0]
        top = f"📧 {m.get('sender','')[:28]}: {m.get('subject','')[:50]}"
    elif diff.get("slack"):
        s = diff["slack"][0]
        tag = "DM" if s.get("is_dm") else "@"
        top = f"[{tag}] {s.get('user_name','')}: {(s.get('text') or '')[:60]}"
    elif diff.get("github"):
        g = diff["github"][0]
        top = f"GH {g.get('repo','')}#{g.get('number','')} — {g.get('title','')[:60]}"
    elif diff.get("calendar"):
        e = diff["calendar"][0]
        top = f"📅 {e.get('start','')[:16]}  {e.get('summary','')[:50]}"

    extras = []
    if n_drafts:
        extras.append(f"{n_drafts} draft(s) ready for review")
    if late_day_nudge_due():
        unchecked = [k for k, v in habits.items() if not v["checked"]]
        if unchecked:
            extras.append("habits open: " + ", ".join(unchecked))

    body_lines = [top] if top else []
    body_lines += extras
    return title, "\n".join(body_lines)[:240]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _print_section(title: str, body: str) -> None:
    print(f"\n===== {title} =====")
    print(body)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--force-llm", action="store_true", help="invoke Claude even if diff is empty")
    ap.add_argument("--dry-run", action="store_true", help="skip Claude + toast, just print the plan")
    ap.add_argument("--no-toast", action="store_true")
    ap.add_argument("--ttl-hours", type=float, default=gmail.DEFAULT_DRAFT_TTL_HOURS,
                    help="expire active drafts older than this")
    args = ap.parse_args()

    started_at = datetime.now(timezone.utc).isoformat()

    # 1. Draft lifecycle sweep (pure Gmail API + filesystem, no LLM)
    try:
        promoted = gmail.promote_drafts()
        expired = gmail.expire_drafts(ttl_hours=args.ttl_hours)
        if promoted or expired:
            print(f"[sweep] promoted={len(promoted)} expired={len(expired)}")
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"[sweep] failed: {exc}\n")
        promoted, expired = [], []

    # 1a. Refresh RAG index (incremental; first run does --full).
    refresh_memory_index()

    # 1b. Refresh job-search listings (rate-limited; skips if <6h since last refresh).
    prev_state_for_jobs = load_state()
    refresh_job_listings(prev_state_for_jobs)
    # Persist the updated last_jobs_refresh_at so it survives the empty-diff early exit below.
    save_state(prev_state_for_jobs)

    # 2. Snapshot
    snapshot = build_snapshot()
    _print_section("snapshot summary", json.dumps({
        k: (len(v) if isinstance(v, list) else v)
        for k, v in snapshot.items() if not k.startswith("_")
    }, indent=2))

    # 3. Diff
    prev_state = load_state()
    diff = diff_snapshot(prev_state.get("snapshot", {}), snapshot)
    _print_section("diff counts", json.dumps({k: len(v) for k, v in diff.items()}, indent=2))

    habits = check_habits(snapshot)

    # 4. Skip LLM on empty diff
    if is_empty_diff(diff) and not args.force_llm:
        save_state({
            "snapshot": snapshot,
            "habits": habits,
            "last_run_at": started_at,
            "last_diff_at": prev_state.get("last_diff_at"),
            "last_response": prev_state.get("last_response", ""),
        })
        print("[heartbeat] diff empty — skipped LLM")
        return 0

    context_blob = render_context(diff, habits)
    _print_section("context for Claude", context_blob)

    if args.dry_run:
        save_state({
            "snapshot": snapshot,
            "habits": habits,
            "last_run_at": started_at,
            "last_diff_at": started_at,
            "last_response": "(dry-run)",
        })
        print("[heartbeat] dry-run — skipped LLM + toast")
        return 0

    # 5. Invoke Claude
    response_text = ""
    try:
        response_text = asyncio.run(
            asyncio.wait_for(invoke_claude(context_blob), timeout=CLAUDE_TIMEOUT_SECONDS)
        )
    except asyncio.TimeoutError:
        sys.stderr.write(f"[claude] timed out after {CLAUDE_TIMEOUT_SECONDS}s\n")
    except Exception:  # noqa: BLE001
        sys.stderr.write(f"[claude] invocation failed:\n{traceback.format_exc()}\n")

    _print_section("claude response", response_text or "(empty)")
    drafts_written = parse_drafts_written(response_text)

    # 6. Toast
    if not args.no_toast:
        title, body = summarise_for_toast(diff, drafts_written, habits)
        notify(title, body)

    # 7. Persist
    save_state({
        "snapshot": snapshot,
        "habits": habits,
        "last_run_at": started_at,
        "last_diff_at": started_at,
        "last_response": response_text[:4000],
        "drafts_written": drafts_written,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
