from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date
import re

from careops.agent.llm import llm_configured, synthesize_with_model as _synthesize_with_model
from careops.metrics import compare_availability, provider_contributors
from careops.rag.corpus import Chunk, chunk_to_dict, load_chunks
from careops.rag.embed import Embedder, get_embedder
from careops.rag.filters import filter_chunks
from careops.rag.retrieve import hybrid_retrieve
from careops.router import classify
from careops.storage import using_postgres

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
    doc_vecs: list[list[float]] | None = None
    if using_postgres():
        from careops.storage.postgres import load_chunks_with_embeddings

        all_chunks, all_vecs = load_chunks_with_embeddings()
        notes.append("chunks_and_embeddings_loaded_from_postgres_pgvector")
    else:
        all_chunks = load_chunks()
        all_vecs = None

    if mode == "governed":
        if all_vecs is None:
            pool = filter_chunks(all_chunks, role=role, as_of=as_of)
            doc_vecs = None
        else:
            kept: list[Chunk] = []
            kept_vecs: list[list[float]] = []
            allowed = filter_chunks(all_chunks, role=role, as_of=as_of)
            allowed_ids = {c.chunk_id for c in allowed}
            for chunk, vec in zip(all_chunks, all_vecs, strict=True):
                if chunk.chunk_id in allowed_ids:
                    kept.append(chunk)
                    kept_vecs.append(vec)
            pool, doc_vecs = kept, kept_vecs
        notes.append("authz_and_effective_date_applied_before_retrieval")
        role_out: str | None = role
    else:
        pool = all_chunks
        doc_vecs = all_vecs
        role_out = None
        notes.append("no_authz_or_effective_date_filtering")

    hits = hybrid_retrieve(question, pool, embedder, limit=limit, doc_vecs=doc_vecs)
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
        contributors = provider_contributors(state="TX", payer_network="Aetna")
        metric = {
            "tool": "get_metric",
            "comparison": cmp,
            "contributors": contributors,
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
    """Optional LLM synthesis (Gemini or OpenAI). Keys from environment only."""
    return _synthesize_with_model(question, evidence.as_dict())


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
    use_model = synthesize if synthesize is not None else llm_configured()
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
