"""Three specialists. Each returns a finding. None of them commits, and none calls another."""

from __future__ import annotations

from datetime import date

from careops.context.episode import narrate
from careops.context.store import LocalTemporalStore
from careops.policy import search_policy
from careops.semantic import metric_definition

AS_OF = date(2026, 10, 5)
POLICY_QUESTION = "What is the current network eligibility policy?"


def observe_data_quality() -> dict:
    contract = metric_definition("available_appointments")
    return {
        "agent": "DataQualityAgent",
        "delegation_request": None,
        "finding": {
            "validity_type": "OBSERVED",
            "evidence_root_ids": [f"metric:{contract['metric_id']}"],
            "producer": "DataQualityAgent",
            "source_refs": [contract["source"]],
            "confidence": 1.0,
        },
    }


def observe_temporal_context() -> dict:
    episodes = LocalTemporalStore.from_fixture().preceding("tenant-demo", "provider:P002", "2026-10-05")
    inactive = next(episode for episode in episodes if episode["episode_id"] == "ep-p002-inactive")
    sentence = narrate([inactive])
    return {
        "agent": "TemporalContextAgent",
        "delegation_request": {"requested_capability": "policy_interpretation"},
        "narration": sentence,
        "finding": {
            "validity_type": "INFERRED",
            "evidence_root_ids": ["episode:ep-p002-inactive"],
            "producer": "TemporalContextAgent",
            "source_refs": [inactive["episode_id"]],
            "confidence": 0.4,
        },
    }


def observe_policy(*, role: str) -> dict:
    try:
        documents = search_policy(POLICY_QUESTION, role=role, as_of=AS_OF)
    except PermissionError:
        documents = []
    document_id = documents[0]["id"] if documents else ""
    root = f"policy:{document_id}" if document_id else "policy:none"
    return {
        "agent": "PolicyAgent",
        "delegation_request": None,
        "finding": {
            "validity_type": "OBSERVED" if document_id else "INFERRED",
            "evidence_root_ids": [root],
            "producer": "PolicyAgent",
            "source_refs": [document_id] if document_id else [],
            "confidence": 1.0 if document_id else 0.0,
        },
    }
