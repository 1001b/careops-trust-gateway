"""Domain-event envelope. The payload points at a contract. It does not redefine it."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

ENVELOPE_FIELDS = (
    "event_id",
    "event_type",
    "event_version",
    "organization_id",
    "subject_refs",
    "occurred_at",
    "observed_at",
    "producer",
    "correlation_id",
    "causation_id",
    "trace_id",
    "classification",
    "schema_version",
    "payload",
)

SCHEMA_VERSION = 1
PRODUCER = "careops.events"

# event_version -> required payload fields. Extra payload fields are additive.
EVENT_SCHEMAS = {
    "provider.network_status_changed": {1: ("provider_id", "payer_network", "to_status")},
    "policy.version_activated": {1: ("policy_id", "policy_version")},
    "data_product.freshness_breached": {1: ("data_product", "source_id")},
    "contract.version_published": {1: ("metric_id", "contract_version")},
}


class SchemaError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def build_event(
    *,
    event_type: str,
    organization_id: str,
    subject_refs: list[str],
    payload: dict,
    occurred_at: str | None = None,
    observed_at: str | None = None,
    trace_id: str | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    event_version: int = 1,
    classification: str = "internal",
    event_id: str | None = None,
) -> dict:
    observed = observed_at or _now()
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "event_type": event_type,
        "event_version": event_version,
        "organization_id": organization_id,
        "subject_refs": list(subject_refs),
        "occurred_at": occurred_at or observed,
        "observed_at": observed,
        "producer": PRODUCER,
        "correlation_id": correlation_id or str(uuid.uuid4()),
        "causation_id": causation_id or "",
        "trace_id": trace_id or str(uuid.uuid4()),
        "classification": classification,
        "schema_version": SCHEMA_VERSION,
        "payload": dict(payload),
    }


def validate_event(event: dict) -> None:
    missing = [field for field in ENVELOPE_FIELDS if field not in event]
    if missing:
        raise SchemaError("envelope_incomplete")
    if event["schema_version"] != SCHEMA_VERSION:
        raise SchemaError("schema_version_unsupported")
    versions = EVENT_SCHEMAS.get(event["event_type"])
    if versions is None:
        raise SchemaError("event_type_unsupported")
    required = versions.get(event["event_version"])
    if required is None:
        raise SchemaError("event_version_unsupported")
    payload = event["payload"]
    if not isinstance(payload, dict):
        raise SchemaError("payload_invalid")
    absent = [field for field in required if field not in payload]
    if absent:
        raise SchemaError("payload_incomplete")
    if not event["organization_id"] or not event["subject_refs"]:
        raise SchemaError("subject_incomplete")
