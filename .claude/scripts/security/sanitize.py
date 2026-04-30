"""Sanitize untrusted external content — Phase 8 Layer 1.

Gmail snippets, Slack messages, GitHub issue bodies, calendar event titles —
all of that is attacker-controlled in principle. Before embedding any of it in
a prompt, wrap it so the model treats it as data, not instructions.

Two operations:
  * `escape(text)` — collapse control chars, neutralize fenced-code and
    backtick tricks, and flag a few well-known prompt-injection triggers.
  * `wrap_external(text, source)` — return a tagged block the LLM has been
    told to treat as untrusted data.

Both are stdlib-only.
"""
from __future__ import annotations

import re

# Known prompt-injection triggers. We don't try to be clever — we just mark
# occurrences so the LLM's attention is drawn to "this is suspicious".
_INJECTION_PATTERNS = [
    re.compile(r"\bignore\s+(?:all\s+)?(?:previous|prior|earlier)\s+(?:instructions|messages)\b", re.I),
    re.compile(r"\bdisregard\s+(?:the\s+)?(?:system|assistant|user)\b", re.I),
    re.compile(r"\bsystem\s*prompt\b|\bsystem\s*message\b", re.I),
    re.compile(r"\byou\s+are\s+(?:now\s+)?(?:a\s+)?(?:different|new)\s+(?:assistant|ai|bot|model)\b", re.I),
    re.compile(r"<\s*/?\s*(?:system|assistant|user|instructions?)\s*>", re.I),
    re.compile(r"\[\[\s*(?:system|override|admin)\s*\]\]", re.I),
]

# Characters that break out of a fenced code block or an inline backtick span.
_BACKTICK_RE = re.compile(r"`")
_FENCE_RE = re.compile(r"^```", re.MULTILINE)

# Zero-width / format control characters that have no business in plaintext.
_CONTROL_RE = re.compile(r"[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F\u200B-\u200F\u2028\u2029\u202A-\u202E\u2066-\u2069\uFEFF]")


def detect_injections(text: str) -> list[str]:
    """Return a list of human-readable reasons this text is suspicious."""
    flags: list[str] = []
    for rx in _INJECTION_PATTERNS:
        m = rx.search(text or "")
        if m:
            snippet = m.group(0)[:80]
            flags.append(f"prompt-injection-pattern: {snippet!r}")
    return flags


def escape(text: str) -> str:
    """Collapse obvious injection vectors without lossy transformation.

    - Strip zero-width + format control characters.
    - Neutralize triple-backtick fences by replacing them with a visible glyph.
    - Escape inline backticks so stray code-quotes don't bleed into a fenced
      block we wrap this content in.
    """
    if not text:
        return ""
    text = _CONTROL_RE.sub("", text)
    text = _FENCE_RE.sub("'''", text)        # defang fence openers
    text = _BACKTICK_RE.sub("\u02cb", text)  # U+02CB MODIFIER LETTER GRAVE ACCENT — looks like `
    return text


def wrap_external(text: str, *, source: str, untrusted: bool = True) -> str:
    """Wrap `text` in an `<external-content>` block for prompts.

    The tag tells the model (and any reader) that everything inside is data.
    If `detect_injections` finds anything, we prepend a `flags=` attribute so
    the model knows to be extra cautious about the content.
    """
    if text is None:
        text = ""
    flags = detect_injections(text)
    safe = escape(text)
    attrs = f'source="{source}" untrusted="{str(untrusted).lower()}"'
    if flags:
        attrs += f' flags="{",".join(flags)}"'
    return f"<external-content {attrs}>\n{safe}\n</external-content>"
