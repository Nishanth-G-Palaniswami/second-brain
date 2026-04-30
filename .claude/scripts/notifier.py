"""Thin wrapper over `win11toast.toast(...)` with a print-to-stderr fallback.

Used by `heartbeat.py` and `memory_reflect.py` to surface items needing attention.
If `win11toast` is unavailable (non-Windows, import error) or raises at runtime
(headless session, no WinRT runtime), we degrade to stderr so callers don't
need to branch.
"""
from __future__ import annotations

import sys
from typing import Any

try:
    from win11toast import toast as _wintoast  # type: ignore
except Exception:  # pragma: no cover — platform-dependent
    _wintoast = None


def notify(title: str, body: str = "", *, on_click: str | None = None,
           duration: str = "short", **kwargs: Any) -> bool:
    """Send a Windows toast. Return True on success, False if it fell back to stderr.

    Keep `body` under ~4 lines — Windows truncates long toasts. Caller may pass
    `on_click` as a URL or shell command that the toast's tap-action opens.
    """
    if _wintoast is None:
        sys.stderr.write(f"[notify] {title}: {body}\n")
        return False
    try:
        _wintoast(
            title,
            body,
            duration=duration,
            on_click=on_click,
            **kwargs,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"[notify] win11toast failed ({exc!r}); falling back to stderr\n")
        sys.stderr.write(f"[notify] {title}: {body}\n")
        return False


if __name__ == "__main__":
    # Quick smoke test: `python .claude/scripts/notifier.py "hello" "this is a test"`
    args = sys.argv[1:]
    t = args[0] if args else "SecondBrain smoke test"
    b = args[1] if len(args) > 1 else "notifier.py is wired."
    ok = notify(t, b)
    sys.exit(0 if ok else 1)
