"""Protected query. Identity comes from the token. Retrieval happens only after allow."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from time import perf_counter

from careops.conformance import governed_view
from careops.context.query import contextualize, evidence_planes
from careops.freshness import FreshnessError, evaluate, not_applicable
from careops.gateway import answer as gateway_answer
from careops.governance import ContractError, apply_field_policy, authorize, validate_contracts
from careops.identity import IdentityConfig, IdentityError, config_from_env, verify_token
from careops.router import classify
from careops.semantic import metric_definition
from careops.trace import emit

METRIC = "available_appointments"
METRIC_TOOL = "tool:get_metric"
POLICY_TOOL = "tool:search_policy"


class DependencyTimeout(Exception):
    pass


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


def _timeout_seconds(override: float | None) -> float:
    if override is not None:
        return float(override)
    raw = os.environ.get("CAREOPS_DEPENDENCY_TIMEOUT_SECONDS", "2")
    try:
        return float(raw)
    except ValueError:
        return 2.0


def _run_bounded(fn, timeout_s: float):
    """Wait at most timeout_s. The executor is not joined, so a slow call cannot extend the wait."""
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(fn)
    try:
        return future.result(timeout=timeout_s)
    except TimeoutError as exc:
        raise DependencyTimeout() from exc
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def protected_query(
    token: str,
    question: str,
    *,
    config: IdentityConfig | None = None,
    untrusted_role: str | None = None,
    untrusted_tenant: str | None = None,
    timeout_seconds: float | None = None,
    trace_id: str | None = None,
) -> dict:
    """Answer a protected question.

    `untrusted_role` and `untrusted_tenant` are accepted only so tests can prove
    the body cannot override the token. They are not read.
    """
    del untrusted_role, untrusted_tenant
    started = perf_counter()
    identity = None
    include_identity = False
    policy_version = None
    rule_id = None
    route = ""
    freshness = None
    tools: list[str] = []
    source_ids: list[str] = []
    retrieval_attempted = False

    def finish(
        *,
        http_status: int,
        outcome: str,
        reason_code: str,
        evidence,
        answer,
        extra: dict | None = None,
        as_of: str | None = None,
        provenance: list[str] | None = None,
        temporal_status: str = "not_requested",
    ):
        contract_id = None
        contract_version = None
        try:
            contract = metric_definition(METRIC)
            contract_id = contract["metric_id"]
            contract_version = contract["contract_version"]
        except (KeyError, TypeError, ValueError):
            pass
        envelope = emit(
            {
                "trace_id": trace_id,
                "principal_ref": identity.principal if identity else "",
                "organization_ref": identity.tenant if identity else "",
                "route": route,
                "policy_version": policy_version,
                "matched_rule_ids": [rule_id] if rule_id else [],
                "metric_contract": contract_id,
                "metric_contract_version": contract_version,
                "source_ids": list(source_ids),
                "freshness": freshness.as_dict() if freshness is not None else {"state": "not_evaluated", "freshness": "not_evaluated"},
                "tools_invoked": list(tools),
                "model_called": False,
                "latency_ms": int((perf_counter() - started) * 1000),
                "outcome": outcome,
                "reason_code": reason_code,
                "evidence_planes": evidence_planes(
                    evidence if isinstance(evidence, list) else None,
                    temporal_status,
                ),
            }
        )
        body = {
            "http_status": http_status,
            "outcome": outcome,
            "reason_code": reason_code,
            "model_called": False,
            "retrieval_attempted": retrieval_attempted,
            "evidence": evidence,
            "answer": answer,
            "decision": envelope,
        }
        if include_identity and identity is not None:
            body["identity"] = {
                "principal": identity.principal,
                "role": identity.role,
                "tenant": identity.tenant,
                "purpose": identity.purpose,
                "scopes": list(identity.scopes),
            }
        if extra:
            body.update(extra)
        label = freshness.state if freshness is not None else "not_evaluated"
        body["governed"] = governed_view(
            outcome=outcome,
            reason_code=reason_code,
            evidence=evidence,
            as_of=as_of,
            provenance=provenance,
            freshness=label,
        )
        return body

    try:
        active = config if config is not None else config_from_env()
        identity = verify_token(token, active)
    except IdentityError as exc:
        status = 503 if exc.code == "not_configured" else 401
        return finish(http_status=status, outcome="deny", reason_code=exc.code, evidence=None, answer=None)
    try:
        validate_contracts()
    except ContractError as exc:
        return finish(http_status=500, outcome="deny", reason_code=exc.code, evidence=None, answer=None)

    decision = authorize(role=identity.role, tenant=identity.tenant, purpose=identity.purpose)
    include_identity = True
    policy_version = decision.policy_version
    rule_id = decision.rule_id
    if not decision.allowed:
        return finish(
            http_status=403,
            outcome="deny",
            reason_code=decision.reason,
            evidence=None,
            answer=None,
            extra={"policy_version": decision.policy_version},
        )

    route = classify(question)
    uses_metric = route in {"metric", "mixed"}
    contract = metric_definition(METRIC)
    if uses_metric:
        try:
            freshness = evaluate(contract["source"])
        except FreshnessError as exc:
            return finish(
                http_status=503,
                outcome="dependency_failure",
                reason_code=exc.code,
                evidence=None,
                answer=None,
            )
        source_ids = [freshness.source_id]
        if not freshness.fresh:
            return finish(
                http_status=200,
                outcome="stale_evidence",
                reason_code="stale_evidence",
                evidence=None,
                answer=None,
                extra={
                    "policy_version": decision.policy_version,
                    "matched_rule_id": decision.rule_id,
                    "metric_contract": contract["metric_id"],
                    "metric_contract_version": contract["contract_version"],
                    "route": route,
                },
                as_of=freshness.evaluated_at[:10] if freshness.evaluated_at else None,
                provenance=list(source_ids),
            )
    else:
        freshness = not_applicable(contract["source"], contract["metric_id"])

    if uses_metric:
        tools.append(METRIC_TOOL)
    if route in {"knowledge", "mixed"}:
        tools.append(POLICY_TOOL)

    def _retrieve():
        return gateway_answer(question, role=identity.role)

    try:
        raw = _run_bounded(_retrieve, _timeout_seconds(timeout_seconds))
    except DependencyTimeout:
        retrieval_attempted = True
        if not source_ids:
            source_ids = [contract["source"]]
        return finish(
            http_status=504,
            outcome="dependency_failure",
            reason_code="dependency_timeout",
            evidence=None,
            answer=None,
            extra={"dependency": contract["source"]},
        )

    retrieval_attempted = True
    route = raw.route
    if raw.provenance:
        source_ids = list(raw.provenance)
    evidence = apply_field_policy(raw.evidence, identity.role)
    outcome = "allow" if raw.status == "ok" else "abstain"
    temporal_status = "not_requested"
    note = None
    if outcome == "allow":
        evidence, temporal_status, note = contextualize(
            evidence,
            organization_id=identity.tenant,
            as_of=raw.as_of,
        )
    answer = _render(evidence, raw.answer)
    if note:
        answer = f"{answer} {note}"
    return finish(
        http_status=200,
        outcome=outcome,
        reason_code=raw.status,
        evidence=evidence,
        answer=answer,
        temporal_status=temporal_status,
        extra={
            "policy_version": decision.policy_version,
            "matched_rule_id": decision.rule_id,
            "metric_contract": contract["metric_id"],
            "metric_contract_version": contract["contract_version"],
            "route": raw.route,
        },
        as_of=raw.as_of,
        provenance=list(raw.provenance),
    )
