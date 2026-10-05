"""Intentionally unsafe retrieval example.

This is NOT recommended architecture. It ignores authorization and effective dates
so the failure is easy to compare with careops.policy.search_policy().
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKEN_RE = re.compile(r"[a-z0-9_]+")


def tokens(text):
    return set(TOKEN_RE.findall(text.lower()))


def main(query: str):
    q = tokens(query)
    index = json.loads((ROOT / "knowledge/index.json").read_text())
    hits = []
    for item in index:
        content = (ROOT / item["path"]).read_text()
        score = len(q & tokens(content + " " + item["topic"]))
        if score:
            hits.append((score, item, content))
    hits.sort(reverse=True, key=lambda x: x[0])
    for score, item, content in hits[:5]:
        print(f"\n[{item['id']}] score={score} class={item['class']} effective={item['effective_from']}..{item.get('effective_to')}")
        print(content.strip())


if __name__ == "__main__":
    main(" ".join(sys.argv[1:]) or "network eligibility credentialing escalation")
