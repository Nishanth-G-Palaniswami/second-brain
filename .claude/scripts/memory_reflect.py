"""Daily reflection — scheduled at 07:45 EST.

Reads yesterday's daily log and asks Claude to:
  * extract decisions, lessons, new facts, completed items, open loops
  * promote decisions/lessons to MEMORY.md (or the right project's decisions.md)
  * archive yesterday's HABITS.md checklist to a History section and write a
    fresh one for today
  * append a brief "Yesterday in review" section to yesterday's daily log so
    Obsidian preserves the summary inline

Python does the context gathering; Claude does the judgement + writes.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / ".claude" / "scripts"))

VAULT_ROOT = Path(os.environ.get("SECOND_BRAIN_VAULT", str(Path.home() / "second-brain-vault")))
SOUL_FILE = VAULT_ROOT / "SOUL.md"
DAILY_DIR = VAULT_ROOT / "daily"
HABITS_FILE = VAULT_ROOT / "HABITS.md"
MEMORY_FILE = VAULT_ROOT / "MEMORY.md"

CLAUDE_TIMEOUT_SECONDS = 300


def _venv_env() -> dict[str, str]:
    """Prepend our venv's Scripts dir to PATH so Bash-tool calls in the
    reflection agent run the venv Python (where fastembed/sqlite-vec live)."""
    env = dict(os.environ)
    scripts_dir = str(Path(sys.executable).parent)
    env["PATH"] = scripts_dir + os.pathsep + env.get("PATH", "")
    return env


REFLECT_SYSTEM_PROMPT = """{soul}

You are running the **daily reflection** for {yesterday_date}. Mandate:

1. Read the daily log below. Extract, as crisp bullets:
   - **Decisions** made (with rationale)
   - **Lessons / insights** (what I'd do differently)
   - **New facts** worth remembering across sessions
   - **Completed** items
   - **Open loops** carrying forward
   If a category is empty, skip its heading.

2. **Promote** the important items:
   - Cross-cutting decisions and lessons → append as a dated section to
     `${SECOND_BRAIN_VAULT}/MEMORY.md`, and add a one-line index entry under
     "Decisions" (keep MEMORY.md under 200 lines — prune the oldest entries
     if you'd push past that).
   - Project-specific decisions → append under a dated H2 in
     `${SECOND_BRAIN_VAULT}/projects/<slug>/decisions.md`
     (create the file from a simple `# <slug> — decisions` header if missing).
   - Use Obsidian wikilinks `[[...]]` inside the vault.

3. **Habits**: edit `${SECOND_BRAIN_VAULT}/HABITS.md`:
   - Move yesterday's ({yesterday_date}) checklist into a `## History` section
     (append below if it already exists). Include whether pillars were checked
     and a one-line note if provided in the daily log.
   - Write a fresh `## Today — {today_date}` checklist with the three pillars
     (`main-project`, `learning`, `inbox-size`), all unchecked.

4. **Append to yesterday's daily log** (`${{SECOND_BRAIN_VAULT}}/daily/{yesterday_date}.md`)
   a `## Yesterday in review` section containing the summary you produced in
   step 1. Keep it tight — one bullet per item.

5. Do NOT invoke any skill. You own this workflow directly.

6. Finish with one line:
     REFLECT_OK: files_edited=<count>
"""


def _read_or_empty(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


async def invoke_reflect(yesterday_date: str, today_date: str, yesterday_log: str) -> str:
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ClaudeSDKClient,
        TextBlock,
    )

    soul = _read_or_empty(SOUL_FILE)
    system_prompt = REFLECT_SYSTEM_PROMPT.format(
        soul=soul,
        yesterday_date=yesterday_date,
        today_date=today_date,
    )

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        setting_sources=["project"],
        allowed_tools=["Read", "Write", "Edit", "Bash"],
        permission_mode="acceptEdits",
        cwd=str(PROJECT_ROOT),
        env=_venv_env(),
        skills=[],
        include_partial_messages=False,
        max_turns=15,
    )

    habits_md = _read_or_empty(HABITS_FILE)
    memory_md = _read_or_empty(MEMORY_FILE)

    prompt = (
        f"Yesterday's date: {yesterday_date}\n"
        f"Today's date: {today_date}\n\n"
        f"### Yesterday's daily log (daily/{yesterday_date}.md)\n\n"
        f"{yesterday_log or '_(empty — no log for this date)_'}\n\n"
        f"### Current HABITS.md\n\n"
        f"{habits_md or '_(missing)_'}\n\n"
        f"### Current MEMORY.md\n\n"
        f"{memory_md or '_(missing)_'}\n"
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


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="reflect on this date instead of yesterday (YYYY-MM-DD)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the prompt context and exit without calling Claude")
    args = ap.parse_args()

    today = datetime.now().date()
    yesterday = (today - timedelta(days=1)) if not args.date else datetime.fromisoformat(args.date).date()
    today_str = today.isoformat()
    yesterday_str = yesterday.isoformat()

    log_path = DAILY_DIR / f"{yesterday_str}.md"
    yesterday_log = _read_or_empty(log_path)

    if not yesterday_log.strip():
        print(f"[reflect] no daily log at {log_path} — nothing to reflect on")
        # still roll habits over so tomorrow's checklist is fresh
        if args.dry_run:
            return 0

    print(f"[reflect] yesterday={yesterday_str}  today={today_str}")
    print(f"[reflect] log bytes: {len(yesterday_log)}")

    if args.dry_run:
        print("--- (dry-run — not calling Claude) ---")
        return 0

    try:
        response = asyncio.run(
            asyncio.wait_for(
                invoke_reflect(yesterday_str, today_str, yesterday_log),
                timeout=CLAUDE_TIMEOUT_SECONDS,
            )
        )
    except asyncio.TimeoutError:
        sys.stderr.write(f"[reflect] timed out after {CLAUDE_TIMEOUT_SECONDS}s\n")
        return 1
    except Exception:  # noqa: BLE001
        sys.stderr.write(f"[reflect] failed:\n{traceback.format_exc()}\n")
        return 1

    print("\n===== claude response =====")
    print(response or "(empty)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
