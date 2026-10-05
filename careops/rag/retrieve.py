from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .corpus import Chunk
from .embed import Embedder

TOKEN_RE = re.compile(r"[a-z0-9_]+")


@dataclass
class ScoredChunk:
    chunk: Chunk
    dense_score: float
    lexical_score: float
    rrf_score: float
    rank: int


def _tokens(text: str) -> set[str]:
    return set(TOKEN_RE.findall(text.lower()))


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def _lexical_score(query: str, chunk: Chunk) -> float:
    q = _tokens(query)
    if not q:
        return 0.0
    doc = _tokens(chunk.topic) | _tokens(chunk.text)
    return len(q & doc) / math.sqrt(len(q) * max(len(doc), 1))


def _rrf(rank_lists: list[list[str]], k: int = 60) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranks in rank_lists:
        for i, chunk_id in enumerate(ranks):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + i + 1)
    return scores


def hybrid_retrieve(
    query: str,
    chunks: list[Chunk],
    embedder: Embedder,
    *,
    limit: int = 5,
    candidate_pool: int = 20,
) -> list[ScoredChunk]:
    if not chunks:
        return []
    texts = [f"{c.topic}\n{c.text}" for c in chunks]
    doc_vecs = embedder.embed(texts)
    q_vec = embedder.embed([query])[0]

    dense_ranked = sorted(
        (( _cosine(q_vec, v), c) for c, v in zip(chunks, doc_vecs, strict=True)),
        key=lambda x: x[0],
        reverse=True,
    )
    lex_ranked = sorted(
        ((_lexical_score(query, c), c) for c in chunks),
        key=lambda x: x[0],
        reverse=True,
    )

    # Tiny synthetic corpus: keep full top pool even when lexical overlap is sparse
    dense_ids = [c.chunk_id for _, c in dense_ranked[:candidate_pool]]
    lex_ids = [c.chunk_id for score, c in lex_ranked[:candidate_pool] if score > 0]
    if not lex_ids:
        lex_ids = [c.chunk_id for _, c in lex_ranked[:candidate_pool]]

    fused = _rrf([dense_ids, lex_ids])
    # Governed-friendly tie-break: slight authority signal inside RRF ordering only via sort key later
    by_id = {c.chunk_id: c for c in chunks}
    dense_map = {c.chunk_id: s for s, c in dense_ranked}
    lex_map = {c.chunk_id: s for s, c in lex_ranked}

    ordered = sorted(
        fused.items(),
        key=lambda kv: (kv[1], by_id[kv[0]].authority, dense_map.get(kv[0], 0.0)),
        reverse=True,
    )[:limit]

    results: list[ScoredChunk] = []
    for rank, (cid, rrf) in enumerate(ordered, start=1):
        results.append(
            ScoredChunk(
                chunk=by_id[cid],
                dense_score=round(dense_map.get(cid, 0.0), 4),
                lexical_score=round(lex_map.get(cid, 0.0), 4),
                rrf_score=round(rrf, 6),
                rank=rank,
            )
        )
    return results
