"""One decision envelope for a governed request. Logs carry the envelope and nothing else."""

from __future__ import annotations

import json
import logging
import uuid

LOG = logging.getLogger("careops.decision")

FIELDS = (
    "trace_id",
    "principal_ref",
    "organization_ref",
    "route",
    "policy_version",
    "matched_rule_ids",
    "metric_contract",
    "metric_contract_version",
    "source_ids",
    "freshness",
    "tools_invoked",
    "model_called",
    "latency_ms",
    "outcome",
    "reason_code",
)

# Defense in depth. The envelope is not supposed to carry these.
_DROPPED = {
    "answer",
    "authorization",
    "credential_notes",
    "evidence",
    "provider_name",
    "secret",
    "token",
}


def emit(envelope: dict) -> dict:
    trace_id = envelope.get("trace_id") or str(uuid.uuid4())
    body = {field: envelope.get(field) for field in FIELDS}
    body["trace_id"] = trace_id
    safe = {key: value for key, value in body.items() if key not in _DROPPED}
    LOG.info("%s", json.dumps(safe, sort_keys=True, default=str))
    return body
