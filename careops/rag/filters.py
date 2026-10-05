from __future__ import annotations

from datetime import date

from careops.semantic import access_policy

from .corpus import Chunk


def filter_chunks(
    chunks: list[Chunk],
    *,
    role: str,
    as_of: date,
) -> list[Chunk]:
    """Authorization + effective-date filtering before retrieval (governed path)."""
    policy = access_policy()["roles"]
    if role not in policy:
        raise PermissionError(f"Unknown role: {role}")
    allowed = set(policy[role]["document_classes"])
    kept: list[Chunk] = []
    for chunk in chunks:
        if chunk.doc_class not in allowed:
            continue
        if role not in chunk.roles:
            continue
        start = date.fromisoformat(chunk.effective_from)
        end = date.fromisoformat(chunk.effective_to) if chunk.effective_to else None
        if start <= as_of and (end is None or as_of <= end):
            kept.append(chunk)
    return kept
