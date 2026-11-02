"""One promotion. Specialists propose. Only a current authority version commits."""

from __future__ import annotations

import asyncio

from careops.context.episode import project_episode
from careops.control.gate import DecisionRequest, FixtureDecisionGate
from careops.control.lineage import corroboration
from careops.control.specialists import observe_data_quality, observe_policy, observe_temporal_context
from careops.control.store import ControlStore
from careops.events.outbox import ChangeStore, VersionConflict
from careops.events.schema import build_event
from careops.governance import authorize

SUBJECT_REFS = ["provider:P002", "network:Aetna"]
ORGANIZATION = "tenant-demo"


def promote(
    *,
    control: ControlStore,
    changes: ChangeStore,
    role: str,
    tenant: str,
    purpose: str,
    promotion_key: str,
    expected_version: str = "0",
    gate: FixtureDecisionGate | None = None,
    max_delegation_depth: int = 2,
) -> dict:
    existing = changes.promotion_receipt(promotion_key)
    if existing is not None:
        return _with_episode(existing, idempotent=True)

    subject_key = "|".join(SUBJECT_REFS)
    workflow = control.open_workflow(
        authority_snapshot_version=f"{subject_key}@{expected_version}",
        max_delegation_depth=max_delegation_depth,
        organization_id=tenant,
    )
    workflow_id = workflow["workflow_id"]
    findings = []
    narration = ""

    data_task = _task(
        control,
        workflow_id,
        promotion_key=promotion_key,
        capability="data_quality",
        objective="observe the metric contract",
    )
    findings.append(_remember(control, observe_data_quality()["finding"]))
    _finish(control, workflow_id, data_task["task_id"])

    context = observe_temporal_context()
    narration = context["narration"]
    context_task = _task(
        control,
        workflow_id,
        promotion_key=promotion_key,
        capability="temporal_context",
        objective="recall preceding context for P002",
        evidence_refs=context["finding"]["evidence_root_ids"],
    )
    findings.append(_remember(control, context["finding"]))
    _finish(control, workflow_id, context_task["task_id"])

    request = context["delegation_request"]
    if request:
        try:
            policy_task = _task(
                control,
                workflow_id,
                promotion_key=promotion_key,
                capability=request["requested_capability"],
                objective="identify the applicable policy",
                parent_task_id=context_task["task_id"],
                delegation_depth=1,
                evidence_refs=["policy:current"],
            )
        except Exception as exc:
            code = getattr(exc, "code", "")
            if code == "delegation_depth_exceeded":
                return _stopped("delegation_depth_exceeded", findings, narration)
            raise
        findings.append(_remember(control, observe_policy(role=role)["finding"]))
        _finish(control, workflow_id, policy_task["task_id"])

    summary = corroboration(findings)
    proposal = _proposal(control, workflow_id, expected_version, promotion_key, summary["evidence_root_ids"])
    decision = asyncio.run(
        (gate or FixtureDecisionGate("COMPLETE")).decide(
            DecisionRequest(
                workflow_id=workflow_id,
                question="Is one network-status note warranted?",
                available_options=("COMPLETE", "VERIFY_MORE", "HUMAN_REVIEW", "REJECT"),
                evidence_refs=tuple(summary["evidence_root_ids"]),
                workflow_version=control.get_workflow(workflow_id)["workflow_version"],
            )
        )
    )
    if decision.selected_option != "COMPLETE" or decision.authoritative:
        return _stopped(decision.selected_option, findings, narration, summary, proposal["proposal_id"])

    access = authorize(role=role, tenant=tenant, purpose=purpose)
    if not access.allowed:
        return _stopped(access.reason, findings, narration, summary, proposal["proposal_id"])

    event = build_event(
        event_type="provider.network_status_changed",
        organization_id=ORGANIZATION,
        subject_refs=SUBJECT_REFS,
        payload={"provider_id": "P002", "payer_network": "Aetna", "to_status": "inactive"},
        occurred_at="2026-09-30T00:00:00+00:00",
        observed_at="2026-10-05T12:00:00+00:00",
        trace_id=f"trace-{promotion_key}",
        correlation_id=f"corr-{promotion_key}",
        causation_id=proposal["proposal_id"],
        event_id=f"evt-{promotion_key}",
    )
    try:
        receipt = changes.commit_if_current(event, expected_version=expected_version, promotion_key=promotion_key)
    except VersionConflict:
        current = changes.entity_version(ORGANIZATION, subject_key)
        _mark(control, proposal["proposal_id"], "COMMITTING", current)
        return _stopped("stale_version", findings, narration, summary, proposal["proposal_id"])

    _mark(control, proposal["proposal_id"], "COMMITTING", expected_version)
    _mark(control, proposal["proposal_id"], "COMMITTED", expected_version)
    body = _with_episode(receipt)
    body["findings"] = findings
    body["corroboration"] = summary
    body["narration"] = narration
    body["proposal_id"] = proposal["proposal_id"]
    body["proposal_status"] = "COMMITTED"
    body["workflow_id"] = workflow_id
    body["metric_mutated"] = False
    return body


def _task(control: ControlStore, workflow_id: str, *, promotion_key: str, capability: str, objective: str, **extra) -> dict:
    workflow = control.get_workflow(workflow_id)
    return control.create_task(
        workflow_id,
        expected_version=workflow["workflow_version"],
        idempotency_key=f"{promotion_key}:{capability}",
        requested_capability=capability,
        objective=objective,
        correlation_id=f"corr-{promotion_key}",
        causation_id=f"cause-{promotion_key}",
        **extra,
    )


def _finish(control: ControlStore, workflow_id: str, task_id: str) -> None:
    workflow = control.get_workflow(workflow_id)
    control.ingest_result(task_id, expected_version=workflow["workflow_version"], body={"domain_mutated": False})


def _remember(control: ControlStore, fields: dict) -> dict:
    return control.record_finding(**fields)


def _proposal(control: ControlStore, workflow_id: str, expected_version: str, promotion_key: str, evidence_refs: list[str]) -> dict:
    workflow = control.get_workflow(workflow_id)
    proposal = control.create_proposal(
        workflow_id,
        expected_version=workflow["workflow_version"],
        proposed_by="control-plane",
        target_entity="provider:P002",
        action="note_network_status",
        expected_entity_version=expected_version,
        evidence_refs=evidence_refs,
    )
    _mark(control, proposal["proposal_id"], "PENDING_REVIEW", None)
    return _mark(control, proposal["proposal_id"], "APPROVED", None)


def _mark(control: ControlStore, proposal_id: str, status: str, entity_version: str | None) -> dict:
    proposal = control.get_proposal(proposal_id)
    workflow = control.get_workflow(proposal["workflow_id"])
    return control.transition_proposal(
        proposal_id,
        status,
        expected_workflow_version=workflow["workflow_version"],
        current_entity_version=entity_version,
    )


def _with_episode(receipt: dict, *, idempotent: bool = False) -> dict:
    body = dict(receipt)
    body["idempotent"] = idempotent or bool(receipt.get("idempotent"))
    body["episode"] = project_episode(receipt["event"])
    body["metric_mutated"] = False
    return body


def _stopped(reason: str, findings: list[dict], narration: str, summary: dict | None = None, proposal_id: str = "") -> dict:
    return {
        "committed": False,
        "idempotent": False,
        "reason": reason,
        "event_id": None,
        "findings": findings,
        "corroboration": summary or corroboration(findings),
        "narration": narration,
        "proposal_id": proposal_id,
        "metric_mutated": False,
    }
