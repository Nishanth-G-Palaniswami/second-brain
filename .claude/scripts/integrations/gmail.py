"""Gmail integration — OAuth2 desktop-app flow, read + draft only.

Design invariants:
  - Scope is `gmail.modify` (read + create drafts). We never request `gmail.send`,
    so "never send without approval" is enforced at the Google API layer itself.
  - Secrets stay inside this module. The LLM invokes `query.py gmail ...` as a
    subprocess and sees clean dataclasses — it never holds the credentials or the
    API service object.
  - Returns dataclasses, not raw API payloads, so calling code is stable against
    Gmail API response shape changes.

One-time setup (see .claude/data/creds/README.md):
  Drop a Google OAuth "Desktop app" client secrets file at
    .claude/data/creds/gmail-credentials.json
  The first call triggers a browser OAuth flow and writes
    .claude/data/creds/gmail-token.json
  with the refresh token. Back that file up; losing it means re-consenting.
"""
from __future__ import annotations

import base64
import email
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Iterable

# Deferred imports — Google SDKs are only needed when this module is actually used,
# not when the registry is introspected.

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CREDS_DIR = PROJECT_ROOT / ".claude" / "data" / "creds"
CLIENT_SECRETS = CREDS_DIR / "gmail-credentials.json"
TOKEN_FILE = CREDS_DIR / "gmail-token.json"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    # Phase 6 — Calendar piggybacks on this OAuth flow. Adding this scope after
    # an existing token was issued with only `gmail.modify` will require a
    # one-time re-consent: delete `gmail-token.json` and re-run any command.
    "https://www.googleapis.com/auth/calendar.readonly",
]

# Default triage query — unread in the last 2 days, excluding all Gmail category
# tabs (promotions, social, updates, forums) plus any thread delivered via a
# mailing-list header. Heartbeat and the `triage-inbox` skill both inherit this
# by calling `list_triage()` with no arg; override via `list_triage(query=...)`
# when a caller needs a different filter.
DEFAULT_TRIAGE_Q = (
    "is:unread newer_than:2d "
    "-category:promotions -category:social -category:updates -category:forums "
    "-list:(*)"
)
DEFAULT_TRIAGE_MAX = 25

MAX_SNIPPET_LEN = 240


@dataclass
class GmailMessage:
    id: str
    thread_id: str
    sender: str            # "Jane Doe <jane@example.com>"
    to: list[str]
    subject: str
    snippet: str
    date: str              # ISO-8601 UTC
    labels: list[str] = field(default_factory=list)
    unread: bool = True

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GmailThread:
    id: str
    subject: str
    messages: list[GmailMessage]
    participants: list[str]
    latest_message_id: str  # RFC 2822 Message-ID header of the most recent message
    latest_from: str
    latest_to: list[str]

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject": self.subject,
            "participants": self.participants,
            "latest_message_id": self.latest_message_id,
            "latest_from": self.latest_from,
            "latest_to": self.latest_to,
            "messages": [m.to_json() for m in self.messages],
        }


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _get_service():
    """Return an authenticated gmail v1 service. Triggers browser OAuth if needed."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    CREDS_DIR.mkdir(parents=True, exist_ok=True)

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CLIENT_SECRETS.exists():
                raise FileNotFoundError(
                    f"Missing {CLIENT_SECRETS}. See .claude/data/creds/README.md for "
                    "one-time Google Cloud setup."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")

    return build("gmail", "v1", credentials=creds, cache_discovery=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _header(headers: list[dict], name: str) -> str:
    n = name.lower()
    for h in headers:
        if h.get("name", "").lower() == n:
            return h.get("value", "")
    return ""


def _parse_addr_list(value: str) -> list[str]:
    if not value:
        return []
    return [a.strip() for a in value.split(",") if a.strip()]


def _iso_utc(internal_date_ms: str | int) -> str:
    try:
        ms = int(internal_date_ms)
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _message_from_payload(msg: dict) -> GmailMessage:
    payload = msg.get("payload", {})
    headers = payload.get("headers", [])
    labels = msg.get("labelIds", []) or []
    snippet = msg.get("snippet", "") or ""
    if len(snippet) > MAX_SNIPPET_LEN:
        snippet = snippet[: MAX_SNIPPET_LEN - 1].rstrip() + "…"
    return GmailMessage(
        id=msg["id"],
        thread_id=msg.get("threadId", ""),
        sender=_header(headers, "From"),
        to=_parse_addr_list(_header(headers, "To")),
        subject=_header(headers, "Subject"),
        snippet=snippet,
        date=_iso_utc(msg.get("internalDate", 0)),
        labels=labels,
        unread="UNREAD" in labels,
    )


def _extract_plain_body(payload: dict) -> str:
    """Best-effort plain-text extraction from a Gmail message payload."""
    if not payload:
        return ""
    mime_type = payload.get("mimeType", "")
    body = payload.get("body", {})
    data = body.get("data")
    if mime_type == "text/plain" and data:
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
    for part in payload.get("parts", []) or []:
        text = _extract_plain_body(part)
        if text:
            return text
    # Fallback to text/html stripped of tags.
    if mime_type == "text/html" and data:
        html = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        return re.sub(r"<[^>]+>", "", html)
    return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_triage(query: str = DEFAULT_TRIAGE_Q, max_results: int = DEFAULT_TRIAGE_MAX) -> list[GmailMessage]:
    """Return the triage queue (unread, recent, non-promo) as structured messages.

    Uses batched metadata fetches to stay inside Gmail's 250 quota units/sec
    (each metadata get = 5 units, so 50/sec sustained is safe).
    """
    service = _get_service()
    resp = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    ids = [m["id"] for m in resp.get("messages", [])]
    if not ids:
        return []

    out: list[GmailMessage] = []

    def _cb(request_id, response, exception):
        if exception is not None:
            return
        out.append(_message_from_payload(response))

    batch = service.new_batch_http_request(callback=_cb)
    for mid in ids:
        batch.add(
            service.users().messages().get(
                userId="me",
                id=mid,
                format="metadata",
                metadataHeaders=["From", "To", "Subject", "Date"],
            )
        )
    batch.execute()
    # preserve Gmail's returned order (most recent first)
    order = {mid: i for i, mid in enumerate(ids)}
    out.sort(key=lambda m: order.get(m.id, 1 << 30))
    # Dedupe by thread_id — `messages.list` returns per-message, so a thread with
    # multiple unread messages shows up multiple times. Keep the first (newest)
    # occurrence so triage is one-row-per-thread.
    seen_threads: set[str] = set()
    deduped: list[GmailMessage] = []
    for msg in out:
        tid = msg.thread_id or msg.id
        if tid in seen_threads:
            continue
        seen_threads.add(tid)
        deduped.append(msg)
    return deduped


def get_thread(thread_id: str) -> GmailThread:
    """Fetch a full thread with message bodies. Costs ~5 units * len(thread)."""
    service = _get_service()
    thread = service.users().threads().get(userId="me", id=thread_id, format="full").execute()
    raw_msgs = thread.get("messages", [])
    messages = [_message_from_payload(m) for m in raw_msgs]

    # pull body + Message-ID off the latest message for reply-building
    latest_raw = raw_msgs[-1] if raw_msgs else {}
    latest_headers = latest_raw.get("payload", {}).get("headers", [])
    latest_message_id = _header(latest_headers, "Message-ID")
    latest_from = _header(latest_headers, "From")
    latest_to = _parse_addr_list(_header(latest_headers, "To"))
    subject = messages[-1].subject if messages else ""

    participants: list[str] = []
    seen: set[str] = set()
    for m in messages:
        for addr in [m.sender, *m.to]:
            key = addr.lower()
            if addr and key not in seen:
                seen.add(key)
                participants.append(addr)

    # attach bodies back onto the dataclasses via snippet extension (keeps the
    # public dataclass simple; callers needing full body can fetch again).
    for msg, raw in zip(messages, raw_msgs, strict=False):
        body = _extract_plain_body(raw.get("payload", {})).strip()
        if body and len(body) > len(msg.snippet):
            msg.snippet = body[:4000]  # cap to keep context small

    return GmailThread(
        id=thread_id,
        subject=subject,
        messages=messages,
        participants=participants,
        latest_message_id=latest_message_id,
        latest_from=latest_from,
        latest_to=latest_to,
    )


def create_draft(thread_id: str, body: str, *, subject: str | None = None,
                 recipient: str | None = None) -> dict[str, str]:
    """Create a Gmail draft as a reply to `thread_id`. Lands in Gmail's Drafts UI.

    - `body` is plain text / light markdown. Gmail treats it as plain text.
    - `subject` and `recipient` override the inferred reply headers when given.
    - Returns {"draft_id": ..., "message_id": ..., "thread_id": ...}.
    """
    service = _get_service()
    thread = get_thread(thread_id)

    reply_subject = subject or (
        thread.subject if thread.subject.lower().startswith("re:") else f"Re: {thread.subject}"
    )
    to = recipient or thread.latest_from
    if not to:
        raise ValueError(f"Cannot infer recipient for thread {thread_id}")

    mime = MIMEText(body, "plain", "utf-8")
    mime["To"] = to
    mime["Subject"] = reply_subject
    if thread.latest_message_id:
        mime["In-Reply-To"] = thread.latest_message_id
        mime["References"] = thread.latest_message_id

    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")
    result = service.users().drafts().create(
        userId="me",
        body={"message": {"raw": raw, "threadId": thread_id}},
    ).execute()
    return {
        "draft_id": result.get("id", ""),
        "message_id": (result.get("message") or {}).get("id", ""),
        "thread_id": thread_id,
    }


# ---------------------------------------------------------------------------
# CLI entry points (called by query.py; no argparse here, keep it composable)
# ---------------------------------------------------------------------------

def cli_triage(args) -> int:
    msgs = list_triage(query=args.query, max_results=args.max)
    if args.json:
        print(json.dumps([m.to_json() for m in msgs], indent=2))
        return 0
    if not msgs:
        print("(inbox clear)")
        return 0
    for m in msgs:
        flags = ",".join(l for l in m.labels if l in {"IMPORTANT", "STARRED"}) or "-"
        print(f"{m.id}  {m.date[:16]}  [{flags}]  {m.sender[:40]:<40} {m.subject[:70]}")
        if m.snippet:
            print(f"           {m.snippet}")
    return 0


def cli_thread(args) -> int:
    thread = get_thread(args.id)
    if args.json:
        print(json.dumps(thread.to_json(), indent=2))
        return 0
    print(f"# {thread.subject}")
    print(f"thread_id: {thread.id}")
    print(f"participants: {', '.join(thread.participants)}")
    print(f"latest_from: {thread.latest_from}")
    print(f"latest_message_id: {thread.latest_message_id}")
    print()
    for i, m in enumerate(thread.messages, 1):
        print(f"--- message {i}/{len(thread.messages)} — {m.date[:16]} — {m.sender}")
        print(m.snippet)
        print()
    return 0


def cli_draft(args) -> int:
    body = args.body
    if args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")
    if not body:
        raise SystemExit("draft body is empty (pass --body or --body-file)")
    result = create_draft(
        args.thread_id,
        body,
        subject=args.subject,
        recipient=args.to,
    )
    print(json.dumps(result, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Draft lifecycle (Phase 6)
#
# Local markdown lives at:
#   drafts/active/YYYY-MM-DD_email_<slug>.md    — written by triage-inbox / heartbeat
#   drafts/sent/...                             — promoted after Gmail shows an outbound reply
#   drafts/expired/...                          — auto-moved after TTL with no outbound reply
#
# Frontmatter schema (all lowercase keys, values are raw strings):
#   type:        "email"
#   source_id:   gmail thread id
#   recipient:   "To:" address used when drafting
#   subject:     plain subject (without "Re:")
#   created:     ISO-8601 UTC
#   status:      "active" | "sent" | "expired"
#   sent_at:     ISO-8601 UTC (only when status=sent)
#
# Body has `## Original Message` then `## Draft Reply` (or `## Sent Reply` after promotion).
# ---------------------------------------------------------------------------

DEFAULT_DRAFTS_ROOT = Path(os.environ.get("SECOND_BRAIN_VAULT", str(Path.home() / "second-brain-vault"))) / "drafts"
DEFAULT_DRAFT_TTL_HOURS = 24

_FRONTMATTER_RE = re.compile(r"^---\s*\n(?P<fm>.*?)\n---\s*\n(?P<body>.*)$", re.DOTALL)
_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)

_me_email_cache: str | None = None


def _me_email() -> str:
    """Return the authenticated Gmail account's primary email address (cached)."""
    global _me_email_cache
    if _me_email_cache:
        return _me_email_cache
    service = _get_service()
    profile = service.users().getProfile(userId="me").execute()
    _me_email_cache = profile.get("emailAddress", "")
    return _me_email_cache


def _parse_local_draft(path: Path) -> dict[str, Any]:
    # Strip UTF-8 BOM if present — PowerShell's `Out-File -Encoding utf8` and
    # Notepad's "Save As UTF-8" both prepend one, and the frontmatter regex is
    # anchored to `^---`, so a BOM would silently fail the match.
    text = path.read_text(encoding="utf-8").lstrip("\ufeff")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError(f"no YAML frontmatter in {path}")
    fm_raw = m.group("fm")
    body = m.group("body")
    fm: dict[str, str] = {}
    for line in fm_raw.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()

    sections: dict[str, str] = {}
    matches = list(_HEADING_RE.finditer(body))
    for i, hm in enumerate(matches):
        key = hm.group(1).strip()
        start = hm.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections[key] = body[start:end].strip()
    return {"frontmatter": fm, "sections": sections}


def _serialize_local_draft(frontmatter: dict[str, str], sections: dict[str, str]) -> str:
    lines = ["---"]
    for k, v in frontmatter.items():
        lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append("")
    for heading, body in sections.items():
        lines.append(f"## {heading}")
        lines.append("")
        lines.append(body.rstrip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def send_draft_from_file(path: Path | str) -> dict[str, str]:
    """Read a `drafts/active/*.md` file and push its body to Gmail as a draft.

    Does NOT move the local file — the active→sent promotion happens lazily via
    `promote_drafts()` once Gmail shows an outbound reply. This keeps the "user
    must actually send from Gmail" step meaningful.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    parsed = _parse_local_draft(path)
    fm = parsed["frontmatter"]
    if fm.get("type") != "email":
        raise ValueError(f"{path}: frontmatter.type is {fm.get('type')!r}, expected 'email'")
    if fm.get("status") != "active":
        raise ValueError(f"{path}: frontmatter.status is {fm.get('status')!r}, expected 'active'")
    thread_id = fm.get("source_id") or ""
    if not thread_id:
        raise ValueError(f"{path}: missing source_id (gmail thread id)")
    body = parsed["sections"].get("Draft Reply") or ""
    if not body.strip():
        raise ValueError(f"{path}: empty 'Draft Reply' section")
    return create_draft(
        thread_id,
        body.strip(),
        subject=fm.get("subject") or None,
        recipient=fm.get("recipient") or None,
    )


def _latest_outbound_after(thread_id: str, after_iso: str) -> GmailMessage | None:
    """Find the most recent outbound message in `thread_id` sent after `after_iso`.

    "Outbound" = labelIds contains "SENT" (Gmail's server-side label for messages the
    authenticated user sent). That's the authoritative signal that a draft was sent.
    """
    me = _me_email().lower()
    try:
        thread = get_thread(thread_id)
    except Exception:
        return None
    best: GmailMessage | None = None
    for msg in thread.messages:
        if "SENT" not in (msg.labels or []):
            continue
        if msg.sender and me not in msg.sender.lower():
            continue
        if after_iso and msg.date and msg.date <= after_iso:
            continue
        if best is None or (msg.date and msg.date > (best.date or "")):
            best = msg
    return best


def promote_drafts(drafts_root: Path = DEFAULT_DRAFTS_ROOT) -> list[dict[str, str]]:
    """Detect active drafts whose Gmail thread now has a newer outbound message from us.

    Move each matched file to `drafts/sent/` and replace the `Draft Reply` section
    with the actual sent body — that's what the voice-matching RAG will search.
    """
    active_dir = drafts_root / "active"
    sent_dir = drafts_root / "sent"
    if not active_dir.is_dir():
        return []
    sent_dir.mkdir(parents=True, exist_ok=True)

    promoted: list[dict[str, str]] = []
    for path in sorted(active_dir.glob("*.md")):
        try:
            parsed = _parse_local_draft(path)
        except (OSError, ValueError) as exc:
            sys.stderr.write(f"[promote] skip {path.name}: {exc}\n")
            continue
        fm = parsed["frontmatter"]
        if fm.get("status") != "active":
            continue
        tid = fm.get("source_id") or ""
        created = fm.get("created") or ""
        if not tid:
            continue
        outbound = _latest_outbound_after(tid, created)
        if outbound is None:
            continue

        fm["status"] = "sent"
        fm["sent_at"] = outbound.date or datetime.now(timezone.utc).isoformat()
        sections = parsed["sections"]
        sections.pop("Draft Reply", None)
        sections["Sent Reply"] = outbound.snippet.strip() or "(body unavailable)"
        new_text = _serialize_local_draft(fm, sections)

        dest = sent_dir / path.name
        dest.write_text(new_text, encoding="utf-8")
        path.unlink()
        promoted.append({"path": str(dest), "thread_id": tid})
    return promoted


def expire_drafts(drafts_root: Path = DEFAULT_DRAFTS_ROOT,
                  ttl_hours: float = DEFAULT_DRAFT_TTL_HOURS) -> list[dict[str, str]]:
    """Move active drafts older than `ttl_hours` to `drafts/expired/`."""
    active_dir = drafts_root / "active"
    exp_dir = drafts_root / "expired"
    if not active_dir.is_dir():
        return []
    exp_dir.mkdir(parents=True, exist_ok=True)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=ttl_hours)
    expired: list[dict[str, str]] = []

    for path in sorted(active_dir.glob("*.md")):
        try:
            parsed = _parse_local_draft(path)
        except (OSError, ValueError) as exc:
            sys.stderr.write(f"[expire] skip {path.name}: {exc}\n")
            continue
        fm = parsed["frontmatter"]
        if fm.get("status") != "active":
            continue
        created_raw = fm.get("created") or ""
        try:
            created_dt = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            # Unparseable timestamp — fall back to the file's mtime
            created_dt = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if created_dt > cutoff:
            continue

        fm["status"] = "expired"
        fm["expired_at"] = datetime.now(timezone.utc).isoformat()
        new_text = _serialize_local_draft(fm, parsed["sections"])
        dest = exp_dir / path.name
        dest.write_text(new_text, encoding="utf-8")
        path.unlink()
        expired.append({"path": str(dest), "age_hours": f"{(datetime.now(timezone.utc) - created_dt).total_seconds() / 3600:.1f}"})
    return expired


# Missing imports used by the helpers above
from datetime import timedelta  # noqa: E402


# ---------------------------------------------------------------------------
# CLI entry points for the lifecycle helpers
# ---------------------------------------------------------------------------

def cli_send_draft(args) -> int:
    result = send_draft_from_file(args.path)
    print(json.dumps(result, indent=2))
    return 0


def cli_sweep(args) -> int:
    root = Path(args.drafts_root) if args.drafts_root else DEFAULT_DRAFTS_ROOT
    promoted = promote_drafts(root) if not args.expire_only else []
    expired = expire_drafts(root, ttl_hours=args.ttl_hours) if not args.promote_only else []
    out = {"promoted": promoted, "expired": expired}
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"promoted {len(promoted)} draft(s) active → sent")
        for p in promoted:
            print(f"  → {p['path']}")
        print(f"expired {len(expired)} draft(s) active → expired")
        for e in expired:
            print(f"  → {e['path']}  (age {e['age_hours']}h)")
    return 0
