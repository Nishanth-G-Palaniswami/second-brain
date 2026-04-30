"""Google Calendar integration — read-only next-events.

Shares Gmail's OAuth client + token file. When `calendar.readonly` is added to
`gmail.SCOPES`, existing tokens become incompatible; we force a one-time
re-consent in that case (see `_get_service`).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import gmail as _gmail  # reuse CREDS_DIR, TOKEN_FILE, CLIENT_SECRETS

DEFAULT_LOOKAHEAD_MINUTES = 12 * 60  # 12 hours forward
DEFAULT_MAX_RESULTS = 20


@dataclass
class CalendarEvent:
    id: str
    calendar_id: str
    summary: str
    start: str           # ISO-8601
    end: str             # ISO-8601
    location: str
    hangout_link: str
    attendees: list[str]
    status: str
    self_response: str   # accepted | tentative | declined | needsAction | unknown
    is_all_day: bool

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def _get_service():
    """Build a `calendar v3` service using Gmail's shared OAuth token.

    Phase 6 adds `calendar.readonly` to `gmail.SCOPES`; if the stored token was
    issued before that change, Google's library notices the scope mismatch on
    load, and the first call raises. Deleting `gmail-token.json` and re-running
    any command triggers a fresh browser flow covering both scopes.
    """
    from googleapiclient.discovery import build

    service = _gmail._get_service()  # triggers re-auth if scopes changed
    creds = service._http.credentials  # pull the creds we just validated
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _ensure_iso(ts: str | None) -> str:
    if not ts:
        return ""
    # Calendar API returns either "YYYY-MM-DDTHH:MM:SS..." (timed) or
    # {"date": "YYYY-MM-DD"} (all-day). We already normalized that at the caller.
    return ts


def next_events(
    lookahead_minutes: int = DEFAULT_LOOKAHEAD_MINUTES,
    max_results: int = DEFAULT_MAX_RESULTS,
    calendar_id: str = "primary",
) -> list[CalendarEvent]:
    """Events starting between now and `now + lookahead_minutes`, sorted ascending."""
    service = _get_service()
    now = datetime.now(timezone.utc)
    time_min = now.isoformat()
    time_max = (now + timedelta(minutes=lookahead_minutes)).isoformat()

    resp = service.events().list(
        calendarId=calendar_id,
        timeMin=time_min,
        timeMax=time_max,
        maxResults=max_results,
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    out: list[CalendarEvent] = []
    for e in resp.get("items", []) or []:
        start = e.get("start", {})
        end = e.get("end", {})
        is_all_day = "date" in start and "dateTime" not in start
        start_iso = start.get("dateTime") or start.get("date") or ""
        end_iso = end.get("dateTime") or end.get("date") or ""

        attendees_raw = e.get("attendees", []) or []
        attendees = [a.get("email", "") for a in attendees_raw if a.get("email")]
        self_response = "unknown"
        for a in attendees_raw:
            if a.get("self"):
                self_response = a.get("responseStatus", "unknown")
                break

        conf_data = e.get("conferenceData", {}) or {}
        hangout = e.get("hangoutLink") or ""
        if not hangout:
            for ep in conf_data.get("entryPoints", []) or []:
                if ep.get("entryPointType") == "video":
                    hangout = ep.get("uri", "")
                    break

        out.append(
            CalendarEvent(
                id=e.get("id", ""),
                calendar_id=calendar_id,
                summary=e.get("summary", "(no title)"),
                start=_ensure_iso(start_iso),
                end=_ensure_iso(end_iso),
                location=e.get("location", ""),
                hangout_link=hangout,
                attendees=attendees,
                status=e.get("status", ""),
                self_response=self_response,
                is_all_day=is_all_day,
            )
        )
    return out


# ---------------------------------------------------------------------------
# CLI entry points
# ---------------------------------------------------------------------------

def cli_next(args) -> int:
    events = next_events(
        lookahead_minutes=args.lookahead,
        max_results=args.max,
        calendar_id=args.calendar,
    )
    if args.json:
        print(json.dumps([e.to_json() for e in events], indent=2))
        return 0
    if not events:
        print("(no events in window)")
        return 0
    for e in events:
        when = e.start[:16].replace("T", " ")
        tag = "[all-day]" if e.is_all_day else ""
        resp = f"[{e.self_response}]" if e.self_response != "unknown" else ""
        print(f"{when}  {tag}{resp}  {e.summary}")
        if e.location or e.hangout_link:
            print(f"           {e.hangout_link or e.location}")
    return 0
