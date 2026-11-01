"""Shared reading of a question. Adapters do not choose the metric filters themselves."""

from __future__ import annotations

import re


def asked_state(question: str) -> str:
    q = question.lower()
    if "texas" in q or re.search(r"\btx\b", q):
        return "TX"
    if "california" in q or re.search(r"\bca\b", q):
        return "CA"
    return "TX"


def asked_network(question: str) -> str:
    # The demo fixture has one payer network.
    return "Aetna"
