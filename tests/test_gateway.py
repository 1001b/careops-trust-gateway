from careops.bootstrap import bootstrap
from careops.entities import resolve_provider
from careops.gateway import answer
from careops.metrics import compare_availability, get_metric
from careops.policy import search_policy
from careops.router import classify


def setup_module():
    bootstrap()


def test_metric_values_are_deterministic():
    assert get_metric("available_appointments", state="TX", payer_network="Aetna", period="prior_week").value == 42
    assert get_metric("available_appointments", state="TX", payer_network="Aetna", period="last_week").value == 24
    cmp = compare_availability(state="TX", payer_network="Aetna")
    assert cmp["delta"] == -18
    assert cmp["pct_change"] == -42.9


def test_current_policy_excludes_stale_version():
    ids = [d["id"] for d in search_policy("network eligibility pending", role="analyst")]
    assert "network-policy-v2" in ids
    assert "network-policy-v1" not in ids


def test_authorization_prevents_restricted_retrieval():
    analyst_ids = [d["id"] for d in search_policy("credentialing escalation", role="analyst")]
    admin_ids = [d["id"] for d in search_policy("credentialing escalation", role="clinical_admin")]
    assert "credentialing-escalation" not in analyst_ids
    assert "credentialing-escalation" in admin_ids


def test_mixed_question_routes_to_both_paths():
    assert classify("Why did in-network appointment availability in Texas decline last week?") == "mixed"
    response = answer("Why did in-network appointment availability in Texas decline last week?", role="analyst")
    assert response.status == "ok"
    assert response.route == "mixed"
    assert any(e["type"] == "metric" for e in response.evidence)
    assert any(e["type"] == "policy" for e in response.evidence)
    assert "gold_provider_availability_daily" in response.provenance


def test_unknown_request_abstains():
    response = answer("What is the moon made of?", role="analyst")
    assert response.status == "insufficient_evidence"


def test_restricted_question_abstains_for_analyst():
    response = answer("What is the credentialing escalation procedure?", role="analyst")
    assert response.status == "insufficient_evidence"
    admin = answer("What is the credentialing escalation procedure?", role="clinical_admin")
    assert admin.status == "ok"
    assert any("credentialing_escalation" in p for p in admin.provenance)


def test_entity_resolution_exposes_confidence_and_review_signal():
    exact = resolve_provider("Carla Diaz")
    assert exact["confidence"] == 1.0
    fuzzy = resolve_provider("Carla Daz")
    assert fuzzy["provider_id"] == "P003"
    assert 0 < fuzzy["confidence"] < 1
