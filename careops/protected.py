"""Protected query. Identity comes from the token. Retrieval happens only after allow."""

from __future__ import annotations

from careops.gateway import answer as gateway_answer
from careops.governance import ContractError, apply_field_policy, authorize, validate_contracts
from careops.identity import IdentityConfig, IdentityError, config_from_env, verify_token
from careops.semantic import metric_definition


def _render(evidence: list[dict], fallback: str) -> str:
    metric = next((item for item in evidence if item.get("type") == "metric"), None)
    if metric is None:
        return fallback
    comparison = metric["comparison"]
    current = comparison["current"]
    prior = comparison["prior"]
    state = current["filters"]["state"]
    network = current["filters"]["payer_network"]
    contributors = [
        f"{row.get('provider_name', '')} {row['delta']} slots"
        for row in metric.get("contributors", [])
        if row.get("delta", 0) < 0
    ]
    contributor_text = "; ".join(contributors) or "no provider-level negative contributors were found"
    return (
        f"{state} {network} bookable appointment capacity declined from {prior['value']} slots to "
        f"{current['value']} last week ({comparison['delta']} slots, {comparison['pct_change']}%). "
        f"The provider-level contributors were: {contributor_text}."
    )


def protected_query(
    token: str,
    question: str,
    *,
    config: IdentityConfig | None = None,
    untrusted_role: str | None = None,
    untrusted_tenant: str | None = None,
) -> dict:
    """Answer a protected question.

    `untrusted_role` and `untrusted_tenant` are accepted only so tests can prove
    the body cannot override the token. They are not read.
    """
    del untrusted_role, untrusted_tenant
    try:
        active = config if config is not None else config_from_env()
        identity = verify_token(token, active)
    except IdentityError as exc:
        status = 503 if exc.code == "not_configured" else 401
        return {
            "http_status": status,
            "outcome": "deny",
            "reason_code": exc.code,
            "model_called": False,
            "retrieval_attempted": False,
            "evidence": None,
            "answer": None,
        }
    try:
        validate_contracts()
    except ContractError as exc:
        return {
            "http_status": 500,
            "outcome": "deny",
            "reason_code": exc.code,
            "model_called": False,
            "retrieval_attempted": False,
            "evidence": None,
            "answer": None,
        }
    decision = authorize(role=identity.role, tenant=identity.tenant, purpose=identity.purpose)
    if not decision.allowed:
        return {
            "http_status": 403,
            "outcome": "deny",
            "reason_code": decision.reason,
            "policy_version": decision.policy_version,
            "model_called": False,
            "retrieval_attempted": False,
            "evidence": None,
            "answer": None,
            "identity": {
                "principal": identity.principal,
                "role": identity.role,
                "tenant": identity.tenant,
                "purpose": identity.purpose,
                "scopes": list(identity.scopes),
            },
        }
    raw = gateway_answer(question, role=identity.role)
    evidence = apply_field_policy(raw.evidence, identity.role)
    contract = metric_definition("available_appointments")
    outcome = "allow" if raw.status == "ok" else "abstain"
    return {
        "http_status": 200,
        "outcome": outcome,
        "reason_code": raw.status,
        "policy_version": decision.policy_version,
        "matched_rule_id": decision.rule_id,
        "metric_contract": contract["metric_id"],
        "metric_contract_version": contract["contract_version"],
        "model_called": False,
        "retrieval_attempted": True,
        "identity": {
            "principal": identity.principal,
            "role": identity.role,
            "tenant": identity.tenant,
            "purpose": identity.purpose,
            "scopes": list(identity.scopes),
        },
        "evidence": evidence,
        "answer": _render(evidence, raw.answer),
        "route": raw.route,
    }
