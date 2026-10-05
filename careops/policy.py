from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from .paths import ROOT, KNOWLEDGE_DIR
from .semantic import access_policy

TOKEN_RE = re.compile(r"[a-z0-9_]+")


def _tokens(text: str):
    return set(TOKEN_RE.findall(text.lower()))


def _index():
    return json.loads((KNOWLEDGE_DIR / "index.json").read_text(encoding="utf-8"))


def _allowed_classes(role: str):
    policy = access_policy()["roles"]
    if role not in policy:
        raise PermissionError(f"Unknown role: {role}")
    return set(policy[role]["document_classes"])


def _effective(item: dict, as_of: date):
    start = date.fromisoformat(item["effective_from"])
    end = date.fromisoformat(item["effective_to"]) if item.get("effective_to") else None
    return start <= as_of and (end is None or as_of <= end)


def search_policy(query: str, *, role: str, as_of: date = date(2026, 10, 5), limit: int = 3):
    allowed_classes = _allowed_classes(role)
    qtokens = _tokens(query)
    scored = []
    for item in _index():
        if item["class"] not in allowed_classes:
            continue
        if role not in item.get("roles", []):
            continue
        if not _effective(item, as_of):
            continue
        content = (ROOT / item["path"]).read_text(encoding="utf-8")
        score = len(qtokens & (_tokens(item["topic"]) | _tokens(content)))
        if score <= 0:
            continue
        scored.append((score, item.get("authority", 0), item, content))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [
        {
            "id": item["id"],
            "topic": item["topic"],
            "effective_from": item["effective_from"],
            "effective_to": item.get("effective_to"),
            "class": item["class"],
            "source": item["path"],
            "content": content.strip(),
            "score": score,
        }
        for score, _, item, content in scored[:limit]
    ]
