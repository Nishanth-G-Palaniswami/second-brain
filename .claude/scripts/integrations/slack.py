"""Slack integration — read-only attention list (DMs + @mentions).

Phase 4b is pure polling via `slack_sdk.WebClient`; Socket Mode is reserved for
Phase 7 (chat interface). Only `SLACK_BOT_TOKEN` + `SLACK_USER_ID` are required.

Design invariants:
  - The bot must be a member of any public/private channel you want it to watch
    (invite with `/invite @second-brain` in Slack). DMs are always visible to
    the bot if the user has messaged it.
  - State tracked in `.claude/data/state/slack-state.json` so successive calls
    only surface new items. Heartbeat (Phase 6) reuses the same state file.
  - User-id → display-name cache at `.claude/data/state/slack-users.json` to
    spare the `users:read` tier limit on repeated runs.
"""
from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_USER_ID_RE = re.compile(r"^[UW][A-Z0-9]{6,}$")
_CHANNEL_ID_RE = re.compile(r"^[CG][A-Z0-9]{6,}$")

from . import _env

PROJECT_ROOT = Path(__file__).resolve().parents[3]
STATE_DIR = PROJECT_ROOT / ".claude" / "data" / "state"
STATE_FILE = STATE_DIR / "slack-state.json"
USER_CACHE_FILE = STATE_DIR / "slack-users.json"

DEFAULT_LOOKBACK_SECONDS = 24 * 3600  # first run: 24h back
CHANNEL_HISTORY_LIMIT = 100
MAX_TEXT_LEN = 800


@dataclass
class SlackMessage:
    channel: str
    channel_name: str
    ts: str
    user: str
    user_name: str
    text: str
    permalink: str
    is_dm: bool
    is_mention: bool
    thread_ts: str = ""
    is_thread_reply: bool = False

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Auth + state
# ---------------------------------------------------------------------------

def _get_client():
    from slack_sdk import WebClient

    token = _env.require("SLACK_BOT_TOKEN", "create a Slack app, install to workspace, copy the xoxb- token")
    return WebClient(token=token)


def _get_user_client():
    """Slack client acting AS the user (xoxp- token).

    Required for reading DMs between the user and other humans — bot tokens can
    only see DMs the bot itself is a party to. Scopes needed on the user token:
    im:read, im:history, mpim:read, mpim:history, users:read. Read-only; this
    client never writes to Slack.
    """
    from slack_sdk import WebClient

    token = _env.require(
        "SLACK_USER_TOKEN",
        "add a Slack user token (xoxp-) with user-scopes im:read, im:history, "
        "mpim:read, mpim:history, users:read. Reinstall the app after adding scopes.",
    )
    return WebClient(token=token)


def _self_id() -> str:
    return _env.require("SLACK_USER_ID", "your Slack user id, e.g. U0XXXXXXX")


def _read_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(state: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _load_user_cache() -> dict[str, str]:
    if not USER_CACHE_FILE.exists():
        return {}
    try:
        return json.loads(USER_CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_user_cache(cache: dict[str, str]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    USER_CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _lookup_user(client, user_id: str, cache: dict[str, str]) -> str:
    from slack_sdk.errors import SlackApiError

    if not user_id:
        return ""
    if user_id in cache:
        return cache[user_id]
    try:
        resp = client.users_info(user=user_id)
        u = resp["user"]
        name = u.get("real_name") or u.get("profile", {}).get("display_name") or u.get("name") or user_id
    except SlackApiError:
        name = user_id
    cache[user_id] = name
    return name


def _iter_conversations(client, channel_limit: int) -> list[dict]:
    """Return channels the bot can read: IMs (always) + public/private where is_member."""
    from slack_sdk.errors import SlackApiError

    channels: list[dict] = []
    cursor: str | None = None
    while True:
        try:
            kwargs = {
                "types": "public_channel,private_channel,im,mpim",
                "exclude_archived": True,
                "limit": 200,
            }
            if cursor:
                kwargs["cursor"] = cursor
            resp = client.conversations_list(**kwargs)
        except SlackApiError as exc:
            sys.stderr.write(f"[slack] conversations.list failed: {exc}\n")
            break
        for c in resp.get("channels", []) or []:
            if c.get("is_im") or c.get("is_mpim") or c.get("is_member"):
                channels.append(c)
        cursor = (resp.get("response_metadata") or {}).get("next_cursor") or None
        if not cursor or len(channels) >= channel_limit:
            break
    return channels[:channel_limit]


def _permalink(client, channel_id: str, ts: str) -> str:
    from slack_sdk.errors import SlackApiError

    try:
        return client.chat_getPermalink(channel=channel_id, message_ts=ts).get("permalink", "") or ""
    except SlackApiError:
        return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_attention(
    since_ts: str | None = None,
    *,
    channel_limit: int = 200,
    update_state: bool = True,
) -> list[SlackMessage]:
    """Return DMs + @mentions newer than `since_ts`.

    - `since_ts` defaults to `state.last_run_ts`, or (now - 24h) on first run.
    - `update_state=False` makes the call ephemeral (heartbeat dry-runs, debugging).
    """
    client = _get_client()
    self_id = _self_id()
    mention_token = f"<@{self_id}>"

    state = _read_state()
    if since_ts is None:
        since_ts = state.get("last_run_ts") or f"{time.time() - DEFAULT_LOOKBACK_SECONDS:.6f}"

    user_cache = _load_user_cache()
    channels = _iter_conversations(client, channel_limit)

    out: list[SlackMessage] = []
    max_ts = since_ts

    for c in channels:
        cid = c["id"]
        is_dm = bool(c.get("is_im") or c.get("is_mpim"))
        try:
            resp = client.conversations_history(
                channel=cid,
                oldest=since_ts,
                limit=CHANNEL_HISTORY_LIMIT,
            )
        except Exception as exc:  # noqa: BLE001 — log + skip on per-channel failure
            sys.stderr.write(f"[slack] history {cid} failed: {exc}\n")
            continue

        for m in resp.get("messages", []) or []:
            ts = m.get("ts", "")
            text = m.get("text", "") or ""
            uid = m.get("user") or m.get("bot_id") or ""
            if uid == self_id:
                continue
            if m.get("subtype") in {"channel_join", "channel_leave"}:
                continue
            is_mention = mention_token in text
            if not (is_dm or is_mention):
                continue
            out.append(
                SlackMessage(
                    channel=cid,
                    channel_name=c.get("name") or ("(dm)" if is_dm else cid),
                    ts=ts,
                    user=uid,
                    user_name=_lookup_user(client, uid, user_cache),
                    text=text[:MAX_TEXT_LEN],
                    permalink=_permalink(client, cid, ts),
                    is_dm=is_dm,
                    is_mention=is_mention,
                )
            )
            if ts > max_ts:
                max_ts = ts

    _save_user_cache(user_cache)
    if update_state:
        state["last_run_ts"] = max_ts
        state["last_run_at"] = time.time()
        _write_state(state)
    out.sort(key=lambda s: s.ts, reverse=True)
    return out


def auth_test() -> dict[str, Any]:
    """Verify credentials + surface workspace/user info. Good first smoke test."""
    client = _get_client()
    resp = client.auth_test()
    return {k: resp.get(k) for k in ("ok", "team", "user", "team_id", "user_id", "url")}


def auth_test_user() -> dict[str, Any]:
    """Same as auth_test but uses SLACK_USER_TOKEN. `user_id` in the response
    should match your own SLACK_USER_ID — if it doesn't, the token belongs to
    the wrong identity."""
    client = _get_user_client()
    resp = client.auth_test()
    return {k: resp.get(k) for k in ("ok", "team", "user", "team_id", "user_id", "url")}


# ---------------------------------------------------------------------------
# Per-person DM history — used by the slack-dm-digest skill
# ---------------------------------------------------------------------------

def _find_im(client, user_ref: str, user_cache: dict[str, str]) -> tuple[str, str, str]:
    """Find the already-open IM channel with the referenced user.

    Returns `(user_id, channel_id, display_name)`. Accepts an explicit user ID
    (U0XXXXXXX / W0XXXXXXX), a @handle, or a display name matched against IMs
    already in the workspace. We intentionally do NOT call `conversations.open`
    — that requires `im:write`, and the Slack integration is read-only. If the
    user hasn't DM'd the target before, there's nothing to digest; the caller
    gets a clear error telling them to start a DM in Slack first.
    """
    from slack_sdk.errors import SlackApiError

    ref = user_ref.strip().lstrip("@")
    if not ref:
        raise RuntimeError("empty user reference")
    is_id = bool(_USER_ID_RE.match(ref))
    ref_low = ref.lower()

    candidates: list[tuple[str, str, str]] = []  # (user_id, channel_id, display_name)
    cursor: str | None = None
    while True:
        try:
            kwargs: dict[str, Any] = {"types": "im", "exclude_archived": True, "limit": 200}
            if cursor:
                kwargs["cursor"] = cursor
            resp = client.conversations_list(**kwargs)
        except SlackApiError as exc:
            raise RuntimeError(f"conversations.list failed: {exc}") from exc
        for c in resp.get("channels", []) or []:
            uid = c.get("user")
            cid = c.get("id")
            if not uid or not cid:
                continue
            if is_id:
                if uid == ref:
                    name = _lookup_user(client, uid, user_cache)
                    return uid, cid, name
                continue
            name = _lookup_user(client, uid, user_cache)
            nl = name.lower()
            if nl == ref_low or ref_low in nl:
                candidates.append((uid, cid, name))
        cursor = (resp.get("response_metadata") or {}).get("next_cursor") or None
        if not cursor:
            break

    if is_id:
        raise RuntimeError(
            f"user ID {ref} has no open IM with this bot. Send them a DM in Slack first, "
            "then retry."
        )
    if not candidates:
        raise RuntimeError(
            f"could not resolve Slack user '{user_ref}' — no open IM with a matching "
            "display name. Pass an explicit user ID (U0XXXXXXX) or start a DM first."
        )
    exacts = [c for c in candidates if c[2].lower() == ref_low]
    if len(exacts) == 1:
        return exacts[0]
    if len(exacts) > 1:
        listing = ", ".join(f"{uid} ({name})" for uid, _cid, name in exacts)
        raise RuntimeError(f"ambiguous exact match for '{user_ref}': {listing}")
    if len(candidates) == 1:
        return candidates[0]
    listing = ", ".join(f"{uid} ({name})" for uid, _cid, name in candidates)
    raise RuntimeError(f"ambiguous match for '{user_ref}': {listing}")


def list_dm(
    user_ref: str,
    *,
    limit: int = 200,
    oldest: str | None = None,
    include_threads: bool = True,
) -> list[SlackMessage]:
    """Return the DM history with a specific person, newest-first.

    Does NOT mutate `slack-state.json` — full-history reads must not advance
    the attention cursor used by `list_attention`. `limit` caps top-level
    pagination; if `include_threads` is True, all replies under any fetched
    parent are additionally appended (may exceed `limit`).
    """
    from slack_sdk.errors import SlackApiError

    client = _get_user_client()
    user_cache = _load_user_cache()

    user_id, cid, other_name = _find_im(client, user_ref, user_cache)
    channel_name = f"dm:{other_name}" if other_name else f"dm:{user_id}"

    def _mk(m: dict, *, is_thread_reply: bool) -> SlackMessage:
        # Unlike list_attention (which truncates to MAX_TEXT_LEN for brief
        # previews), list_dm preserves the full message body. Digests must
        # retain URLs, commit hashes, file paths, and code blocks verbatim.
        ts = m.get("ts", "")
        uid = m.get("user") or m.get("bot_id") or ""
        return SlackMessage(
            channel=cid,
            channel_name=channel_name,
            ts=ts,
            user=uid,
            user_name=_lookup_user(client, uid, user_cache) if uid else "",
            text=m.get("text") or "",
            permalink=_permalink(client, cid, ts),
            is_dm=True,
            is_mention=False,
            thread_ts=m.get("thread_ts", "") or "",
            is_thread_reply=is_thread_reply,
        )

    collected: list[SlackMessage] = []
    parent_thread_ts: list[str] = []
    cursor: str | None = None
    remaining = max(limit, 1)

    while remaining > 0:
        try:
            kwargs: dict[str, Any] = {"channel": cid, "limit": min(remaining, 200)}
            if oldest:
                kwargs["oldest"] = oldest
            if cursor:
                kwargs["cursor"] = cursor
            resp = client.conversations_history(**kwargs)
        except SlackApiError as exc:
            raise RuntimeError(f"conversations.history failed: {exc}") from exc
        msgs = resp.get("messages", []) or []
        if not msgs:
            break
        for m in msgs:
            if m.get("subtype") in {"channel_join", "channel_leave"}:
                continue
            collected.append(_mk(m, is_thread_reply=False))
            if include_threads and m.get("reply_count", 0) and m.get("thread_ts"):
                parent_thread_ts.append(m["thread_ts"])
            remaining -= 1
            if remaining <= 0:
                break
        cursor = (resp.get("response_metadata") or {}).get("next_cursor") or None
        if not cursor or not resp.get("has_more"):
            break

    if include_threads:
        for tts in parent_thread_ts:
            try:
                rresp = client.conversations_replies(channel=cid, ts=tts, limit=200)
            except SlackApiError as exc:
                sys.stderr.write(f"[slack] conversations.replies {tts} failed: {exc}\n")
                continue
            for m in rresp.get("messages", []) or []:
                if m.get("ts") == tts:
                    continue  # parent already collected
                if m.get("subtype") in {"channel_join", "channel_leave"}:
                    continue
                collected.append(_mk(m, is_thread_reply=True))

    _save_user_cache(user_cache)
    collected.sort(key=lambda s: s.ts, reverse=True)
    return collected


# ---------------------------------------------------------------------------
# CLI entry points
# ---------------------------------------------------------------------------

def cli_attention(args) -> int:
    msgs = list_attention(
        since_ts=args.since,
        channel_limit=args.channel_limit,
        update_state=not args.ephemeral,
    )
    if args.json:
        print(json.dumps([m.to_json() for m in msgs], indent=2))
        return 0
    if not msgs:
        print("(no new DMs or mentions)")
        return 0
    for m in msgs:
        tag = "DM " if m.is_dm else "@  " if m.is_mention else "-  "
        chan = "#" + m.channel_name if not m.is_dm else m.channel_name
        print(f"{tag} {m.ts}  {chan[:20]:<20} {m.user_name[:24]:<24}  {m.text[:80]}")
        if m.permalink:
            print(f"     {m.permalink}")
    return 0


def cli_test(args) -> int:
    info = auth_test()
    print(json.dumps(info, indent=2))
    return 0 if info.get("ok") else 1


def cli_user_test(args) -> int:
    info = auth_test_user()
    print(json.dumps(info, indent=2))
    return 0 if info.get("ok") else 1


def cli_dm(args) -> int:
    try:
        msgs = list_dm(
            user_ref=args.user,
            limit=args.limit,
            oldest=args.since,
            include_threads=not args.no_threads,
        )
    except RuntimeError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2

    if args.json:
        print(json.dumps([m.to_json() for m in msgs], indent=2))
        return 0
    if not msgs:
        print("(no messages in that DM)")
        return 0
    for m in msgs:
        tag = "  ->" if m.is_thread_reply else "DM "
        print(f"{tag} {m.ts}  {m.user_name[:24]:<24}  {m.text[:80]}")
        if m.permalink:
            print(f"     {m.permalink}")
    return 0


# ---------------------------------------------------------------------------
# Channel history — used by the slack-channel-digest skill
# ---------------------------------------------------------------------------

def _resolve_channel(client, channel_ref: str) -> tuple[str, str, bool]:
    """Resolve a channel reference to (channel_id, name, is_private).

    Accepts `#foo`, `foo`, or `C0XXXXXXX` / `G0XXXXXXX`. Uses the user token
    (via caller), which requires user-token scopes `channels:read` + `groups:read`.
    Raises RuntimeError with candidate listing on ambiguity.
    """
    from slack_sdk.errors import SlackApiError

    ref = channel_ref.strip().lstrip("#")
    if not ref:
        raise RuntimeError("empty channel reference")
    if _CHANNEL_ID_RE.match(ref):
        try:
            info = (client.conversations_info(channel=ref) or {}).get("channel", {}) or {}
            return ref, info.get("name") or ref, bool(info.get("is_private"))
        except SlackApiError as exc:
            raise RuntimeError(f"conversations.info failed for {ref}: {exc}") from exc

    ref_low = ref.lower()
    candidates: list[tuple[str, str, bool]] = []  # (id, name, is_private)
    cursor: str | None = None
    while True:
        try:
            kwargs: dict[str, Any] = {
                "types": "public_channel,private_channel,mpim",
                "exclude_archived": True,
                "limit": 200,
            }
            if cursor:
                kwargs["cursor"] = cursor
            resp = client.conversations_list(**kwargs)
        except SlackApiError as exc:
            raise RuntimeError(f"conversations.list failed: {exc}") from exc
        for c in resp.get("channels", []) or []:
            cid = c.get("id")
            cname = c.get("name") or ""
            if not cid:
                continue
            cl = cname.lower()
            if cl == ref_low or ref_low in cl:
                candidates.append((cid, cname, bool(c.get("is_private"))))
        cursor = (resp.get("response_metadata") or {}).get("next_cursor") or None
        if not cursor:
            break

    if not candidates:
        raise RuntimeError(
            f"could not resolve Slack channel '{channel_ref}' — no channel with a matching "
            "name. Check spelling, or verify the user token has channels:read + groups:read."
        )
    exacts = [c for c in candidates if c[1].lower() == ref_low]
    if len(exacts) == 1:
        return exacts[0]
    if len(exacts) > 1:
        listing = ", ".join(f"{cid} ({name})" for cid, name, _priv in exacts)
        raise RuntimeError(f"ambiguous exact match for '{channel_ref}': {listing}")
    if len(candidates) == 1:
        return candidates[0]
    listing = ", ".join(f"{cid} ({name})" for cid, name, _priv in candidates)
    raise RuntimeError(f"ambiguous match for '{channel_ref}': {listing}")


def list_channel(
    channel_ref: str,
    *,
    limit: int = 500,
    oldest: str | None = None,
    include_threads: bool = True,
) -> list[SlackMessage]:
    """Return channel history, newest-first.

    Does NOT mutate `slack-state.json`. Preserves full message text (no
    MAX_TEXT_LEN truncation) — channels carry code blocks and long-form
    decisions that matter at full fidelity. `limit` caps top-level pagination;
    threaded replies are additive when `include_threads=True`.
    """
    from slack_sdk.errors import SlackApiError

    client = _get_user_client()
    user_cache = _load_user_cache()

    cid, channel_name, _is_private = _resolve_channel(client, channel_ref)

    def _mk(m: dict, *, is_thread_reply: bool) -> SlackMessage:
        ts = m.get("ts", "")
        uid = m.get("user") or m.get("bot_id") or ""
        return SlackMessage(
            channel=cid,
            channel_name=channel_name,
            ts=ts,
            user=uid,
            user_name=_lookup_user(client, uid, user_cache) if uid else "",
            text=m.get("text") or "",
            permalink=_permalink(client, cid, ts),
            is_dm=False,
            is_mention=False,
            thread_ts=m.get("thread_ts", "") or "",
            is_thread_reply=is_thread_reply,
        )

    collected: list[SlackMessage] = []
    parent_thread_ts: list[str] = []
    cursor: str | None = None
    remaining = max(limit, 1)

    while remaining > 0:
        try:
            kwargs: dict[str, Any] = {"channel": cid, "limit": min(remaining, 200)}
            if oldest:
                kwargs["oldest"] = oldest
            if cursor:
                kwargs["cursor"] = cursor
            resp = client.conversations_history(**kwargs)
        except SlackApiError as exc:
            raise RuntimeError(f"conversations.history failed: {exc}") from exc
        msgs = resp.get("messages", []) or []
        if not msgs:
            break
        for m in msgs:
            if m.get("subtype") in {"channel_join", "channel_leave"}:
                continue
            collected.append(_mk(m, is_thread_reply=False))
            if include_threads and m.get("reply_count", 0) and m.get("thread_ts"):
                parent_thread_ts.append(m["thread_ts"])
            remaining -= 1
            if remaining <= 0:
                break
        cursor = (resp.get("response_metadata") or {}).get("next_cursor") or None
        if not cursor or not resp.get("has_more"):
            break

    if include_threads:
        for tts in parent_thread_ts:
            try:
                rresp = client.conversations_replies(channel=cid, ts=tts, limit=200)
            except SlackApiError as exc:
                sys.stderr.write(f"[slack] conversations.replies {tts} failed: {exc}\n")
                continue
            for m in rresp.get("messages", []) or []:
                if m.get("ts") == tts:
                    continue  # parent already collected
                if m.get("subtype") in {"channel_join", "channel_leave"}:
                    continue
                collected.append(_mk(m, is_thread_reply=True))

    _save_user_cache(user_cache)
    collected.sort(key=lambda s: s.ts, reverse=True)
    return collected


def cli_channel(args) -> int:
    try:
        msgs = list_channel(
            channel_ref=args.channel,
            limit=args.limit,
            oldest=args.since,
            include_threads=not args.no_threads,
        )
    except RuntimeError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2

    if args.json:
        print(json.dumps([m.to_json() for m in msgs], indent=2))
        return 0
    if not msgs:
        print("(no messages in that channel)")
        return 0
    for m in msgs:
        tag = "  ->" if m.is_thread_reply else "#  "
        print(f"{tag} {m.ts}  {m.user_name[:20]:<20}  {m.text[:80]}")
        if m.permalink:
            print(f"     {m.permalink}")
    return 0
