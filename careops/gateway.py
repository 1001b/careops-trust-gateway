from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date
import re

from .router import classify
from .metrics import compare_availability, provider_contributors, get_metric
from .policy import search_policy


@dataclass
class GatewayResponse:
    status: str
    route: str
    answer: str
    evidence: list[dict]
    provenance: list[str]
    as_of: str

    def as_dict(self):
        return asdict(self)


def _extract_state(question: str) -> str:
    q = question.lower()
    if "texas" in q or re.search(r"\btx\b", q):
        return "TX"
    if "california" in q or re.search(r"\bca\b", q):
        return "CA"
    return "TX"  # demo default, intentionally explicit in output


def _extract_network(question: str) -> str:
    # Demo uses a single payer network. In production this would resolve through a governed entity registry.
    return "Aetna"


def answer(question: str, *, role: str, as_of: date = date(2026, 10, 5)) -> GatewayResponse:
    route = classify(question)
    evidence: list[dict] = []
    provenance: list[str] = []
    state = _extract_state(question)
    network = _extract_network(question)

    if route in {"metric", "mixed"}:
        comparison = compare_availability(state=state, payer_network=network)
        contributors = provider_contributors(state=state, payer_network=network)
        evidence.append({"type": "metric", "comparison": comparison, "contributors": contributors})
        provenance.append("gold_provider_availability_daily")

    if route in {"knowledge", "mixed"}:
        docs = search_policy(question, role=role, as_of=as_of)
        if docs:
            evidence.append({"type": "policy", "documents": docs})
            provenance.extend(d["source"] for d in docs)

    if not evidence:
        return GatewayResponse(
            status="insufficient_evidence",
            route=route,
            answer="I do not have enough governed evidence to answer this request.",
            evidence=[],
            provenance=[],
            as_of=as_of.isoformat(),
        )

    # Sufficiency is query-specific: generic authorized policy evidence must not be used
    # to answer a restricted credentialing question. The exact credentialing policy
    # must have survived authorization + effective-date filtering.
    if "credentialing" in question.lower():
        policy_docs = next((e["documents"] for e in evidence if e["type"] == "policy"), [])
        has_credentialing_authority = any(d["id"] == "credentialing-escalation" for d in policy_docs)
        if not has_credentialing_authority:
            return GatewayResponse(
                status="insufficient_evidence",
                route=route,
                answer="No authorized policy evidence is available for this role.",
                evidence=[],
                provenance=[],
                as_of=as_of.isoformat(),
            )

    if route == "metric":
        cmp = evidence[0]["comparison"]
        answer_text = (
            f"{state} {network} bookable appointment capacity was {cmp['current']['value']} slots last week "
            f"versus {cmp['prior']['value']} in the prior week ({cmp['delta']} slots, {cmp['pct_change']}%). "
            "The value comes from the governed metric tool, not model arithmetic."
        )
    elif route == "knowledge":
        docs = next((e["documents"] for e in evidence if e["type"] == "policy"), [])
        if not docs:
            return GatewayResponse(
                status="insufficient_evidence", route=route,
                answer="No authorized current policy evidence was found.", evidence=[], provenance=[], as_of=as_of.isoformat()
            )
        answer_text = f"Current governed policy evidence: {docs[0]['content'].splitlines()[-1]}"
    else:
        metric_ev = next(e for e in evidence if e["type"] == "metric")
        cmp = metric_ev["comparison"]
        negative = [c for c in metric_ev["contributors"] if c["delta"] < 0]
        contributor_text = "; ".join(
            f"{c['provider_name']} {c['delta']} slots" for c in negative
        ) or "no provider-level negative contributors were found"
        docs = next((e["documents"] for e in evidence if e["type"] == "policy"), [])
        policy_note = docs[0]["content"].splitlines()[-1] if docs else "No policy context retrieved."
        answer_text = (
            f"{state} {network} bookable appointment capacity declined from {cmp['prior']['value']} slots to "
            f"{cmp['current']['value']} last week ({cmp['delta']} slots, {cmp['pct_change']}%). "
            f"The provider-level contributors were: {contributor_text}. "
            f"The current policy context says: {policy_note}"
        )

    return GatewayResponse(
        status="ok",
        route=route,
        answer=answer_text,
        evidence=evidence,
        provenance=sorted(set(provenance)),
        as_of=as_of.isoformat(),
    )
