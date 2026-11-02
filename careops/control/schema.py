"""Control-plane vocabulary. These statuses are workflow state, not domain truth."""

from __future__ import annotations

WORKFLOW_STATUSES = frozenset({
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
})

OPEN_STATUSES = frozenset({"RUNNING", "WAITING"})

TERMINAL_STATUSES = WORKFLOW_STATUSES - OPEN_STATUSES

PROPOSAL_STATUSES = frozenset({
    "DRAFT",
    "PENDING_REVIEW",
    "APPROVED",
    "REJECTED",
    "COMMITTING",
    "COMMITTED",
    "STALE",
    "FAILED",
})

PROPOSAL_TRANSITIONS = {
    "DRAFT": frozenset({"PENDING_REVIEW"}),
    "PENDING_REVIEW": frozenset({"APPROVED", "REJECTED"}),
    "APPROVED": frozenset({"COMMITTING"}),
    "REJECTED": frozenset(),
    "COMMITTING": frozenset({"COMMITTED", "STALE", "FAILED"}),
    "COMMITTED": frozenset(),
    "STALE": frozenset(),
    "FAILED": frozenset(),
}

DECISION_OPTIONS = frozenset({"COMPLETE", "VERIFY_MORE", "HUMAN_REVIEW", "REJECT"})

CAPABILITIES = {
    "data_quality": {
        "assigned_agent": "DataQualityAgent",
        "allowed_tools": ["tool:get_metric"],
    },
    "policy_interpretation": {
        "assigned_agent": "PolicyAgent",
        "allowed_tools": ["tool:search_policy"],
    },
    "temporal_context": {
        "assigned_agent": "TemporalContextAgent",
        "allowed_tools": [],
    },
}
