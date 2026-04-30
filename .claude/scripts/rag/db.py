"""SQLite schema + connection helper for the hybrid RAG index.

One DB at `.claude/data/memory.db` with:
  - `files(path, mtime, sha, indexed_at)`           — source-file bookkeeping for incremental re-index
  - `chunks(id, path, chunk_idx, heading, text, tokens)` — chunk storage
  - `vec_chunks(rowid, embedding float[384])`       — sqlite-vec virtual table (vec0)
  - `chunks_fts(text, content='chunks', content_rowid='id')` — FTS5 external-content index

`vec_chunks.rowid` and `chunks_fts.rowid` are both aligned to `chunks.id` so we can JOIN.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import sqlite_vec

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = PROJECT_ROOT / ".claude" / "data" / "memory.db"
EMBED_DIM = 384  # all-MiniLM-L6-v2


def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS files (
            path       TEXT PRIMARY KEY,
            mtime      REAL NOT NULL,
            sha        TEXT NOT NULL,
            indexed_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chunks (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            path       TEXT NOT NULL,
            chunk_idx  INTEGER NOT NULL,
            heading    TEXT,
            text       TEXT NOT NULL,
            tokens     INTEGER NOT NULL,
            UNIQUE(path, chunk_idx)
        );

        CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path);

        CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(
            embedding float[{EMBED_DIM}]
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            text,
            content='chunks',
            content_rowid='id',
            tokenize='porter unicode61'
        );
        """
    )
    conn.commit()


def delete_file_entries(conn: sqlite3.Connection, path: str) -> None:
    """Remove all chunks/embeddings/FTS rows for a file before re-indexing it."""
    rows = conn.execute("SELECT id FROM chunks WHERE path = ?", (path,)).fetchall()
    if rows:
        ids = [r[0] for r in rows]
        placeholders = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM vec_chunks WHERE rowid IN ({placeholders})", ids)
        conn.execute(f"DELETE FROM chunks_fts WHERE rowid IN ({placeholders})", ids)
        conn.execute("DELETE FROM chunks WHERE path = ?", (path,))
    conn.execute("DELETE FROM files WHERE path = ?", (path,))


def delete_missing_files(conn: sqlite3.Connection, current_paths: set[str]) -> list[str]:
    """Drop index entries for files that no longer exist on disk. Returns removed paths."""
    tracked = {r[0] for r in conn.execute("SELECT path FROM files").fetchall()}
    gone = sorted(tracked - current_paths)
    for p in gone:
        delete_file_entries(conn, p)
    return gone
