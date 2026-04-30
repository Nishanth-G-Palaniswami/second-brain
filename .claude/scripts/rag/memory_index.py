"""Walk the vault and (re)index changed markdown files.

Incremental by default: reads mtime+sha from the `files` table and only re-chunks
files whose mtime OR sha differs. `--full` wipes the DB first.

Usage:
  python memory_index.py               # incremental
  python memory_index.py --full        # from scratch
  python memory_index.py --vault PATH  # override default vault
"""
from __future__ import annotations

import argparse
import hashlib
import os
import struct
import sys
import time
from pathlib import Path

from db import DB_PATH, EMBED_DIM, connect, delete_file_entries, delete_missing_files, init_schema
from chunker import chunk_markdown
from embeddings import embed_passages

DEFAULT_VAULT = Path(os.environ.get("SECOND_BRAIN_VAULT", str(Path.home() / "second-brain-vault")))
EMBED_BATCH = 32
EXCLUDE_DIR_PARTS = {".obsidian", ".trash", ".git"}


def iter_markdown_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # prune hidden/system dirs in place
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIR_PARTS and not d.startswith(".")]
        for name in filenames:
            if name.lower().endswith(".md"):
                out.append(Path(dirpath) / name)
    return out


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def encode_embedding(vec: list[float]) -> bytes:
    if len(vec) != EMBED_DIM:
        raise ValueError(f"embedding dim {len(vec)} != {EMBED_DIM}")
    return struct.pack(f"{EMBED_DIM}f", *vec)


def index_file(conn, vault: Path, path: Path) -> int:
    rel = str(path.relative_to(vault)).replace("\\", "/")
    mtime = path.stat().st_mtime
    sha = file_sha(path)

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        sys.stderr.write(f"[skip] {rel}: {exc}\n")
        return 0

    chunks = chunk_markdown(text, rel)
    if not chunks:
        delete_file_entries(conn, rel)
        conn.execute(
            "INSERT INTO files(path, mtime, sha, indexed_at) VALUES(?,?,?,?)",
            (rel, mtime, sha, time.time()),
        )
        return 0

    # Embed in batches
    texts = [c.text for c in chunks]
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH):
        vectors.extend(embed_passages(texts[start : start + EMBED_BATCH]))

    delete_file_entries(conn, rel)
    for c, vec in zip(chunks, vectors, strict=True):
        cur = conn.execute(
            "INSERT INTO chunks(path, chunk_idx, heading, text, tokens) VALUES(?,?,?,?,?)",
            (rel, c.chunk_idx, c.heading, c.text, c.tokens),
        )
        chunk_id = cur.lastrowid
        conn.execute(
            "INSERT INTO vec_chunks(rowid, embedding) VALUES(?, ?)",
            (chunk_id, encode_embedding(vec)),
        )
        conn.execute(
            "INSERT INTO chunks_fts(rowid, text) VALUES(?, ?)",
            (chunk_id, c.text),
        )

    conn.execute(
        "INSERT INTO files(path, mtime, sha, indexed_at) VALUES(?,?,?,?)",
        (rel, mtime, sha, time.time()),
    )
    return len(chunks)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", type=Path, default=DEFAULT_VAULT)
    ap.add_argument("--full", action="store_true", help="wipe DB before indexing")
    ap.add_argument("--db", type=Path, default=DB_PATH)
    args = ap.parse_args()

    vault: Path = args.vault
    if not vault.is_dir():
        sys.stderr.write(f"vault not found: {vault}\n")
        return 2

    if args.full and args.db.exists():
        args.db.unlink()

    conn = connect(args.db)
    init_schema(conn)

    files = iter_markdown_files(vault)
    current_rel = {str(p.relative_to(vault)).replace("\\", "/") for p in files}
    removed = delete_missing_files(conn, current_rel)
    if removed:
        print(f"[prune] removed {len(removed)} missing file(s)")

    tracked = {
        row[0]: (row[1], row[2])
        for row in conn.execute("SELECT path, mtime, sha FROM files").fetchall()
    }

    reindexed = 0
    new_chunks = 0
    t0 = time.time()
    for path in files:
        rel = str(path.relative_to(vault)).replace("\\", "/")
        stat_mtime = path.stat().st_mtime
        prev = tracked.get(rel)
        if prev is not None and abs(prev[0] - stat_mtime) < 1e-3:
            # mtime match — skip sha check to avoid hashing every file
            continue
        # mtime differs: check sha to avoid re-embedding if touched but unchanged
        sha = file_sha(path)
        if prev is not None and prev[1] == sha:
            conn.execute("UPDATE files SET mtime=? WHERE path=?", (stat_mtime, rel))
            continue
        n = index_file(conn, vault, path)
        reindexed += 1
        new_chunks += n
        print(f"[index] {rel} — {n} chunks")

    conn.commit()
    conn.close()
    print(
        f"done: {reindexed} file(s) reindexed, {new_chunks} chunk(s) written, "
        f"{len(files) - reindexed} unchanged, {time.time() - t0:.1f}s"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
