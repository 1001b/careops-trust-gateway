"""One comparable view of a governed answer. Adapters fill it from core results."""

from __future__ import annotations

from careops.freshness import evaluate
from careops.semantic import metric_definition

RESPONSE_FIELDS = (
    "metric_id",
    "contract_version",
    "value",
    "as_of",
    "period",
    "provenance",
    "freshness",
    "outcome",
    "reason_code",
)


def _metric_parts(evidence: list[dict] | None):
    if not evidence:
        return None, None, None, None
    metric = next((item for item in evidence if item.get("type") == "metric"), None)
    if metric is None:
        return None, None, None, None
    current = metric["comparison"]["current"]
    prior = metric["comparison"]["prior"]
    value = {"prior": prior["value"], "current": current["value"]}
    period = {
        "prior": {"start": prior["period_start"], "end": prior["period_end"]},
        "current": {"start": current["period_start"], "end": current["period_end"]},
    }
    return current["metric"], current["contract_version"], value, period


def governed_view(
    *,
    outcome: str,
    reason_code: str,
    evidence: list[dict] | None,
    as_of: str | None,
    provenance: list[str] | None,
    freshness: str,
) -> dict:
    metric_id, version, value, period = _metric_parts(evidence)
    return {
        "metric_id": metric_id,
        "contract_version": version,
        "value": value,
        "as_of": as_of,
        "period": period,
        "provenance": sorted(provenance or []),
        "freshness": freshness,
        "outcome": outcome,
        "reason_code": reason_code,
    }


def project_gateway_payload(payload: dict) -> dict:
    status = payload["status"]
    if status == "ok":
        outcome = "allow"
    elif status == "insufficient_evidence":
        outcome = "abstain"
    else:
        outcome = status
    evidence = payload.get("evidence") or []
    metric_id, _, _, _ = _metric_parts(evidence)
    if metric_id is None:
        freshness = "not_applicable"
    else:
        freshness = evaluate(metric_definition(metric_id)["source"]).state
    return governed_view(
        outcome=outcome,
        reason_code=status,
        evidence=evidence,
        as_of=payload.get("as_of"),
        provenance=payload.get("provenance") or [],
        freshness=freshness,
    )
