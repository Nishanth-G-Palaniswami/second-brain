"""Job listings scraper — free public ATS endpoints (no auth, no paid service).

Supported ATS:
- Greenhouse  (boards-api.greenhouse.io/v1/boards/<slug>/jobs?content=true)
- Lever       (api.lever.co/v0/postings/<slug>?mode=json)
- Ashby       (api.ashbyhq.com/posting-api/job-board/<slug>)
- Amazon Jobs (amazon.jobs/en/search.json) — keyword-based, used for AWS specifically

Each tracked company in `job-search/companies/<slug>/company.md` carries `ats:` and
`ats_slug:` fields in its frontmatter. The `probe` command auto-discovers those by
trying each adapter in turn; `fetch` reads the cached pair directly.

No external dependencies — stdlib urllib + json only. The CLI lives in `query.py`
under the `jobs` integration; this module is also runnable standalone for debugging.

Advisor-mode safe: read-only; never writes outside the vault's `job-search/` subtree.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

VAULT_ROOT = Path(os.environ.get("SECOND_BRAIN_VAULT", str(Path.home() / "second-brain-vault")))
JOB_SEARCH_ROOT = VAULT_ROOT / "job-search" / "companies"

REQUEST_TIMEOUT = 15
POLITE_DELAY_SECONDS = 0.4  # between requests to the same host
USER_AGENT = "Mozilla/5.0 (compatible; SecondBrainJobsBot/1.0; personal-use)"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class JobPosting:
    id: str
    title: str
    company: str
    location: str
    url: str
    posted_at: str  # YYYY-MM-DD or empty
    updated_at: str
    department: str = ""
    ats: str = ""
    description_snippet: str = ""

    def to_json(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _http_get_json(url: str, timeout: int = REQUEST_TIMEOUT) -> tuple[bool, object | None]:
    """Return (ok, parsed_json). ok=True iff HTTP 200 + parseable JSON."""
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status != 200:
                return (False, None)
            raw = r.read().decode("utf-8", errors="replace")
            return (True, json.loads(raw))
    except urllib.error.HTTPError:
        return (False, None)
    except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError, OSError):
        return (False, None)


def _strip_html(s: str, max_chars: int = 240) -> str:
    if not s:
        return ""
    text = re.sub(r"<[^>]+>", "", s)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars] + ("…" if len(text) > max_chars else "")


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"


def fetch_greenhouse(slug: str) -> tuple[bool, list[JobPosting]]:
    ok, data = _http_get_json(GREENHOUSE_URL.format(slug=slug))
    if not ok or not isinstance(data, dict) or "jobs" not in data:
        return (False, [])
    postings: list[JobPosting] = []
    for j in data["jobs"]:
        loc_obj = j.get("location")
        if isinstance(loc_obj, dict):
            loc = loc_obj.get("name", "")
        else:
            loc = str(loc_obj or "")
        posted = (j.get("first_published") or j.get("updated_at") or "")[:10]
        updated = (j.get("updated_at") or "")[:10]
        departments = [
            d.get("name", "")
            for d in (j.get("departments") or [])
            if isinstance(d, dict)
        ]
        postings.append(
            JobPosting(
                id=str(j.get("id", "")),
                title=j.get("title", ""),
                company=slug,
                location=loc,
                url=j.get("absolute_url", ""),
                posted_at=posted,
                updated_at=updated,
                department=" / ".join(x for x in departments if x),
                ats="greenhouse",
                description_snippet=_strip_html(j.get("content", "")),
            )
        )
    return (True, postings)


LEVER_URL = "https://api.lever.co/v0/postings/{slug}?mode=json"


def fetch_lever(slug: str) -> tuple[bool, list[JobPosting]]:
    ok, data = _http_get_json(LEVER_URL.format(slug=slug))
    if not ok or not isinstance(data, list):
        return (False, [])
    postings: list[JobPosting] = []
    for j in data:
        if not isinstance(j, dict):
            continue
        cats = j.get("categories") or {}
        posted = ""
        if j.get("createdAt"):
            try:
                ts = int(j["createdAt"]) / 1000
                posted = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
            except (TypeError, ValueError):
                pass
        postings.append(
            JobPosting(
                id=str(j.get("id", "")),
                title=j.get("text", ""),
                company=slug,
                location=cats.get("location", "") if isinstance(cats, dict) else "",
                url=j.get("hostedUrl", "") or j.get("applyUrl", ""),
                posted_at=posted,
                updated_at=posted,
                department=cats.get("team", "") if isinstance(cats, dict) else "",
                ats="lever",
                description_snippet=_strip_html(
                    j.get("descriptionPlain") or j.get("description", "")
                ),
            )
        )
    return (True, postings)


ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"


def fetch_ashby(slug: str) -> tuple[bool, list[JobPosting]]:
    ok, data = _http_get_json(ASHBY_URL.format(slug=slug))
    if not ok or not isinstance(data, dict) or "jobs" not in data:
        return (False, [])
    postings: list[JobPosting] = []
    for j in data["jobs"]:
        if not isinstance(j, dict):
            continue
        posted = (j.get("publishedAt") or "")[:10]
        postings.append(
            JobPosting(
                id=str(j.get("id", "")),
                title=j.get("title", ""),
                company=slug,
                location=j.get("location", ""),
                url=j.get("jobUrl", "") or j.get("applyUrl", ""),
                posted_at=posted,
                updated_at=posted,
                department=j.get("department", ""),
                ats="ashby",
                description_snippet=_strip_html(j.get("descriptionPlain") or ""),
            )
        )
    return (True, postings)


AMAZON_JOBS_URL = (
    "https://www.amazon.jobs/en/search.json"
    "?base_query={query}"
    "&offset={offset}"
    "&result_limit=50"
    "&sort=recent"
)


def fetch_amazon(slug: str = "amazon-web-services") -> tuple[bool, list[JobPosting]]:
    """Amazon jobs — keyword-driven, not per-company. Runs several queries and dedups by req id.
    Casts a broad SWE/ML/Data net; profile filter downstream narrows to new-grad-shaped titles."""
    queries = [
        "software development engineer 2026",
        "new graduate software",
        "applied scientist new graduate",
        "data engineer",
        "business intelligence engineer",
    ]
    dedup: dict[str, JobPosting] = {}
    detected = False
    for q in queries:
        url = AMAZON_JOBS_URL.format(query=urllib.parse.quote(q), offset=0)
        ok, data = _http_get_json(url)
        if not ok:
            continue
        detected = True
        if not isinstance(data, dict):
            continue
        for j in data.get("jobs", []):
            if not isinstance(j, dict):
                continue
            jid = str(j.get("id_icims", "") or j.get("id", "") or j.get("job_path", ""))
            if not jid or jid in dedup:
                continue
            dedup[jid] = JobPosting(
                id=jid,
                title=j.get("title", ""),
                company=slug,
                location=j.get("normalized_location") or j.get("location") or "",
                url=f"https://www.amazon.jobs{j.get('job_path', '')}",
                posted_at=(j.get("posted_date") or "")[:10],
                updated_at=(j.get("updated_time") or j.get("posted_date") or "")[:10],
                department=j.get("business_category", ""),
                ats="amazon-jobs",
                description_snippet=_strip_html(
                    j.get("description_short", "") or j.get("description", "")
                ),
            )
        time.sleep(POLITE_DELAY_SECONDS)
    return (detected, list(dedup.values()))


WORKDAY_API_URL = "https://{tenant}.{region}.myworkdayjobs.com/wday/cxs/{tenant}/{board}/jobs"


def _http_post_json(url: str, body: dict, timeout: int = REQUEST_TIMEOUT) -> tuple[bool, object | None]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status != 200:
                return (False, None)
            return (True, json.loads(r.read().decode("utf-8", errors="replace")))
    except urllib.error.HTTPError:
        return (False, None)
    except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError, OSError):
        return (False, None)


def fetch_workday(ats_slug: str) -> tuple[bool, list[JobPosting]]:
    """Workday adapter. `ats_slug` encodes `tenant@region/board` (e.g. 'geico@wd5/External').

    Workday exposes a POST JSON API at
    <tenant>.<region>.myworkdayjobs.com/wday/cxs/<tenant>/<board>/jobs with paging.
    Different companies deploy to different regions (wd1/wd5/wd12/etc.) and different
    board slugs — these must be known from the careers-page URL.
    """
    if "@" not in ats_slug or "/" not in ats_slug:
        return (False, [])
    tenant_region, _, board = ats_slug.partition("/")
    tenant, _, region = tenant_region.partition("@")
    if not tenant or not region or not board:
        return (False, [])

    url = WORKDAY_API_URL.format(tenant=tenant, region=region, board=board)
    dedup: dict[str, JobPosting] = {}
    detected = False
    for offset in (0, 20, 40):
        body = {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": ""}
        ok, data = _http_post_json(url, body)
        if not ok or not isinstance(data, dict):
            break
        detected = True
        postings = data.get("jobPostings") or []
        if not postings:
            break
        for j in postings:
            if not isinstance(j, dict):
                continue
            external_path = j.get("externalPath", "") or ""
            jid = external_path.split("/")[-1] if external_path else str(id(j))
            if jid in dedup:
                continue
            dedup[jid] = JobPosting(
                id=jid,
                title=j.get("title", ""),
                company=tenant,
                location=j.get("locationsText", "") or "",
                url=f"https://{tenant}.{region}.myworkdayjobs.com/en-US/{board}{external_path}",
                posted_at="",  # Workday returns "Posted X Days Ago" — not an ISO date
                updated_at="",
                department="",
                ats="workday",
                description_snippet=j.get("postedOn", "") or "",
            )
        if len(postings) < 20:
            break
        time.sleep(POLITE_DELAY_SECONDS)
    return (detected, list(dedup.values()))


ADAPTERS: dict[str, Callable[[str], tuple[bool, list[JobPosting]]]] = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "amazon-jobs": fetch_amazon,
    "workday": fetch_workday,
}


# ---------------------------------------------------------------------------
# Slug variants + probe
# ---------------------------------------------------------------------------

def _slug_variants(folder_slug: str, company_name: str) -> list[str]:
    """Plausible ATS-side slug variants for a company. ATS vendors use the company's
    chosen slug, which often differs from our folder-level slug."""
    variants = {folder_slug}
    variants.add(folder_slug.replace("-", ""))
    variants.add(folder_slug.replace("-", "_"))
    for suffix in ("-inc", "-international", "-technologies", "-solutions", "-ai", "-io"):
        if folder_slug.endswith(suffix):
            variants.add(folder_slug[: -len(suffix)])
    if company_name:
        cn = company_name.lower()
        normalized = re.sub(r"[^a-z0-9]+", "", cn)
        if normalized:
            variants.add(normalized)
        first_word = cn.split()[0] if cn.split() else ""
        if first_word:
            variants.add(first_word)
    return [v for v in sorted(variants) if v]


def probe(folder_slug: str, company_name: str = "") -> tuple[str, str] | None:
    """Return (ats_name, ats_slug) on success, else None."""
    # Amazon special-case
    lower = (folder_slug + " " + company_name).lower()
    if "amazon" in lower:
        ok, _ = fetch_amazon(folder_slug)
        if ok:
            return ("amazon-jobs", folder_slug)

    for variant in _slug_variants(folder_slug, company_name):
        for ats_name in ("greenhouse", "lever", "ashby"):
            ok, _ = ADAPTERS[ats_name](variant)
            if ok:
                return (ats_name, variant)
            time.sleep(POLITE_DELAY_SECONDS)
    return None


# ---------------------------------------------------------------------------
# Profile-match filter
# ---------------------------------------------------------------------------

SENIOR_TITLE_RE = re.compile(
    r"\b(senior|staff|principal|lead|director|manager|vp|head\s+of|chief)\b",
    re.I,
)
RELEVANT_TITLE_RE = re.compile(
    r"\b(software\s+engineer|swe|sde|software\s+development|"
    r"ml\s+engineer|machine\s+learning\s+engineer|"
    r"data\s+engineer|data\s+scientist|applied\s+scientist|research\s+engineer|"
    r"ml\s*infrastructure|ml\s*platform|data\s+platform|"
    r"business\s+intelligence|bi\s+engineer|full[- ]?stack|backend|"
    r"mlops|ml\s*ops|analytics\s+engineer|research\s+scientist|"
    r"product\s+engineer|platform\s+engineer|sre|site\s+reliability)\b",
    re.I,
)
NEW_GRAD_RE = re.compile(
    r"\b(new\s*grad|entry[-\s]?level|early[-\s]?career|university|college|"
    r"2026|recent\s+graduate|intern|l[134])\b",
    re.I,
)
US_LOCATION_RE = re.compile(
    r"\b(new\s+york|nyc|brooklyn|manhattan|san\s+francisco|sf|seattle|"
    r"boston|austin|remote|united\s+states|\bus\b|usa|hybrid|"
    r"nj|new\s+jersey|ny|ca|wa|tx|ma)\b",
    re.I,
)
NON_US_LOCATION_RE = re.compile(
    r"\b(london|uk|united\s+kingdom|dublin|ireland|berlin|munich|paris|"
    r"singapore|tokyo|seoul|bangalore|hyderabad|mumbai|chennai|delhi|"
    r"toronto|vancouver|sydney|melbourne|amsterdam|barcelona|madrid|"
    r"tel\s+aviv|shanghai|shenzhen|beijing|zurich|geneva)\b",
    re.I,
)


def matches_profile(p: JobPosting) -> bool:
    """True iff the posting looks like a fit for a 2026 new-grad SWE/ML/Data target profile."""
    title = p.title or ""
    # Reject senior titles unless also tagged as new-grad / L1-3
    if SENIOR_TITLE_RE.search(title) and not NEW_GRAD_RE.search(title):
        return False
    if not RELEVANT_TITLE_RE.search(title):
        return False
    # Location: reject if clearly non-US; accept if US or unspecified
    loc = p.location or ""
    if loc and NON_US_LOCATION_RE.search(loc) and not US_LOCATION_RE.search(loc):
        return False
    return True


# ---------------------------------------------------------------------------
# Vault I/O
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def read_company_frontmatter(folder_slug: str, vault_root: Path = VAULT_ROOT) -> dict:
    company_md = vault_root / "job-search" / "companies" / folder_slug / "company.md"
    if not company_md.exists():
        return {}
    text = company_md.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    fm: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith(" "):
            key, _, val = line.partition(":")
            fm[key.strip()] = val.strip().strip('"').strip("'")
    return fm


def set_company_ats(folder_slug: str, ats: str, ats_slug: str,
                    vault_root: Path = VAULT_ROOT) -> bool:
    """Upsert ats + ats_slug into company.md frontmatter."""
    company_md = vault_root / "job-search" / "companies" / folder_slug / "company.md"
    if not company_md.exists():
        return False
    text = company_md.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return False
    fm_lines = m.group(1).splitlines()
    existing: dict[str, int] = {}
    for i, ln in enumerate(fm_lines):
        if ":" in ln and not ln.startswith(" "):
            existing[ln.partition(":")[0].strip()] = i

    def upsert(key: str, val: str) -> None:
        line = f'{key}: "{val}"'
        if key in existing:
            fm_lines[existing[key]] = line
        else:
            fm_lines.append(line)
            existing[key] = len(fm_lines) - 1

    upsert("ats", ats)
    upsert("ats_slug", ats_slug)

    new_fm = "\n".join(fm_lines)
    new_text = text.replace(m.group(0), f"---\n{new_fm}\n---\n", 1)
    company_md.write_text(new_text, encoding="utf-8")
    return True


def list_tracked_companies(vault_root: Path = VAULT_ROOT) -> list[str]:
    root = vault_root / "job-search" / "companies"
    if not root.is_dir():
        return []
    return sorted(
        p.name for p in root.iterdir()
        if p.is_dir() and (p / "company.md").exists()
    )


def fetch_jobs(folder_slug: str, *, vault_root: Path = VAULT_ROOT,
               force_probe: bool = False) -> list[JobPosting]:
    """Fetch jobs for one company. Uses cached ats+ats_slug if present; probes otherwise.

    If `ats:` is present in frontmatter but empty/invalid, treats it as intentionally
    cleared (e.g. a prior probe hit a false-positive that was manually wiped) and
    returns [] without re-probing. Pass `force_probe=True` to override.
    """
    fm = read_company_frontmatter(folder_slug, vault_root=vault_root)

    if not force_probe and "ats" in fm:
        ats = fm.get("ats", "").strip()
        ats_slug = fm.get("ats_slug", "").strip()
        if ats in ADAPTERS and ats_slug:
            _, postings = ADAPTERS[ats](ats_slug)
            return postings
        # ats key exists but is empty or unsupported → intentionally skipped
        return []

    # No ats field at all — never probed; run auto-discovery
    company_name = fm.get("name", folder_slug.replace("-", " ").title())
    discovered = probe(folder_slug, company_name)
    if discovered is None:
        # Record that no free ATS was discoverable so we don't re-probe every run
        set_company_ats(folder_slug, "", "", vault_root=vault_root)
        return []
    ats_name, discovered_slug = discovered
    set_company_ats(folder_slug, ats_name, discovered_slug, vault_root=vault_root)
    _, postings = ADAPTERS[ats_name](discovered_slug)
    return postings


def write_jobs_md(folder_slug: str, postings: list[JobPosting],
                  *, vault_root: Path = VAULT_ROOT) -> Path:
    """Render job-search/companies/<slug>/jobs.md with Dataview-friendly metadata."""
    company_dir = vault_root / "job-search" / "companies" / folder_slug
    company_dir.mkdir(parents=True, exist_ok=True)
    jobs_md = company_dir / "jobs.md"

    matches = [p for p in postings if matches_profile(p)]
    others = [p for p in postings if not matches_profile(p)]
    now = datetime.now(timezone.utc).date().isoformat()
    ats = postings[0].ats if postings else ""

    lines: list[str] = [
        "---",
        f"name: {folder_slug} — Open Roles",
        f'last_refreshed: "{now}"',
        f"source_ats: {ats or 'none'}",
        f"total_open: {len(postings)}",
        f"profile_matches: {len(matches)}",
        "type: jobs-listing",
        "---",
        "",
        "<!-- graph-links:begin -->",
        "## Related",
        "",
        f"- **Company:** [[SecondBrain/job-search/companies/{folder_slug}/company|{folder_slug}]]",
        "",
        "<!-- graph-links:end -->",
        "",
        f"# {folder_slug} — Open Roles",
        "",
        f"_Source: `{ats or 'none'}`. Refreshed {now}. "
        f"{len(postings)} open; {len(matches)} match my new-grad/junior profile._",
        "",
    ]

    def _render(p: JobPosting) -> list[str]:
        out = [
            f"### {p.title}",
            f"- **Req ID:** `{p.id}`",
            f"- **Location:** {p.location or '—'}",
            f"- **Department:** {p.department or '—'}",
            f"- **Posted:** {p.posted_at or '—'}",
            f"- **URL:** {p.url}",
        ]
        if p.description_snippet:
            out.append(f"- **Snippet:** _{p.description_snippet}_")
        out.append("")
        return out

    lines.append("## 🎯 Profile matches (new-grad / junior SWE / ML / Data)")
    lines.append("")
    if matches:
        for p in matches[:40]:
            lines.extend(_render(p))
    else:
        lines.append("_No profile matches at this refresh._")
        lines.append("")

    if others:
        lines.append(f"## Other open roles ({len(others)})")
        lines.append("")
        for p in others[:30]:
            lines.extend(_render(p))
        if len(others) > 30:
            lines.append(
                f"_…and {len(others) - 30} more. Refresh with `python query.py jobs fetch {folder_slug}`._"
            )
            lines.append("")

    jobs_md.write_text("\n".join(lines), encoding="utf-8")
    return jobs_md


# ---------------------------------------------------------------------------
# CLI commands (invoked via query.py's `jobs` subparser)
# ---------------------------------------------------------------------------

def cli_probe(args) -> int:
    slugs = [args.slug] if args.slug else list_tracked_companies()
    for slug in slugs:
        fm = read_company_frontmatter(slug)
        if fm.get("ats") and not args.force:
            print(f"{slug:40s}  cached: {fm.get('ats', '?')} / {fm.get('ats_slug', '?')}")
            continue
        company_name = fm.get("name", slug.replace("-", " ").title())
        result = probe(slug, company_name)
        if result:
            ats_name, ats_slug = result
            set_company_ats(slug, ats_name, ats_slug)
            print(f"{slug:40s}  DETECTED  {ats_name} / {ats_slug}")
        else:
            print(f"{slug:40s}  no free ATS match")
        time.sleep(POLITE_DELAY_SECONDS)
    return 0


def cli_fetch(args) -> int:
    postings = fetch_jobs(args.slug, force_probe=args.force)
    if args.json:
        print(json.dumps([p.to_json() for p in postings], indent=2))
        return 0
    if not postings:
        print(f"{args.slug}: (no open roles via free ATS — may not be discoverable)")
        return 0
    matches = [p for p in postings if matches_profile(p)]
    print(f"{args.slug}: {len(postings)} open total, {len(matches)} match profile")
    for p in matches[: args.max]:
        print(f"  [{p.posted_at or '----------'}] {p.title} — {p.location or '—'}")
        print(f"    {p.url}")
    return 0


def cli_all(args) -> int:
    slugs = list_tracked_companies()
    total_open = 0
    total_match = 0
    covered = 0
    for slug in slugs:
        postings = fetch_jobs(slug)
        if postings:
            matches = [p for p in postings if matches_profile(p)]
            write_jobs_md(slug, postings)
            total_open += len(postings)
            total_match += len(matches)
            covered += 1
            print(f"{slug:40s}  {len(postings):3d} open  {len(matches):3d} match")
        else:
            print(f"{slug:40s}  —")
        time.sleep(POLITE_DELAY_SECONDS)
    print()
    print(
        f"Total: {total_open} open postings, {total_match} profile matches across "
        f"{covered}/{len(slugs)} companies with discoverable ATS."
    )
    return 0


def cli_match(args) -> int:
    out: list[JobPosting] = []
    for slug in list_tracked_companies():
        for p in fetch_jobs(slug):
            if matches_profile(p):
                out.append(p)
    if args.json:
        print(json.dumps([p.to_json() for p in out], indent=2))
        return 0
    for p in sorted(out, key=lambda x: (x.company, x.posted_at), reverse=True):
        print(f"{p.company:25s}  [{p.posted_at or '----------'}]  {p.title}")
        print(f"  → {p.url}")
    print(f"\nTotal profile matches across all companies: {len(out)}")
    return 0


def build_subparsers(sub: argparse._SubParsersAction) -> None:
    p_probe = sub.add_parser("probe", help="detect which ATS each tracked company uses")
    p_probe.add_argument("slug", nargs="?", default=None, help="single slug; omit for all")
    p_probe.add_argument("--force", action="store_true", help="re-probe even if cached")
    p_probe.set_defaults(func=cli_probe)

    p_fetch = sub.add_parser("fetch", help="fetch open jobs for one company")
    p_fetch.add_argument("slug")
    p_fetch.add_argument("--force", action="store_true", help="re-probe instead of using cached ats")
    p_fetch.add_argument("--json", action="store_true")
    p_fetch.add_argument("--max", type=int, default=10)
    p_fetch.set_defaults(func=cli_fetch)

    p_all = sub.add_parser("all", help="fetch + write jobs.md for every tracked company")
    p_all.set_defaults(func=cli_all)

    p_match = sub.add_parser("match", help="list all profile matches across every company")
    p_match.add_argument("--json", action="store_true")
    p_match.set_defaults(func=cli_match)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    ap = argparse.ArgumentParser(prog="jobs.py")
    sub = ap.add_subparsers(dest="op", required=True)
    build_subparsers(sub)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
