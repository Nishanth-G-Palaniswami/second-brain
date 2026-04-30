"""Which integrations are enabled, and what subcommands each exposes.

`query.py` reads this to build its argparse subparsers. Disabling an integration
(set enabled=False) removes its commands from the CLI without deleting the module.
"""
from __future__ import annotations

INTEGRATIONS: dict[str, dict] = {
    "gmail": {
        "enabled": True,
        "module": "gmail",
        "commands": {
            "triage":     "list unread threads that need attention",
            "thread":     "fetch a single thread by id",
            "draft":      "create a Gmail draft as a reply to a thread",
        },
    },
    "slack": {
        "enabled": True,
        "module": "slack",
        "commands": {
            "attention":  "list new DMs + @mentions since last run",
            "test":       "verify bot token and show workspace info",
        },
    },
    "github": {
        "enabled": True,
        "module": "github",
        "commands": {
            "attention":  "PRs awaiting your review + your open PRs + assigned issues",
            "repos":      "show active repos parsed from USER.md + projects/<slug>/status.md",
            "test":       "verify PAT and show rate-limit headroom",
        },
    },
    "calendar": {
        "enabled": True,
        "module": "calendar",
        "commands": {
            "next": "upcoming events from your Google Calendar primary (shares Gmail OAuth)",
        },
    },
    "jobs": {
        "enabled": True,
        "module": "jobs",
        "commands": {
            "probe":  "detect which free ATS (Greenhouse/Lever/Ashby) each tracked company uses",
            "fetch":  "fetch open job postings for one company",
            "all":    "fetch + write jobs.md for every tracked company",
            "match":  "list profile-matching postings across every company",
        },
    },
}


def enabled() -> dict[str, dict]:
    return {name: cfg for name, cfg in INTEGRATIONS.items() if cfg.get("enabled")}
