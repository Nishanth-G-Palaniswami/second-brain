"""Unified CLI for second-brain integrations. Dispatches subcommands to per-integration modules.

Examples:
  python query.py gmail triage
  python query.py gmail triage --max 50 --json
  python query.py gmail thread <thread_id>
  python query.py gmail draft <thread_id> --body-file drafts/active/foo.md

The LLM invokes this as a subprocess and parses stdout. Tokens/credentials never
leave the integration module — the CLI returns structured data only.
"""
from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

# Make `.claude/scripts/` importable regardless of how we're invoked. Do NOT add
# `.claude/scripts/integrations/` to sys.path — that would let local file names
# shadow installed packages (e.g. our `github.py` would shadow PyGithub's
# `github` package). Access integration modules as `integrations.<name>` only.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from integrations.registry import enabled  # noqa: E402


def _build_gmail_subparsers(sub: argparse._SubParsersAction) -> None:
    from integrations import gmail

    p_triage = sub.add_parser("triage", help=gmail.__doc__.splitlines()[0] if gmail.__doc__ else "triage")
    p_triage.add_argument("--query", default=gmail.DEFAULT_TRIAGE_Q)
    p_triage.add_argument("--max", type=int, default=gmail.DEFAULT_TRIAGE_MAX)
    p_triage.add_argument("--json", action="store_true")
    p_triage.set_defaults(func=gmail.cli_triage)

    p_thread = sub.add_parser("thread", help="fetch a single thread")
    p_thread.add_argument("id")
    p_thread.add_argument("--json", action="store_true")
    p_thread.set_defaults(func=gmail.cli_thread)

    p_draft = sub.add_parser("draft", help="create a Gmail draft reply")
    p_draft.add_argument("thread_id")
    p_draft.add_argument("--body", default="")
    p_draft.add_argument("--body-file", default=None)
    p_draft.add_argument("--subject", default=None)
    p_draft.add_argument("--to", default=None, help="override recipient (default: reply to sender)")
    p_draft.set_defaults(func=gmail.cli_draft)

    p_send = sub.add_parser("send-draft",
                            help="push a local drafts/active/*.md file to Gmail as a draft")
    p_send.add_argument("path", help="path to drafts/active/YYYY-MM-DD_email_<slug>.md")
    p_send.set_defaults(func=gmail.cli_send_draft)

    p_sweep = sub.add_parser("sweep",
                             help="promote drafts that were sent + expire stale drafts")
    p_sweep.add_argument("--drafts-root", default=None,
                         help="override default drafts root (SecondBrain/drafts)")
    p_sweep.add_argument("--ttl-hours", type=float, default=gmail.DEFAULT_DRAFT_TTL_HOURS)
    p_sweep.add_argument("--promote-only", action="store_true")
    p_sweep.add_argument("--expire-only", action="store_true")
    p_sweep.add_argument("--json", action="store_true")
    p_sweep.set_defaults(func=gmail.cli_sweep)


def _build_slack_subparsers(sub: argparse._SubParsersAction) -> None:
    from integrations import slack

    p_att = sub.add_parser("attention", help="list new DMs + @mentions")
    p_att.add_argument("--since", default=None,
                       help="override start timestamp (slack ts like 1700000000.000000); "
                            "default is last_run_ts from state, or now-24h on first run")
    p_att.add_argument("--channel-limit", type=int, default=200)
    p_att.add_argument("--ephemeral", action="store_true",
                       help="don't update last_run_ts state (safe for debugging)")
    p_att.add_argument("--json", action="store_true")
    p_att.set_defaults(func=slack.cli_attention)

    p_dm = sub.add_parser("dm", help="fetch the full DM history with a specific person")
    p_dm.add_argument("user",
                      help="Slack user ID (U0XXXXXXX), @handle, or display name "
                           "(matched against users you already share an IM with)")
    p_dm.add_argument("--limit", type=int, default=200,
                      help="max top-level messages to fetch (threaded replies are additive)")
    p_dm.add_argument("--since", default=None,
                      help="only messages newer than this Slack ts (e.g. 1700000000.000000)")
    p_dm.add_argument("--no-threads", action="store_true",
                      help="skip conversations.replies lookups (faster, but drops thread context)")
    p_dm.add_argument("--json", action="store_true")
    p_dm.set_defaults(func=slack.cli_dm)

    p_test = sub.add_parser("test", help="verify bot token")
    p_test.set_defaults(func=slack.cli_test)

    p_utest = sub.add_parser("user-test", help="verify user token (needed for slack dm)")
    p_utest.set_defaults(func=slack.cli_user_test)

    p_chan = sub.add_parser("channel", help="fetch the full history of a Slack channel")
    p_chan.add_argument("channel",
                        help="channel name (#foo, foo) or channel ID (C0XXXXXXX / G0XXXXXXX)")
    p_chan.add_argument("--limit", type=int, default=500,
                        help="max top-level messages to fetch (threaded replies are additive)")
    p_chan.add_argument("--since", default=None,
                        help="only messages newer than this Slack ts (e.g. 1700000000.000000)")
    p_chan.add_argument("--no-threads", action="store_true",
                        help="skip conversations.replies lookups (faster, but drops thread context)")
    p_chan.add_argument("--json", action="store_true")
    p_chan.set_defaults(func=slack.cli_channel)


def _build_calendar_subparsers(sub: argparse._SubParsersAction) -> None:
    from integrations import calendar as cal

    p_next = sub.add_parser("next", help="upcoming calendar events")
    p_next.add_argument("--lookahead", type=int, default=cal.DEFAULT_LOOKAHEAD_MINUTES,
                        help="minutes ahead to scan (default: 720 = 12h)")
    p_next.add_argument("--max", type=int, default=cal.DEFAULT_MAX_RESULTS)
    p_next.add_argument("--calendar", default="primary")
    p_next.add_argument("--json", action="store_true")
    p_next.set_defaults(func=cal.cli_next)


def _build_github_subparsers(sub: argparse._SubParsersAction) -> None:
    from integrations import github as gh

    p_att = sub.add_parser("attention", help="PRs + issues needing your attention")
    p_att.add_argument("--pr-cap", type=int, default=gh.DEFAULT_PR_SCAN_CAP,
                       help="max open PRs scanned per repo (pagination guard)")
    p_att.add_argument("--json", action="store_true")
    p_att.set_defaults(func=gh.cli_attention)

    p_repos = sub.add_parser("repos", help="list active repos")
    p_repos.add_argument("--json", action="store_true")
    p_repos.set_defaults(func=gh.cli_repos)

    p_test = sub.add_parser("test", help="verify PAT and show rate limit")
    p_test.set_defaults(func=gh.cli_test)


def _build_jobs_subparsers(sub: argparse._SubParsersAction) -> None:
    from integrations import jobs
    jobs.build_subparsers(sub)


INTEGRATION_BUILDERS = {
    "gmail":    _build_gmail_subparsers,
    "slack":    _build_slack_subparsers,
    "github":   _build_github_subparsers,
    "calendar": _build_calendar_subparsers,
    "jobs":     _build_jobs_subparsers,
}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser(prog="query.py")
    top = ap.add_subparsers(dest="integration", required=True)

    for name, cfg in enabled().items():
        p = top.add_parser(name, help=f"{name} integration")
        sub = p.add_subparsers(dest="op", required=True)
        builder = INTEGRATION_BUILDERS.get(name)
        if builder is None:
            p.add_argument("_unavailable", nargs="*", help=f"{name} integration module not wired yet")
            continue
        builder(sub)

    args = ap.parse_args()
    if not hasattr(args, "func"):
        ap.print_help()
        return 2
    try:
        return args.func(args)
    except (FileNotFoundError, RuntimeError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
