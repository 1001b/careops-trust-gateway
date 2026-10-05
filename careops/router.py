from __future__ import annotations

METRIC_TERMS = {"how many", "count", "rate", "availability", "decline", "increase", "decrease", "last week", "prior week"}
KNOWLEDGE_TERMS = {"what does", "mean", "definition", "policy", "procedure", "eligible", "eligibility", "why"}


def classify(question: str) -> str:
    q = question.lower()
    has_metric = any(term in q for term in METRIC_TERMS)
    has_knowledge = any(term in q for term in KNOWLEDGE_TERMS)
    if has_metric and has_knowledge:
        return "mixed"
    if has_metric:
        return "metric"
    if has_knowledge:
        return "knowledge"
    return "unknown"
