from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date
import json
import os
import re

from careops.metrics import compare_availability, get_metric
from careops.rag.corpus import chunk_to_dict, load_chunks
from careops.rag.embed import Embedder, get_embedder
from careops.rag.filters import filter_chunks
from careops.rag.retrieve import hybrid_retrieve
from careops.router import classify

METRIC_HINT = re.compile(
    r"how many|availability|decline|increase|decrease|last week|prior week|slots",
    re.I,
)


def _requires_credentialing_authority(question: str) -> bool:
    """True only when the ask needs the restricted credentialing procedure.

    Mentions of 'credentialing' inside broader eligibility/availability questions
    must not wipe otherwise-authorized evidence.
    """
    q = question.lower()
    if "credentialing" not in q:
        return False
    if any(
        term in q
        for term in (
            "availability",
            "eligible",
            "eligibility",
            "network policy",
            "decline",
            "how many",
            "slots",
        )
    ):
        return False
    return any(
        term in q
        for term in (
            "credentialing escalation",
            "credentialing procedure",
            "what is the credentialing",
        )
    )


@dataclass
class EvidenceBundle:
    mode: str
    role: str | None
    route: str
    chunks: list[dict]
    metric: dict | None
    provenance: list[str]
    status: str
    notes: list[str]

    def as_dict(self):
        return asdict(self)


def _default_as_of() -> date:
    return date(2026, 10, 5)


def retrieve_evidence(
    question: str,
    *,
    mode: str,
    role: str = "analyst",
    as_of: date | None = None,
    embedder: Embedder | None = None,
    limit: int = 5,
) -> EvidenceBundle:
    """Build an evidence bundle. Authz/time filters apply only in governed mode."""
    if mode not in {"naive", "governed"}:
        raise ValueError("mode must be 'naive' or 'governed'")
    as_of = as_of or _default_as_of()
    embedder = embedder or get_embedder()
    route = classify(question)
    notes: list[str] = []
    all_chunks = load_chunks()

    if mode == "governed":
        pool = filter_chunks(all_chunks, role=role, as_of=as_of)
        notes.append("authz_and_effective_date_applied_before_retrieval")
        role_out: str | None = role
    else:
        pool = all_chunks
        role_out = None
        notes.append("no_authz_or_effective_date_filtering")

    hits = hybrid_retrieve(question, pool, embedder, limit=limit)
    chunk_dicts = []
    provenance: list[str] = []
    for hit in hits:
        d = chunk_to_dict(hit.chunk)
        d.update(
            {
                "dense_score": hit.dense_score,
                "lexical_score": hit.lexical_score,
                "rrf_score": hit.rrf_score,
                "rank": hit.rank,
            }
        )
        chunk_dicts.append(d)
        provenance.append(hit.chunk.path)

    metric = None
    wants_metric = route in {"metric", "mixed"} or bool(METRIC_HINT.search(question))
    if mode == "governed" and wants_metric:
        cmp = compare_availability(state="TX", payer_network="Aetna")
        metric = {
            "tool": "get_metric",
            "comparison": cmp,
            "authority": "tool:get_metric",
            "source": "gold_provider_availability_daily",
        }
        provenance.append("gold_provider_availability_daily")
        notes.append("quantitative_facts_from_governed_metric_tool")
    elif mode == "naive" and wants_metric:
        notes.append("naive_path_has_no_metric_tool_model_must_not_invent_enterprise_kpis")

    status = "ok" if chunk_dicts or metric else "insufficient_evidence"
    if mode == "governed" and _requires_credentialing_authority(question):
        if not any(c["doc_id"] == "credentialing-escalation" for c in chunk_dicts):
            status = "insufficient_evidence"
            chunk_dicts = []
            metric = None
            provenance = []
            notes.append("restricted_policy_unavailable_for_role")

    return EvidenceBundle(
        mode=mode,
        role=role_out,
        route=route,
        chunks=chunk_dicts,
        metric=metric,
        provenance=sorted(set(provenance)),
        status=status,
        notes=notes,
    )


def synthesize_with_model(question: str, evidence: EvidenceBundle) -> dict:
    """Optional GPT synthesis via OpenAI Responses API. Requires OPENAI_API_KEY."""
    if not os.environ.get("OPENAI_API_KEY"):
        return {
            "status": "model_unavailable",
            "answer": None,
            "reason": "OPENAI_API_KEY not set",
            "evidence": evidence.as_dict(),
        }
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise SystemExit("Install RAG extras: pip install -e '.[rag]'") from exc

    model = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
    client = OpenAI()

    system = (
        "You are an operations analyst. You may reason over the provided evidence bundle only. "
        "You must not invent authoritative metrics, eligibility statuses, or policy text. "
        "If evidence.status is insufficient_evidence, or required facts are missing, say so explicitly. "
        "Cite sources by path/doc_id. Quantitative values may only come from evidence.metric "
        "or successful get_metric tool results."
    )
    user = (
        f"Question: {question}\n\n"
        f"Mode: {evidence.mode}\n"
        f"Evidence JSON:\n{json.dumps(evidence.as_dict())}"
    )

    tools = []
    if evidence.mode == "governed":
        tools = [
            {
                "type": "function",
                "name": "get_metric",
                "description": "Return an authoritative governed metric. Demo supports available_appointments.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "metric": {"type": "string"},
                        "state": {"type": "string"},
                        "payer_network": {"type": "string"},
                        "period": {"type": "string", "enum": ["last_week", "prior_week"]},
                    },
                    "required": ["metric", "state", "payer_network", "period"],
                    "additionalProperties": False,
                },
            }
        ]

    input_messages: list[dict] = [
        {"role": "developer", "content": system},
        {"role": "user", "content": user},
    ]
    max_out = int(os.environ.get("CAREOPS_MAX_OUTPUT_TOKENS", "600"))

    for _ in range(3):
        kwargs: dict = {
            "model": model,
            "input": input_messages,
            "max_output_tokens": max_out,
        }
        if tools:
            kwargs["tools"] = tools
        try:
            response = client.responses.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "model_unavailable",
                "answer": None,
                "reason": f"OpenAI Responses API failed: {exc}",
                "evidence": evidence.as_dict(),
            }

        tool_calls = []
        text_bits = []
        for item in response.output:
            item_type = getattr(item, "type", None)
            if item_type == "function_call":
                tool_calls.append(item)
            elif item_type == "message":
                for part in getattr(item, "content", []) or []:
                    if getattr(part, "type", None) == "output_text":
                        text_bits.append(part.text)

        if not tool_calls:
            answer_text = getattr(response, "output_text", None) or "\n".join(text_bits)
            return {
                "status": "ok" if evidence.status == "ok" else evidence.status,
                "answer": answer_text,
                "model": model,
                "evidence": evidence.as_dict(),
            }

        for call in tool_calls:
            input_messages.append(
                {
                    "type": "function_call",
                    "call_id": call.call_id,
                    "name": call.name,
                    "arguments": call.arguments,
                }
            )
            if call.name == "get_metric":
                args = json.loads(call.arguments)
                result = get_metric(
                    args.get("metric", "available_appointments"),
                    state=args.get("state", "TX"),
                    payer_network=args.get("payer_network", "Aetna"),
                    period=args.get("period", "last_week"),
                ).as_dict()
                input_messages.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": json.dumps(result),
                    }
                )
            else:
                input_messages.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": '{"error":"tool_not_allowed"}',
                    }
                )

    return {
        "status": "ok",
        "answer": "Tool loop exceeded bound without final answer.",
        "model": model,
        "evidence": evidence.as_dict(),
    }


def answer(
    question: str,
    *,
    mode: str,
    role: str = "analyst",
    synthesize: bool | None = None,
    embedder: Embedder | None = None,
) -> dict:
    try:
        evidence = retrieve_evidence(question, mode=mode, role=role, embedder=embedder)
    except RuntimeError as exc:
        return {
            "status": "model_unavailable",
            "answer": None,
            "reason": str(exc),
            "evidence": None,
        }
    use_model = synthesize if synthesize is not None else bool(os.environ.get("OPENAI_API_KEY"))
    if not use_model:
        if evidence.status == "insufficient_evidence":
            text = "Insufficient governed evidence for this request."
        elif evidence.metric:
            cmp = evidence.metric["comparison"]
            text = (
                f"Governed metric: {cmp['prior']['value']} → {cmp['current']['value']} "
                f"({cmp['delta']}, {cmp['pct_change']}%). "
                f"Policy sources: {', '.join(c['doc_id'] for c in evidence.chunks)}."
            )
        else:
            ids = [c["doc_id"] for c in evidence.chunks]
            text = f"Retrieved documents: {', '.join(ids) if ids else '(none)'}."
        return {
            "status": evidence.status,
            "answer": text,
            "model": None,
            "evidence": evidence.as_dict(),
        }
    return synthesize_with_model(question, evidence)
