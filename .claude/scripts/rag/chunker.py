"""Markdown chunker.

Strategy:
  1. Split on ATX headings (`#`..`######`) so each section is a candidate chunk.
  2. If a section exceeds the target size, split further on paragraph blanks.
  3. Merge too-small adjacent sections until they hit the target.
  4. Preserve the heading-path (breadcrumbs) as chunk metadata.

Targets:
  - Default: 400 tokens per chunk, 50-token overlap.
  - Runbooks (`runbooks/` in path): 200 tokens per chunk, 30-token overlap —
    commands/API snippets are short and need precise retrieval.

Tokens are approximated as `max(1, round(len(text) / 4))` — good enough for
chunk sizing; the embedding model re-tokenizes internally.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)
PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")


@dataclass
class Chunk:
    chunk_idx: int
    heading: str        # breadcrumb like "Phase 3 > Key files"
    text: str
    tokens: int


def _approx_tokens(text: str) -> int:
    return max(1, round(len(text) / 4))


def _target_sizes(path: str) -> tuple[int, int]:
    # (target_tokens, overlap_tokens)
    if "runbooks" in path.replace("\\", "/").split("/"):
        return 200, 30
    return 400, 50


def _tokens_to_chars(tokens: int) -> int:
    return tokens * 4


def _split_sections(md: str) -> list[tuple[str, str]]:
    """Return list of (heading_breadcrumb, body) pairs.

    Maintains a heading stack so nested headings get "Parent > Child" breadcrumbs.
    Content before the first heading gets heading="".
    """
    out: list[tuple[str, str]] = []
    matches = list(HEADING_RE.finditer(md))
    if not matches:
        return [("", md.strip())]

    # prefix (before first heading)
    if matches[0].start() > 0:
        prefix = md[: matches[0].start()].strip()
        if prefix:
            out.append(("", prefix))

    stack: list[tuple[int, str]] = []  # (level, heading_text)
    for i, m in enumerate(matches):
        level = len(m.group(1))
        heading = m.group(2).strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, heading))
        breadcrumb = " > ".join(h for _, h in stack)

        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md)
        body = md[start:end].strip()
        if body:
            out.append((breadcrumb, body))
    return out


def _split_long_body(body: str, target_chars: int, overlap_chars: int) -> list[str]:
    """Split a body that exceeds target size. Prefer paragraph boundaries, then char windows."""
    if len(body) <= target_chars:
        return [body]

    # First try paragraph-level packing.
    paragraphs = [p.strip() for p in PARAGRAPH_SPLIT.split(body) if p.strip()]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for p in paragraphs:
        plen = len(p) + 2  # account for the blank-line separator
        if current and current_len + plen > target_chars:
            chunks.append("\n\n".join(current))
            # seed next chunk with an overlap tail of the previous chunk
            tail = chunks[-1][-overlap_chars:] if overlap_chars else ""
            current = [tail, p] if tail else [p]
            current_len = len(tail) + plen
        else:
            current.append(p)
            current_len += plen
    if current:
        chunks.append("\n\n".join(current))

    # Any chunk still too long? fall back to hard char windows with overlap.
    final: list[str] = []
    for c in chunks:
        if len(c) <= target_chars * 1.2:
            final.append(c)
            continue
        step = max(1, target_chars - overlap_chars)
        for start in range(0, len(c), step):
            final.append(c[start : start + target_chars])
    return final


def chunk_markdown(md: str, path: str) -> list[Chunk]:
    target_tokens, overlap_tokens = _target_sizes(path)
    target_chars = _tokens_to_chars(target_tokens)
    overlap_chars = _tokens_to_chars(overlap_tokens)
    min_chars = target_chars // 4  # sections smaller than this get merged

    sections = _split_sections(md)

    # Merge tiny adjacent sections that share the same top-level heading.
    merged: list[tuple[str, str]] = []
    for heading, body in sections:
        if merged and len(merged[-1][1]) < min_chars:
            prev_head, prev_body = merged[-1]
            # only merge if headings are related (same first segment) or both empty
            prev_root = prev_head.split(" > ")[0] if prev_head else ""
            cur_root = heading.split(" > ")[0] if heading else ""
            if prev_root == cur_root:
                merged[-1] = (
                    prev_head or heading,
                    f"{prev_body}\n\n## {heading}\n\n{body}" if heading else f"{prev_body}\n\n{body}",
                )
                continue
        merged.append((heading, body))

    chunks: list[Chunk] = []
    idx = 0
    for heading, body in merged:
        parts = _split_long_body(body, target_chars, overlap_chars)
        for part in parts:
            part = part.strip()
            if not part:
                continue
            chunks.append(
                Chunk(
                    chunk_idx=idx,
                    heading=heading,
                    text=part,
                    tokens=_approx_tokens(part),
                )
            )
            idx += 1
    return chunks
