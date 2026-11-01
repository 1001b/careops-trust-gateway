"""Attach temporal context beside a metric result. The metric function is not called here."""

from __future__ import annotations

from careops.context.episode import narrate, present
from careops.context.store import ContextUnavailable, LocalTemporalStore


def load_store() -> LocalTemporalStore:
    return LocalTemporalStore.from_fixture()


def contextualize(evidence: list[dict], *, organization_id: str, as_of: str, store=None) -> tuple[list[dict], str, str | None]:
    """Return evidence, a plane label, and an optional sentence.

    A failure of the context store leaves the evidence unchanged.
    """
    provider_refs = []
    for item in evidence:
        if item.get("type") != "metric":
            continue
        for row in item.get("contributors") or []:
            provider_id = row.get("provider_id")
            if provider_id:
                provider_refs.append(f"provider:{provider_id}")
    if not provider_refs:
        return evidence, "not_requested", None
    try:
        active = store if store is not None else load_store()
        episodes = []
        for ref in dict.fromkeys(provider_refs):
            episodes.extend(active.preceding(organization_id, ref, as_of))
    except ContextUnavailable:
        return evidence, "unavailable", "Historical context is unavailable."
    shown = [present(episode) for episode in episodes]
    sentence = narrate(shown)
    if not shown:
        return evidence, "available", None
    attached = evidence + [{
        "type": "temporal_context",
        "authority": False,
        "causality": "not_asserted",
        "episodes": shown,
        "narration": sentence,
    }]
    return attached, "available", sentence


def evidence_planes(evidence: list[dict] | None, temporal_status: str) -> dict:
    kinds = {item.get("type") for item in evidence or []}
    return {
        "authoritative_structured": "metric" in kinds,
        "policy": "policy" in kinds,
        "temporal_context": temporal_status,
        "model": False,
    }
