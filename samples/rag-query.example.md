# Memory search — example query

Synthetic walkthrough of `.claude/scripts/rag/memory_search.py`. Real values from a live vault would include actual file paths and line numbers; the structure is identical.

## Command

```
python .claude/scripts/rag/memory_search.py "deterministic data pipeline" --k 3
```

## Output

```
1. score 0.83  projects/example-project/decisions.md:14
   "Picked golden-set regression tests over snapshot diffs because we need a
   deterministic check that survives refactors. The 50-row golden set runs in
   4ms; full snapshot diff was 600ms and produced false positives on row order."

2. score 0.71  daily/2026-04-22.md:31
   "Spent the morning making the ingestion path deterministic. Removed the
   non-deterministic dict iteration in the dedup step (Python 3.7+ preserves
   insertion order but the upstream sort key was unstable). Added a property
   test."

3. score 0.62  runbooks/eval-gating.md:7
   "Eval gating runs the golden tests on every PR. Anything that breaks them
   blocks the merge — no manual override. Determinism is the whole point: a
   flaky eval is worse than no eval."
```

## How it ranks

The CLI calls `memory_search.py --mode hybrid` under the hood. Each candidate gets two scores:

- **vector score** — cosine similarity between the query embedding (fastembed `all-MiniLM-L6-v2`, 384 dims, local ONNX) and the chunk embedding stored in `sqlite-vec`.
- **keyword score** — SQLite `FTS5` BM25 over the same chunks.

Final score is `0.7 * vector + 0.3 * keyword`. The 0.7 / 0.3 split is intentional: pure vector misses exact-match queries (proper nouns, file names, error messages); pure keyword misses paraphrases. The hybrid keeps both useful.

The index is incremental — `memory_index.py` only re-embeds files whose `mtime` or content `sha` has changed since the last run. A typical reindex on a 2,000-file vault takes <2 seconds after the first cold build (~80 MB of model cache, ~30 MB of embeddings).

## What this means in practice

When the agent asks for context on "deterministic data pipeline", it doesn't just get keyword matches — it gets the decision file that *justified* the determinism choice, the daily log where the work happened, and the runbook that operationalizes it. Three files, three different angles, all pulled by similarity rather than by hand.
