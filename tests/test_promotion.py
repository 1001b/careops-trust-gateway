"""TC-09: one controlled delegation and one authority commit."""

from __future__ import annotations

import inspect
import json

import pytest

from careops.control.gate import FixtureDecisionGate
from careops.control.lineage import corroboration
from careops.control.promotion import promote
from careops.control.specialists import observe_data_quality, observe_policy, observe_temporal_context
from careops.control.store import ControlStore
from careops.events.outbox import ChangeStore
from careops.metrics import get_metric


def _pair(tmp_path):
    return ControlStore(tmp_path / "control.db"), ChangeStore(tmp_path / "changes.db")


def _run(tmp_path, **overrides):
    control, changes = _pair(tmp_path)
    fields = {
        "control": control,
        "changes": changes,
        "role": "analyst",
        "tenant": "tenant-demo",
        "purpose": "operations_analysis",
        "promotion_key": "promo-1",
    }
    fields.update(overrides)
    return control, changes, promote(**fields)


def _metric():
    prior = get_metric("available_appointments", state="TX", payer_network="Aetna", period="prior_week")
    current = get_metric("available_appointments", state="TX", payer_network="Aetna", period="last_week")
    return prior.value, current.value


def test_specialists_do_not_call_each_other_or_the_outbox():
    temporal = inspect.getsource(observe_temporal_context)
    policy = inspect.getsource(observe_policy)
    quality = inspect.getsource(observe_data_quality)
    assert "observe_policy" not in temporal
    assert "observe_temporal_context" not in policy
    assert "observe_data_quality" not in temporal
    assert "ChangeStore" not in temporal + policy + quality
    assert "commit_if_current" not in temporal + policy + quality
    protected = inspect.getsource(__import__("careops.protected", fromlist=["protected_query"]))
    assert "promote" not in protected


def test_promotion_commits_once_and_leaves_the_metric_unchanged(tmp_path):
    control, changes, result = _run(tmp_path)
    assert result["committed"] is True
    assert result["idempotent"] is False
    assert result["proposal_status"] == "COMMITTED"
    assert result["metric_mutated"] is False
    assert result["version"] == "1"
    assert result["corroboration"]["independent_count"] == 3
    assert result["episode"]["validity_type"] == "COMMITTED"
    assert result["episode"]["provenance"]["store_authority"] is False
    assert result["episode"]["reference_time"].startswith("2026-09-30")
    assert result["episode"]["observed_at"].startswith("2026-10-05")
    assert "preceded" in result["narration"]
    assert "caused" not in result["narration"].lower()
    inferred = next(row for row in result["findings"] if row["producer"] == "TemporalContextAgent")
    assert inferred["validity_type"] == "INFERRED"
    repeated = control.repeat_claim(inferred["finding_id"], producer="PolicyAgent")
    assert repeated["validity_type"] == "INFERRED"
    assert corroboration([inferred, repeated])["independent_count"] == 1
    payload = json.dumps(result["event"]["payload"])
    assert "inactive" in payload
    assert "42" not in payload
    assert "24" not in payload
    assert "is_bookable" not in payload
    assert changes.counts() == {"changes": 1, "outbox": 1}
    assert changes.entity_version("tenant-demo", "provider:P002|network:Aetna") == "1"
    proposal = control.get_proposal(result["proposal_id"])
    assert proposal["status"] == "COMMITTED"
    assert proposal["domain_mutated"] is False
    assert proposal["expected_entity_version"] == "0"
    child = control.get_workflow(result["workflow_id"])
    assert child["delegation_count"] == 1
    policy_task = [
        control.get_task(task_id)
        for task_id in child["completed_tasks"]
        if control.get_task(task_id)["requested_capability"] == "policy_interpretation"
    ][0]
    assert policy_task["assigned_agent"] == "PolicyAgent"
    assert policy_task["parent_task_id"]
    assert policy_task["delegation_depth"] == 1
    assert _metric() == (42, 24)

    again = promote(
        control=control,
        changes=changes,
        role="analyst",
        tenant="tenant-demo",
        purpose="operations_analysis",
        promotion_key="promo-1",
    )
    assert again["idempotent"] is True
    assert again["event_id"] == result["event_id"]
    assert again["episode"]["provenance"]["store_authority"] is False
    assert changes.counts() == {"changes": 1, "outbox": 1}
    assert _metric() == (42, 24)


def test_a_stale_second_workflow_does_not_commit(tmp_path):
    _control, changes, first = _run(tmp_path)
    control = ControlStore(tmp_path / "control.db")
    stale = promote(
        control=control,
        changes=changes,
        role="analyst",
        tenant="tenant-demo",
        purpose="operations_analysis",
        promotion_key="promo-2",
        expected_version="0",
    )
    assert first["committed"] is True
    assert stale["committed"] is False
    assert stale["reason"] == "stale_version"
    assert stale["event_id"] is None
    assert control.get_proposal(stale["proposal_id"])["status"] == "STALE"
    assert changes.counts()["changes"] == 1
    assert changes.entity_version("tenant-demo", "provider:P002|network:Aetna") == "1"
    assert _metric() == (42, 24)


def test_decision_and_authorization_can_each_stop_the_commit(tmp_path):
    _control, changes, rejected = _run(tmp_path, gate=FixtureDecisionGate("REJECT"), promotion_key="promo-reject")
    assert rejected["committed"] is False
    assert rejected["reason"] == "REJECT"
    assert changes.counts()["changes"] == 0
    control, changes, denied = _run(
        tmp_path,
        role="guest",
        promotion_key="promo-guest",
    )
    assert denied["committed"] is False
    assert denied["reason"] == "unauthorized_role"
    assert changes.entity_version("tenant-demo", "provider:P002|network:Aetna") == "0"
    assert _metric() == (42, 24)


def test_delegation_above_the_limit_does_not_reach_policy_or_commit(tmp_path):
    _control, changes, result = _run(tmp_path, max_delegation_depth=0, promotion_key="promo-depth")
    assert result["committed"] is False
    assert result["reason"] == "delegation_depth_exceeded"
    assert changes.counts()["changes"] == 0
    assert not any(row["producer"] == "PolicyAgent" for row in result["findings"])
    assert _metric() == (42, 24)
