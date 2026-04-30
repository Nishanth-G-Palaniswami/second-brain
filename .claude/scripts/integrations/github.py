"""GitHub integration — read-only attention list via fine-grained PAT.

Reads active repos from USER.md → "Active repos" section. For each slug, pulls
the `github.com/owner/name` URL out of `projects/<slug>/status.md`. That indirection
keeps USER.md human-friendly (slug + local path) while still letting us hit the API.

Design invariants:
  - PAT stays inside this module; the LLM calls `query.py github ...` and sees
    clean `GHItem` dataclasses, never the token or the client.
  - Token scopes: Contents:Read, Metadata:Read, Pull requests:Read, Issues:Read.
    Nothing that can write — the "never push/merge/deploy" boundary is enforced
    at the PAT layer itself (Phase 8 adds deterministic guardrails on top).
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import _env

PROJECT_ROOT = Path(__file__).resolve().parents[3]
VAULT_ROOT = Path(os.environ.get("SECOND_BRAIN_VAULT", str(Path.home() / "second-brain-vault")))

DEFAULT_PR_SCAN_CAP = 100  # pagination cap per repo to keep rate-limit cost bounded


@dataclass
class GHItem:
    kind: str          # "pr_review_requested" | "pr_open" | "issue_assigned"
    repo: str          # "owner/name"
    number: int
    title: str
    url: str
    author: str
    updated_at: str    # ISO-8601 UTC
    reason: str        # human-readable summary for the briefing

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RepoRef:
    slug: str
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


# ---------------------------------------------------------------------------
# Auth + repo discovery
# ---------------------------------------------------------------------------

def _get_client():
    from github import Auth, Github

    token = _env.require("GITHUB_TOKEN", "fine-grained PAT with Pull requests:Read, Issues:Read, Metadata:Read")
    return Github(auth=Auth.Token(token), per_page=50, timeout=20)

def _login() -> str:
    return _env.require("GITHUB_LOGIN", "your github login (case-sensitive)")


_ACTIVE_SECTION_RE = re.compile(
    r"(?mis)^##\s+Active\s+repos\s*$(?P<body>.*?)(?=^##\s+|\Z)"
)
_BULLET_RE = re.compile(r"^\s*[-*]\s+([A-Za-z0-9_.\-]+)")
_GH_URL_RE = re.compile(r"github\.com[/:]([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+?)(?:\.git)?(?:[\s/)\"]|$)")


def _parse_user_md_slugs() -> list[str]:
    user_md = VAULT_ROOT / "USER.md"
    if not user_md.is_file():
        return []
    text = user_md.read_text(encoding="utf-8", errors="replace")
    m = _ACTIVE_SECTION_RE.search(text)
    if not m:
        return []
    slugs: list[str] = []
    for line in m.group("body").splitlines():
        mb = _BULLET_RE.match(line)
        if mb:
            slugs.append(mb.group(1))
    return slugs


def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _repo_ref_for_slug(slug: str) -> RepoRef | None:
    """Resolve slug → RepoRef via projects/<slug>/status.md.

    Exact-match first. If the folder doesn't exist, fall back to scanning all
    project folders and matching when (a) the normalized folder name equals the
    normalized slug, or (b) the status.md's github repo name normalizes to the
    slug. This tolerates harmless typos in folder names.
    """
    projects_dir = VAULT_ROOT / "projects"
    status = projects_dir / slug / "status.md"
    if status.is_file():
        stext = status.read_text(encoding="utf-8", errors="replace")
        m = _GH_URL_RE.search(stext)
        if m:
            return RepoRef(slug=slug, owner=m.group(1), name=m.group(2))

    if not projects_dir.is_dir():
        return None
    norm_slug = _normalize(slug)
    for child in projects_dir.iterdir():
        if not child.is_dir():
            continue
        child_status = child / "status.md"
        if not child_status.is_file():
            continue
        stext = child_status.read_text(encoding="utf-8", errors="replace")
        m = _GH_URL_RE.search(stext)
        if not m:
            continue
        repo_name = m.group(2)
        if _normalize(child.name) == norm_slug or _normalize(repo_name) == norm_slug:
            return RepoRef(slug=slug, owner=m.group(1), name=repo_name)
    return None


def active_repos() -> list[RepoRef]:
    out: list[RepoRef] = []
    for slug in _parse_user_md_slugs():
        ref = _repo_ref_for_slug(slug)
        if ref is not None:
            out.append(ref)
    return out


# ---------------------------------------------------------------------------
# Attention list
# ---------------------------------------------------------------------------

def _iso(dt) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def list_attention(pr_scan_cap: int = DEFAULT_PR_SCAN_CAP) -> list[GHItem]:
    """Scan active repos for PRs awaiting review, your open PRs, and issues assigned to you."""
    from github import GithubException

    client = _get_client()
    login = _login()
    items: list[GHItem] = []

    for ref in active_repos():
        try:
            repo = client.get_repo(ref.full_name)
        except GithubException as exc:
            sys.stderr.write(f"[github] {ref.full_name}: {exc.data.get('message', exc)}\n")
            continue

        # PRs — single scan, bucket into review-requested vs authored-by-you
        try:
            pulls = repo.get_pulls(state="open", sort="updated", direction="desc")
        except GithubException as exc:
            sys.stderr.write(f"[github] {ref.full_name} pulls: {exc.data.get('message', exc)}\n")
            pulls = []

        seen_prs = 0
        for pr in pulls:
            if seen_prs >= pr_scan_cap:
                break
            seen_prs += 1
            reviewers = [u.login for u in (pr.requested_reviewers or [])]
            author = pr.user.login if pr.user else ""
            if login in reviewers:
                items.append(
                    GHItem(
                        kind="pr_review_requested",
                        repo=ref.full_name,
                        number=pr.number,
                        title=pr.title or "",
                        url=pr.html_url,
                        author=author,
                        updated_at=_iso(pr.updated_at),
                        reason="review requested",
                    )
                )
            elif author == login:
                items.append(
                    GHItem(
                        kind="pr_open",
                        repo=ref.full_name,
                        number=pr.number,
                        title=pr.title or "",
                        url=pr.html_url,
                        author=login,
                        updated_at=_iso(pr.updated_at),
                        reason="your open PR",
                    )
                )

        # Issues assigned to you (the issues endpoint also returns PRs — skip those)
        try:
            issues = repo.get_issues(state="open", assignee=login)
        except GithubException as exc:
            sys.stderr.write(f"[github] {ref.full_name} issues: {exc.data.get('message', exc)}\n")
            issues = []

        for iss in issues:
            if iss.pull_request is not None:
                continue
            items.append(
                GHItem(
                    kind="issue_assigned",
                    repo=ref.full_name,
                    number=iss.number,
                    title=iss.title or "",
                    url=iss.html_url,
                    author=iss.user.login if iss.user else "",
                    updated_at=_iso(iss.updated_at),
                    reason="assigned to you",
                )
            )

    items.sort(key=lambda x: x.updated_at, reverse=True)
    return items


def auth_test() -> dict[str, Any]:
    client = _get_client()
    u = client.get_user()
    rate = client.get_rate_limit()
    return {
        "login": u.login,
        "name": u.name,
        "rate_limit_core": {"remaining": rate.resources.core.remaining, "limit": rate.resources.core.limit},
    }


# ---------------------------------------------------------------------------
# CLI entry points
# ---------------------------------------------------------------------------

def cli_attention(args) -> int:
    items = list_attention(pr_scan_cap=args.pr_cap)
    if args.json:
        print(json.dumps([i.to_json() for i in items], indent=2))
        return 0
    if not items:
        print("(nothing needs your attention)")
        return 0
    kind_label = {
        "pr_review_requested": "PR review",
        "pr_open":             "your PR ",
        "issue_assigned":      "issue   ",
    }
    for i in items:
        tag = kind_label.get(i.kind, i.kind[:8].ljust(8))
        print(f"{tag}  {i.repo}#{i.number:<6} {i.updated_at[:16]}  {i.title[:70]}")
        print(f"          {i.url}  — {i.reason}")
    return 0


def cli_repos(args) -> int:
    refs = active_repos()
    if args.json:
        print(json.dumps([{"slug": r.slug, "owner": r.owner, "name": r.name} for r in refs], indent=2))
        return 0
    if not refs:
        print("(no active repos parsed — check USER.md → Active repos and projects/<slug>/status.md)")
        return 0
    for r in refs:
        print(f"{r.slug:<30} {r.full_name}")
    return 0


def cli_test(args) -> int:
    info = auth_test()
    print(json.dumps(info, indent=2))
    return 0 if info.get("login") else 1
