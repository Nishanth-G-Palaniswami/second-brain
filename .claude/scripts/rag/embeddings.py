"""FastEmbed wrapper. Lazy-loads sentence-transformers/all-MiniLM-L6-v2 (384-dim).

Models are cached under `.claude/data/models/` so the first run downloads ~80MB there
and subsequent runs are offline.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from fastembed import TextEmbedding

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MODEL_CACHE = PROJECT_ROOT / ".claude" / "data" / "models"
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

_model: TextEmbedding | None = None


def _get_model() -> TextEmbedding:
    global _model
    if _model is None:
        MODEL_CACHE.mkdir(parents=True, exist_ok=True)
        _model = TextEmbedding(model_name=MODEL_NAME, cache_dir=str(MODEL_CACHE))
    return _model


def embed_passages(texts: Iterable[str]) -> list[list[float]]:
    """Embed passages (document-side). Returns list of 384-float vectors."""
    model = _get_model()
    return [vec.tolist() for vec in model.passage_embed(list(texts))]


def embed_query(text: str) -> list[float]:
    """Embed a single query (query-side)."""
    model = _get_model()
    return next(iter(model.query_embed([text]))).tolist()
