from __future__ import annotations

import sqlite3
from difflib import SequenceMatcher

from .bootstrap import bootstrap
from .paths import DB_PATH


def _ensure_db():
    if not DB_PATH.exists():
        bootstrap()


def resolve_provider(value: str, *, threshold: float = 0.72):
    _ensure_db()
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute("SELECT provider_id, provider_name, state FROM providers").fetchall()
    finally:
        conn.close()

    normalized = value.strip().lower()
    exact = [r for r in rows if normalized in {r[0].lower(), r[1].lower()}]
    if exact:
        r = exact[0]
        return {"provider_id": r[0], "provider_name": r[1], "state": r[2], "confidence": 1.0, "method": "exact"}

    candidates = []
    for r in rows:
        score = SequenceMatcher(None, normalized, r[1].lower()).ratio()
        candidates.append((score, r))
    candidates.sort(reverse=True, key=lambda x: x[0])
    score, r = candidates[0]
    if score < threshold:
        return {"matched": False, "confidence": round(score, 3), "requires_review": True}
    return {
        "provider_id": r[0], "provider_name": r[1], "state": r[2],
        "confidence": round(score, 3), "method": "fuzzy", "requires_review": score < 0.9
    }
