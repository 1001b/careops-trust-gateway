"""v0.2 retrieval boundary tests — offline, same local embedder for both paths."""
from careops.agent import retrieve_evidence
from careops.bootstrap import bootstrap
from careops.rag.embed import LocalHashEmbedder


def setup_module():
    bootstrap()


def test_same_embedder_naive_can_surface_stale_and_restricted():
    emb = LocalHashEmbedder()
    q = "network eligibility credentialing escalation pending"
    naive = retrieve_evidence(q, mode="naive", role="analyst", embedder=emb, limit=8)
    ids = {c["doc_id"] for c in naive.chunks}
    assert "network-policy-v1" in ids or "credentialing-escalation" in ids
    # Fairness: at least one of the failure-mode docs is retrievable without filters
    assert ids & {"network-policy-v1", "credentialing-escalation"}


def test_governed_excludes_stale_and_restricted_for_analyst():
    emb = LocalHashEmbedder()
    q = "network eligibility credentialing escalation pending"
    gov = retrieve_evidence(q, mode="governed", role="analyst", embedder=emb, limit=8)
    ids = {c["doc_id"] for c in gov.chunks}
    assert gov.status == "ok"
    assert "network-policy-v1" not in ids
    assert "credentialing-escalation" not in ids
    assert "network-policy-v2" in ids or "availability-definition" in ids


def test_governed_admin_can_retrieve_credentialing():
    emb = LocalHashEmbedder()
    q = "credentialing escalation procedure"
    gov = retrieve_evidence(q, mode="governed", role="clinical_admin", embedder=emb, limit=5)
    ids = {c["doc_id"] for c in gov.chunks}
    assert "credentialing-escalation" in ids
    assert gov.status == "ok"


def test_governed_analyst_credentialing_abstains():
    emb = LocalHashEmbedder()
    q = "What is the credentialing escalation procedure?"
    gov = retrieve_evidence(q, mode="governed", role="analyst", embedder=emb, limit=5)
    assert gov.status == "insufficient_evidence"
    assert gov.chunks == []


def test_governed_mixed_question_attaches_metric_authority():
    emb = LocalHashEmbedder()
    q = "Why did in-network appointment availability in Texas decline last week?"
    gov = retrieve_evidence(q, mode="governed", role="analyst", embedder=emb, limit=5)
    assert gov.metric is not None
    assert gov.metric["comparison"]["prior"]["value"] == 42
    assert gov.metric["comparison"]["current"]["value"] == 24
    assert "gold_provider_availability_daily" in gov.provenance
    names = {c["provider_name"] for c in gov.metric["contributors"]}
    assert "Ben Chen" in names and "Carla Diaz" in names


def test_naive_mixed_question_has_no_metric_tool():
    emb = LocalHashEmbedder()
    q = "Why did in-network appointment availability in Texas decline last week?"
    naive = retrieve_evidence(q, mode="naive", role="analyst", embedder=emb, limit=5)
    assert naive.metric is None
    assert any("no_metric_tool" in n for n in naive.notes)
