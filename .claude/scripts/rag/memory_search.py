"""Hybrid retrieval CLI: vector KNN (0.7) + FTS5 BM25 (0.3), merged.

Usage:
  python memory_search.py "query text"
  python memory_search.py "query" --path-prefix drafts/sent
  python memory_search.py "query" --k 10 --json

Exclusion: `drafts/expired/` is excluded from results by default. To include it,
pass `--path-prefix drafts/expired` (explicit opt-in).
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

from db import DB_PATH, EMBED_DIM, connect, init_schema
from embeddings import embed_query

DEFAULT_EXCLUDE_PREFIX = "drafts/expired/"
VEC_WEIGHT = 0.7
FTS_WEIGHT = 0.3
CANDIDATE_MULTIPLIER = 3


@dataclass
class Hit:
    score: float
    vec_score: float
    fts_score: float
    path: str
    chunk_idx: int
    heading: str
    snippet: str

    def to_json(self) -> dict:
        return asdict(self)


def encode_query(vec: list[float]) -> bytes:
    return struct.pack(f"{EMBED_DIM}f", *vec)


def _normalize(scores: dict[int, float]) -> dict[int, float]:
    if not scores:
        return {}
    vals = list(scores.values())
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        return {k: 1.0 for k in scores}
    return {k: (v - lo) / (hi - lo) for k, v in scores.items()}


def vector_search(conn, qvec: list[float], k: int) -> dict[int, float]:
    rows = conn.execute(
        """
        SELECT rowid, distance
        FROM vec_chunks
        WHERE embedding MATCH ?
          AND k = ?
        ORDER BY distance
        """,
        (encode_query(qvec), k),
    ).fetchall()
    # smaller distance = better → convert to similarity so higher = better
    return {rid: -dist for rid, dist in rows}


def fts_search(conn, query: str, k: int) -> dict[int, float]:
    # FTS5 `rank` is more-negative-is-better (bm25 convention); flip sign.
    try:
        rows = conn.execute(
            """
            SELECT rowid, rank
            FROM chunks_fts
            WHERE chunks_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, k),
        ).fetchall()
    except Exception:
        # Unparseable FTS query (e.g. user typed quotes/operators). Retry sanitized.
        sanitized = " ".join(w for w in query.split() if w.isalnum())
        if not sanitized:
            return {}
        rows = conn.execute(
            """
            SELECT rowid, rank FROM chunks_fts
            WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?
            """,
            (sanitized, k),
        ).fetchall()
    return {rid: -rank for rid, rank in rows}


def fetch_chunk(conn, chunk_id: int) -> tuple[str, int, str, str] | None:
    row = conn.execute(
        "SELECT path, chunk_idx, heading, text FROM chunks WHERE id = ?",
        (chunk_id,),
    ).fetchone()
    return row


def snippet(text: str, max_len: int = 240) -> str:
    text = " ".join(text.split())
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def search(
    conn,
    query: str,
    k: int,
    path_prefix: str | None,
) -> list[Hit]:
    candidate_k = max(k * CANDIDATE_MULTIPLIER, 20)
    qvec = embed_query(query)
    vec_raw = vector_search(conn, qvec, candidate_k)
    fts_raw = fts_search(conn, query, candidate_k)

    vec_norm = _normalize(vec_raw)
    fts_norm = _normalize(fts_raw)

    all_ids = set(vec_norm) | set(fts_norm)
    scored: list[Hit] = []
    for cid in all_ids:
        row = fetch_chunk(conn, cid)
        if row is None:
            continue
        path, chunk_idx, heading, text = row

        # Default exclusion: drafts/expired/ unless user explicitly asks for it
        if path_prefix is None:
            if path.startswith(DEFAULT_EXCLUDE_PREFIX):
                continue
        else:
            if not path.startswith(path_prefix):
                continue

        v = vec_norm.get(cid, 0.0)
        f = fts_norm.get(cid, 0.0)
        combined = VEC_WEIGHT * v + FTS_WEIGHT * f
        scored.append(
            Hit(
                score=combined,
                vec_score=v,
                fts_score=f,
                path=path,
                chunk_idx=chunk_idx,
                heading=heading or "",
                snippet=snippet(text),
            )
        )

    scored.sort(key=lambda h: h.score, reverse=True)
    return scored[:k]


def format_text(hits: list[Hit]) -> str:
    if not hits:
        return "(no matches)"
    lines: list[str] = []
    for i, h in enumerate(hits, 1):
        loc = f"{h.path}#{h.chunk_idx}"
        head = f" — {h.heading}" if h.heading else ""
        lines.append(
            f"{i:>2}. [{h.score:.3f} v={h.vec_score:.2f} f={h.fts_score:.2f}] {loc}{head}\n    {h.snippet}"
        )
    return "\n".join(lines)


def main() -> int:
    # Windows default stdout is cp1252 — vault content contains em-dashes/arrows.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("query", help="natural-language query")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--path-prefix", help="restrict to paths starting with this prefix (e.g. drafts/sent)")
    ap.add_argument("--db", type=Path, default=DB_PATH)
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = ap.parse_args()

    if not args.db.exists():
        sys.stderr.write(f"index not found: {args.db}\nRun memory_index.py --full first.\n")
        return 2

    conn = connect(args.db)
    init_schema(conn)

    hits = search(conn, args.query, args.k, args.path_prefix)
    conn.close()

    if args.json:
        print(json.dumps([h.to_json() for h in hits], indent=2))
    else:
        print(format_text(hits))
    return 0


if __name__ == "__main__":
    sys.exit(main())
