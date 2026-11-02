"""TC-08: durable control state that cannot grant domain finality."""

from __future__ import annotations

import asyncio
import importlib
import inspect

import pytest

from careops.control import (
    ControlError,
    ControlStore,
    DecisionRequest,
    FixtureDecisionGate,
    corroboration,
)
from careops.control.schema import WORKFLOW_STATUSES
from careops.metrics import get_metric

AUTHORITY = "available_appointments:1"


def _store(tmp_path) -> ControlStore:
    return ControlStore(tmp_path / "control.db")


def _open(store: ControlStore, **overrides) -> dict:
    fields = {
        "authority_snapshot_version": AUTHORITY,
        "model_budget": 2,
        "tool_budget": 2,
        "max_delegation_depth": 2,
        "loop_threshold": 2,
    }
    fields.update(overrides)
    return store.open_workflow(**fields)


def _reload(store: ControlStore, workflow_id: str) -> dict:
    return store.get_workflow(workflow_id)


def _task(store: ControlStore, workflow: dict, **overrides) -> dict:
    fields = {
        "expected_version": workflow["workflow_version"],
        "idempotency_key": "task-1",
        "requested_capability": "data_quality",
        "objective": "check the availability decline",
        "evidence_refs": ["root-a"],
        "correlation_id": "corr-ctl",
        "causation_id": "cause-ctl",
    }
    fields.update(overrides)
    return store.create_task(workflow["workflow_id"], **fields)


def _metric():
    prior = get_metric("available_appointments", state="TX", payer_network="Aetna", period="prior_week")
    current = get_metric("available_appointments", state="TX", payer_network="Aetna", period="last_week")
    return prior.value, current.value


def test_required_statuses_and_capabilities_are_bounded(tmp_path):
    assert {
        "RUNNING",
        "WAITING",
        "HUMAN_REVIEW",
        "COMPLETE",
        "INSUFFICIENT_EVIDENCE",
        "DENIED",
        "STALE",
        "FAILED",
        "BUDGET_EXCEEDED",
        "LOOP_DETECTED",
    } <= WORKFLOW_STATUSES
    store_source = inspect.getsource(importlib.import_module("careops.control.store"))
    gate_source = inspect.getsource(importlib.import_module("careops.control.gate"))
    assert "get_metric" not in store_source
    assert "ChangeStore" not in store_source
    assert "jev" not in gate_source.lower()
    assert "graphiti" not in store_source.lower()
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("careops.control.jev")
    store = _store(tmp_path)
    workflow = _open(store)
    reviewed = store.set_status(workflow["workflow_id"], workflow["workflow_version"], "HUMAN_REVIEW")
    assert reviewed["status"] == "HUMAN_REVIEW"
    with pytest.raises(ControlError) as closed:
        _task(store, reviewed, idempotency_key="after-review")
    assert closed.value.code == "workflow_closed"


def test_workflow_tasks_proposals_and_decisions_survive_restart(tmp_path):
    store = _store(tmp_path)
    workflow = _open(store)
    workflow = store.set_status(workflow["workflow_id"], workflow["workflow_version"], "WAITING")
    task = _task(store, workflow)
    workflow = _reload(store, workflow["workflow_id"])
    proposal = store.create_proposal(
        workflow["workflow_id"],
        expected_version=workflow["workflow_version"],
        proposed_by="DataQualityAgent",
        target_entity="provider:P002",
        action="note_network_status",
        expected_entity_version="1",
        evidence_refs=["root-a"],
    )
    workflow = _reload(store, workflow["workflow_id"])
    decision = store.record_decision(
        workflow["workflow_id"],
        expected_version=workflow["workflow_version"],
        question="Is another task warranted?",
        available_options=["COMPLETE", "REJECT"],
        selected_option="REJECT",
        evidence_refs=["root-a"],
    )
    finding = store.record_finding(
        validity_type="OBSERVED",
        evidence_root_ids=["root-a"],
        producer="DataQualityAgent",
        source_refs=["metric:available_appointments"],
    )
    restored = ControlStore(tmp_path / "control.db")
    assert restored.get_workflow(workflow["workflow_id"])["status"] == "WAITING"
    assert restored.get_task(task["task_id"])["idempotency_key"] == "task-1"
    assert restored.get_proposal(proposal["proposal_id"])["status"] == "DRAFT"
    assert restored.get_decision(decision["decision_id"])["authoritative"] is False
    assert restored.get_finding(finding["finding_id"])["validity_type"] == "OBSERVED"
    replayed = _task(restored, restored.get_workflow(workflow["workflow_id"]))
    assert replayed["task_id"] == task["task_id"]
    assert len(restored.get_workflow(workflow["workflow_id"])["active_tasks"]) == 1


def test_task_creation_and_result_ingestion_are_idempotent(tmp_path):
    store = _store(tmp_path)
    workflow = _open(store)
    first = _task(store, workflow)
    for field in (
        "workflow_id",
        "workflow_version",
        "task_id",
        "parent_task_id",
        "requested_capability",
        "assigned_agent",
        "objective",
        "authority_snapshot_version",
        "evidence_refs",
        "context_refs",
        "allowed_tools",
        "budget",
        "delegation_depth",
        "deadline",
        "idempotency_key",
        "correlation_id",
        "causation_id",
        "status",
    ):
        assert field in first
    assert first["assigned_agent"] == "DataQualityAgent"
    assert first["authority_snapshot_version"] == AUTHORITY
    assert first["domain_mutated"] is False
    second = _task(store, workflow)
    assert second["task_id"] == first["task_id"]
    workflow = _reload(store, workflow["workflow_id"])
    assert workflow["active_tasks"] == [first["task_id"]]
    version = workflow["workflow_version"]
    body = {"commit": True, "finding_refs": ["finding-1"], "proposals": [{"action": "note_network_status"}]}
    result = store.ingest_result(first["task_id"], expected_version=version, body=body)
    replay = store.ingest_result(first["task_id"], expected_version=version, body={"finding_refs": ["other"]})
    assert replay == result
    assert result["domain_mutated"] is False
    workflow = _reload(store, workflow["workflow_id"])
    assert workflow["completed_tasks"] == [first["task_id"]]
    assert workflow["active_tasks"] == []
    assert workflow["proposal_ids"] == []
    assert workflow["workflow_version"] == version + 1


def test_stale_workflow_update_is_rejected(tmp_path):
    store = _store(tmp_path)
    workflow = _open(store)
    _task(store, workflow)
    current = _reload(store, workflow["workflow_id"])
    with pytest.raises(ControlError) as caught:
        _task(store, current, expected_version=1, idempotency_key="task-2")
    assert caught.value.code == "stale_workflow"
    again = _reload(store, workflow["workflow_id"])
    assert again["workflow_version"] == current["workflow_version"]
    assert again["active_tasks"] == current["active_tasks"]
    assert again["status"] == "RUNNING"


def test_proposal_lifecycle_does_not_change_the_authoritative_metric(tmp_path):
    store = _store(tmp_path)
    workflow = _open(store)
    proposal = store.create_proposal(
        workflow["workflow_id"],
        expected_version=workflow["workflow_version"],
        proposed_by="DataQualityAgent",
        target_entity="provider:P002",
        action="note_network_status",
        expected_entity_version="1",
        evidence_refs=["root-a"],
    )
    assert proposal["expected_entity_version"] == "1"
    assert proposal["domain_mutated"] is False
    for status in ("PENDING_REVIEW", "APPROVED"):
        workflow = _reload(store, workflow["workflow_id"])
        proposal = store.transition_proposal(
            proposal["proposal_id"],
            status,
            expected_workflow_version=workflow["workflow_version"],
        )
    assert proposal["status"] == "APPROVED"
    assert _metric() == (42, 24)
    workflow = _reload(store, workflow["workflow_id"])
    proposal = store.transition_proposal(
        proposal["proposal_id"],
        "COMMITTING",
        expected_workflow_version=workflow["workflow_version"],
        current_entity_version="1",
    )
    workflow = _reload(store, workflow["workflow_id"])
    proposal = store.transition_proposal(
        proposal["proposal_id"],
        "COMMITTED",
        expected_workflow_version=workflow["workflow_version"],
        current_entity_version="1",
    )
    assert proposal["status"] == "COMMITTED"
    assert proposal["domain_mutated"] is False
    assert _reload(store, workflow["workflow_id"])["authority_snapshot_version"] == AUTHORITY
    assert _metric() == (42, 24)


def test_mismatched_entity_version_classifies_the_proposal_stale(tmp_path):
    store = _store(tmp_path)
    workflow = _open(store)
    proposal = store.create_proposal(
        workflow["workflow_id"],
        expected_version=workflow["workflow_version"],
        proposed_by="PolicyAgent",
        target_entity="provider:P002",
        action="note_network_status",
        expected_entity_version="1",
    )
    workflow = _reload(store, workflow["workflow_id"])
    proposal = store.transition_proposal(
        proposal["proposal_id"],
        "PENDING_REVIEW",
        expected_workflow_version=workflow["workflow_version"],
    )
    workflow = _reload(store, workflow["workflow_id"])
    proposal = store.transition_proposal(
        proposal["proposal_id"],
        "APPROVED",
        expected_workflow_version=workflow["workflow_version"],
    )
    workflow = _reload(store, workflow["workflow_id"])
    stale = store.transition_proposal(
        proposal["proposal_id"],
        "COMMITTING",
        expected_workflow_version=workflow["workflow_version"],
        current_entity_version="2",
    )
    assert stale["status"] == "STALE"
    assert stale["domain_mutated"] is False
    assert _reload(store, workflow["workflow_id"])["status"] == "STALE"
    assert _metric() == (42, 24)


def test_a_decision_does_not_authorize_or_commit(tmp_path):
    store = _store(tmp_path)
    workflow = _open(store)
    before = _reload(store, workflow["workflow_id"])
    result = asyncio.run(
        FixtureDecisionGate("COMPLETE").decide(
            DecisionRequest(
                workflow_id=workflow["workflow_id"],
                question="Is the evidence sufficient?",
                available_options=("COMPLETE", "VERIFY_MORE", "HUMAN_REVIEW", "REJECT"),
                evidence_refs=("root-a",),
                workflow_version=before["workflow_version"],
            )
        )
    )
    assert result.selected_option == "COMPLETE"
    assert result.authoritative is False
    assert _reload(store, workflow["workflow_id"]) == before
    recorded = store.record_decision(
        workflow["workflow_id"],
        expected_version=before["workflow_version"],
        question="Is the evidence sufficient?",
        available_options=["COMPLETE", "REJECT"],
        selected_option="COMPLETE",
        evidence_refs=["root-a"],
    )
    assert recorded["authoritative"] is False
    assert recorded["domain_mutated"] is False
    assert recorded["workflow_version"] == before["workflow_version"]
    saved = _reload(store, workflow["workflow_id"])
    assert saved["status"] == "RUNNING"
    assert saved["authority_snapshot_version"] == AUTHORITY
    assert _metric() == (42, 24)


def test_delegation_depth_and_budgets_stop_at_the_control_boundary(tmp_path):
    store = _store(tmp_path)
    workflow = _open(store, max_delegation_depth=2, model_budget=1, tool_budget=1)
    with pytest.raises(ControlError) as depth:
        _task(store, workflow, delegation_depth=3)
    assert depth.value.code == "delegation_depth_exceeded"
    assert _reload(store, workflow["workflow_id"])["workflow_version"] == 1
    assert _reload(store, workflow["workflow_id"])["delegation_count"] == 0
    accepted = _task(store, workflow, delegation_depth=2, idempotency_key="depth-ok")
    assert accepted["delegation_depth"] == 2
    workflow = _reload(store, workflow["workflow_id"])
    assert workflow["delegation_count"] == 1
    spent = store.consume_budget(workflow["workflow_id"], expected_version=workflow["workflow_version"], kind="model")
    assert spent["model_budget"] == 0
    assert spent["status"] == "RUNNING"
    with pytest.raises(ControlError) as budget:
        store.consume_budget(workflow["workflow_id"], expected_version=spent["workflow_version"], kind="model")
    assert budget.value.code == "budget_exceeded"
    exceeded = _reload(store, workflow["workflow_id"])
    assert exceeded["status"] == "BUDGET_EXCEEDED"
    with pytest.raises(ControlError) as again:
        store.consume_budget(workflow["workflow_id"], expected_version=exceeded["workflow_version"], kind="tool")
    assert again.value.code == "budget_exceeded"
    assert _reload(store, workflow["workflow_id"])["workflow_version"] == exceeded["workflow_version"]


def test_repeated_signature_becomes_loop_detected(tmp_path):
    store = _store(tmp_path)
    workflow = _open(store, loop_threshold=2)
    first = _task(store, workflow, idempotency_key="loop-1")
    workflow = _reload(store, workflow["workflow_id"])
    second = _task(store, workflow, idempotency_key="loop-2")
    assert first["task_id"] != second["task_id"]
    workflow = _reload(store, workflow["workflow_id"])
    with pytest.raises(ControlError) as caught:
        _task(store, workflow, idempotency_key="loop-3")
    assert caught.value.code == "loop_detected"
    finished = _reload(store, workflow["workflow_id"])
    assert finished["status"] == "LOOP_DETECTED"
    assert finished["active_tasks"] == [first["task_id"], second["task_id"]]


def test_shared_evidence_root_is_not_independent_corroboration(tmp_path):
    store = _store(tmp_path)
    first = store.record_finding(
        validity_type="INFERRED",
        evidence_root_ids=["root-1"],
        producer="DataQualityAgent",
        source_refs=["episode:ep-p002-inactive"],
        confidence=0.2,
    )
    second = store.repeat_claim(first["finding_id"], producer="PolicyAgent")
    assert second["validity_type"] == "INFERRED"
    assert second["derived_from"] == [first["finding_id"]]
    summary = corroboration([first, second])
    assert summary["finding_count"] == 2
    assert summary["independent_count"] == 1
    assert summary["evidence_root_ids"] == ["root-1"]
    with pytest.raises(ControlError) as verified:
        store.record_finding(
            validity_type="VERIFIED",
            evidence_root_ids=["root-1"],
            producer="PolicyAgent",
        )
    assert verified.value.code == "validity_upgrade_refused"
    with pytest.raises(ControlError) as committed:
        store.record_finding(
            validity_type="COMMITTED",
            evidence_root_ids=["root-1"],
            producer="PolicyAgent",
        )
    assert committed.value.code == "commit_requires_authority_plane"
    checked = store.verify_claim(first["finding_id"])
    assert checked["validity_type"] == "VERIFIED"
    assert checked["verification"] == "deterministic"
    assert store.get_finding(second["finding_id"])["validity_type"] == "INFERRED"


def test_committed_context_can_be_cited_and_does_not_become_current_truth(tmp_path):
    store = _store(tmp_path)
    workflow = _open(store)
    citation = store.cite_context(
        workflow["workflow_id"],
        workflow["workflow_version"],
        {"episode_id": "ep-p002-inactive", "validity_type": "COMMITTED"},
    )
    assert citation["validity_type"] == "COMMITTED"
    assert citation["authority"] is False
    assert citation["store_authority"] is False
    assert citation["provenance"] == "context_plane"
    assert citation["domain_mutated"] is False
    assert _reload(store, workflow["workflow_id"])["authority_snapshot_version"] == AUTHORITY
    assert _metric() == (42, 24)
