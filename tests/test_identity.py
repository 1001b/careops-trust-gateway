"""TC-03: protected identity, field policy, and the unchanged demo path."""

from __future__ import annotations

import json

import pytest

from careops.gateway import answer as gateway_answer
from careops.governance import apply_field_policy, validate_contracts
from careops.identity import IdentityConfig, bounded_demo_role, issue_token
from careops.protected import protected_query
from careops.semantic import metric_definition

CONFIG = IdentityConfig(secret="test-secret")
QUESTION = "Why did in-network appointment availability in Texas decline last week?"


def _token(**overrides) -> str:
    fields = {
        "principal": "analyst-1",
        "role": "analyst",
        "tenant": "tenant-demo",
        "purpose": "operations_analysis",
    }
    fields.update(overrides)
    return issue_token(CONFIG, **fields)


def test_contracts_are_versioned_and_schema_valid():
    validate_contracts()
    metric = metric_definition("available_appointments")
    assert metric["contract_version"] == 1
    assert metric["authority"] == "tool:get_metric"


def test_valid_token_supplies_identity_and_keeps_metric():
    result = protected_query(_token(), QUESTION, config=CONFIG)
    assert result["http_status"] == 200
    assert result["outcome"] == "allow"
    assert result["model_called"] is False
    assert result["identity"]["principal"] == "analyst-1"
    assert result["identity"]["role"] == "analyst"
    assert result["identity"]["tenant"] == "tenant-demo"
    assert result["identity"]["purpose"] == "operations_analysis"
    assert "query" in result["identity"]["scopes"]
    metric = next(item for item in result["evidence"] if item["type"] == "metric")
    assert metric["comparison"]["prior"]["value"] == 42
    assert metric["comparison"]["current"]["value"] == 24
    assert "42" in result["answer"] and "24" in result["answer"]


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        ("forged", "forged"),
        ("expired", "expired"),
        ("unsigned", "unsigned"),
        ("wrong_issuer", "wrong_issuer"),
        ("wrong_audience", "wrong_audience"),
    ],
)
def test_bad_token_rejected_before_retrieval(monkeypatch, mutate, reason):
    def fail_retrieval(*_args, **_kwargs):
        raise AssertionError("retrieval ran")

    monkeypatch.setattr("careops.protected.gateway_answer", fail_retrieval)
    if mutate == "forged":
        issued = _token()
        token = issued[:-1] + ("A" if issued[-1] != "A" else "B")
    elif mutate == "expired":
        token = _token(expired=True)
    elif mutate == "unsigned":
        token = _token(alg="none")
    elif mutate == "wrong_issuer":
        token = _token(issuer="other-issuer")
    else:
        token = _token(audience="other-audience")
    result = protected_query(token, QUESTION, config=CONFIG)
    assert result["http_status"] == 401
    assert result["reason_code"] == reason
    assert result["retrieval_attempted"] is False
    assert result["model_called"] is False
    assert result["evidence"] is None


def test_unauthorized_role_rejected_before_retrieval(monkeypatch):
    def fail_retrieval(*_args, **_kwargs):
        raise AssertionError("retrieval ran")

    monkeypatch.setattr("careops.protected.gateway_answer", fail_retrieval)
    result = protected_query(_token(role="guest", principal="guest-1"), QUESTION, config=CONFIG)
    assert result["http_status"] == 403
    assert result["reason_code"] == "unauthorized_role"
    assert result["retrieval_attempted"] is False
    assert result["model_called"] is False


def test_cross_tenant_denied_before_retrieval(monkeypatch):
    def fail_retrieval(*_args, **_kwargs):
        raise AssertionError("retrieval ran")

    monkeypatch.setattr("careops.protected.gateway_answer", fail_retrieval)
    result = protected_query(_token(tenant="tenant-other"), QUESTION, config=CONFIG)
    assert result["http_status"] == 403
    assert result["reason_code"] == "cross_tenant"
    assert result["retrieval_attempted"] is False
    assert result["evidence"] is None


def test_body_role_and_tenant_cannot_override_token():
    result = protected_query(
        _token(),
        QUESTION,
        config=CONFIG,
        untrusted_role="clinical_admin",
        untrusted_tenant="tenant-other",
    )
    assert result["http_status"] == 200
    assert result["identity"]["role"] == "analyst"
    assert result["identity"]["tenant"] == "tenant-demo"
    blob = json.dumps(result["evidence"]) + result["answer"]
    assert "Ben Chen" not in blob
    assert "Carla Diaz" not in blob
    assert "credential_notes" not in blob
    assert "synthetic restricted credential note" not in blob


def test_denied_and_masked_fields_stay_out_of_model_context():
    analyst = protected_query(_token(), QUESTION, config=CONFIG)
    analyst_blob = json.dumps(analyst["evidence"]) + analyst["answer"]
    assert analyst["model_called"] is False
    assert "***" in analyst_blob
    assert "credential_notes" not in analyst_blob
    assert "synthetic restricted credential note" not in analyst_blob

    admin = protected_query(
        _token(role="clinical_admin", principal="admin-1"),
        QUESTION,
        config=CONFIG,
    )
    admin_blob = json.dumps(admin["evidence"])
    assert "Ben Chen" in admin_blob
    assert "synthetic restricted credential note" in admin_blob

    raw = [{"type": "metric", "comparison": {}, "contributors": [{"provider_id": "P002", "provider_name": "Ben Chen", "delta": -1}]}]
    masked = apply_field_policy(raw, "analyst")
    assert masked[0]["contributors"][0]["provider_name"] == "***"
    assert "credential_notes" not in masked[0]["contributors"][0]


def test_public_demo_path_still_returns_unmasked_qualified_result():
    principal = bounded_demo_role("analyst")
    assert principal.principal == "demo-analyst"
    assert principal.tenant == "tenant-demo"
    with pytest.raises(ValueError):
        bounded_demo_role("guest")
    response = gateway_answer(QUESTION, role=principal.role)
    assert response.status == "ok"
    metric = next(item for item in response.evidence if item["type"] == "metric")
    assert metric["comparison"]["prior"]["value"] == 42
    assert metric["comparison"]["current"]["value"] == 24
    assert "Ben Chen" in response.answer
    assert "Carla Diaz" in response.answer
