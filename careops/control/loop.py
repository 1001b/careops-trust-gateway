"""Repeated-work signature. The workflow version is checked separately and is not part of this key."""

from __future__ import annotations


def loop_signature(*, objective: str, evidence_refs: list, status: str) -> str:
    roots = ",".join(sorted(str(item) for item in evidence_refs))
    return f"{objective.strip().lower()}|{roots}|{status}"
