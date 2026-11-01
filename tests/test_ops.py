"""TC-04: freshness, decision envelope, readiness, and one bounded timeout."""

from __future__ import annotations

import json
import logging
import time

import pytest

from careops.freshness import evaluate, readiness
from careops.gateway import answer as gateway_answer
from careops.identity import IdentityConfig, bounded_demo_role, issue_token
from careops.protected import protected_query
from careops.trace import FIELDS

CONFIG = IdentityConfig(secret="test-secret")
QUESTION = "Why did in-network appointment availability in Texas decline last week?"
POLICY_QUESTION = "What is the current network eligibility policy?"
SOURCE = "gold_provider_availability_daily"


def _token(**overrides) -> str:
    fields = {
        "principal": "analyst-1",
        "role": "analyst",
        "tenant": "tenant-demo",
        "purpose": "operations_analysis",
    }
    fields.update(overrides)
    return issue_token(CONFIG, **fields)


def _sources(loaded_at: str) -> dict:
    return {
        SOURCE: {
            "source_id": SOURCE,
            "data_product": "available_appointments",
            "loaded_at": loaded_at,
        }
    }


def test_slo_boundary_is_source_age():
    fresh = evaluate(SOURCE)
    assert fresh.state == "fresh"
    assert fresh.age_minutes == 720
    assert fresh.slo_minutes == 1440
    assert fresh.data_product == "available_appointments"


def test_age_at_slo_is_fresh_and_one_minute_over_is_stale(monkeypatch):
    monkeypatch.setattr(
        "careops.freshness.load_sources",
        lambda: _sources("2026-10-04T12:00:00+00:00"),
    )
    assert evaluate(SOURCE).state == "fresh"
    assert evaluate(SOURCE).age_minutes == 1440
    monkeypatch.setattr(
        "careops.freshness.load_sources",
        lambda: _sources("2026-10-04T11:59:00+00:00"),
    )
    assert evaluate(SOURCE).state == "stale"
    assert evaluate(SOURCE).age_minutes == 1441


def test_fresh_source_returns_governed_result_and_envelope():
    result = protected_query(_token(), QUESTION, config=CONFIG, trace_id="trace-fresh")
    assert result["http_status"] == 200
    assert result["outcome"] == "allow"
    assert result["model_called"] is False
    metric = next(item for item in result["evidence"] if item["type"] == "metric")
    assert metric["comparison"]["prior"]["value"] == 42
    assert metric["comparison"]["current"]["value"] == 24
    decision = result["decision"]
    assert list(decision) == list(FIELDS)
    assert decision["trace_id"] == "trace-fresh"
    assert decision["principal_ref"] == "analyst-1"
    assert decision["organization_ref"] == "tenant-demo"
    assert decision["metric_contract"] == "available_appointments"
    assert decision["metric_contract_version"] == 1
    assert decision["policy_version"] == 1
    assert decision["matched_rule_ids"] == ["provider_detail_analyst"]
    assert SOURCE in decision["source_ids"]
    assert decision["freshness"]["freshness"] == "fresh"
    assert decision["freshness"]["data_product"] == "available_appointments"
    assert "tool:get_metric" in decision["tools_invoked"]
    assert decision["model_called"] is False
    assert isinstance(decision["latency_ms"], int)
    assert decision["latency_ms"] >= 0
    assert decision["outcome"] == "allow"
    assert decision["reason_code"] == "ok"


def test_stale_source_returns_stale_evidence_without_retrieval(monkeypatch):
    monkeypatch.setattr(
        "careops.freshness.load_sources",
        lambda: _sources("2026-10-03T00:00:00+00:00"),
    )

    def fail_retrieval(*_args, **_kwargs):
        raise AssertionError("retrieval ran")

    monkeypatch.setattr("careops.protected.gateway_answer", fail_retrieval)
    result = protected_query(_token(), QUESTION, config=CONFIG, trace_id="trace-stale")
    assert result["outcome"] == "stale_evidence"
    assert result["reason_code"] == "stale_evidence"
    assert result["model_called"] is False
    assert result["retrieval_attempted"] is False
    assert result["evidence"] is None
    assert result["answer"] is None
    assert result["decision"]["model_called"] is False
    assert result["decision"]["tools_invoked"] == []
    assert result["decision"]["trace_id"] == "trace-stale"
    assert result["decision"]["freshness"]["freshness"] == "stale"
    assert result["decision"]["freshness"]["data_product"] == "available_appointments"
    assert result["decision"]["source_ids"] == [SOURCE]
    assert "42" not in json.dumps(result["decision"])
    assert "24" not in json.dumps(result["decision"]["freshness"])


def test_readiness_names_the_stale_product(monkeypatch):
    ready = readiness()
    assert ready["status"] == "ready"
    assert ready["products"][0]["data_product"] == "available_appointments"
    assert ready["products"][0]["source_id"] == SOURCE
    monkeypatch.setattr(
        "careops.freshness.load_sources",
        lambda: _sources("2026-10-03T00:00:00+00:00"),
    )
    stale = readiness()
    assert stale["status"] == "not_ready"
    affected = stale["affected"][0]
    assert affected["data_product"] == "available_appointments"
    assert affected["source_id"] == SOURCE
    assert affected["freshness"] == "stale"


def test_dependency_timeout_is_bounded_and_discards_the_result(monkeypatch):
    calls = {"n": 0}

    def slow(*_args, **_kwargs):
        calls["n"] += 1
        time.sleep(1.0)
        return {"value": 999, "answer": "fabricated authoritative fallback"}

    monkeypatch.setattr("careops.protected.gateway_answer", slow)
    started = time.perf_counter()
    result = protected_query(
        _token(),
        QUESTION,
        config=CONFIG,
        timeout_seconds=0.1,
        trace_id="trace-timeout",
    )
    elapsed = time.perf_counter() - started
    assert elapsed < 0.5
    assert calls["n"] == 1
    assert result["http_status"] == 504
    assert result["outcome"] == "dependency_failure"
    assert result["reason_code"] == "dependency_timeout"
    assert result["model_called"] is False
    assert result["evidence"] is None
    assert result["answer"] is None
    assert result["dependency"] == SOURCE
    assert result["decision"]["trace_id"] == "trace-timeout"
    assert result["decision"]["reason_code"] == "dependency_timeout"
    assert "tool:get_metric" in result["decision"]["tools_invoked"]
    blob = json.dumps(result)
    assert "999" not in blob
    assert "fabricated" not in blob


def test_decision_log_omits_secrets_and_restricted_fields(caplog):
    caplog.set_level(logging.INFO, logger="careops.decision")
    token = _token()
    protected_query(token, QUESTION, config=CONFIG, trace_id="trace-log")
    admin_token = _token(role="clinical_admin", principal="admin-1")
    protected_query(admin_token, QUESTION, config=CONFIG, trace_id="trace-log-admin")
    text = caplog.text
    assert token not in text
    assert admin_token not in text
    assert "test-secret" not in text
    assert "synthetic restricted credential note" not in text
    assert "Ben Chen" not in text
    assert "Carla Diaz" not in text
    assert "credential_notes" not in text
    records = [json.loads(rec.message) for rec in caplog.records if rec.name == "careops.decision"]
    assert len(records) == 2
    for record in records:
        assert record["trace_id"] in {"trace-log", "trace-log-admin"}
        assert "evidence" not in record
        assert "answer" not in record
        assert "token" not in record
        assert record["model_called"] is False


def test_public_demo_still_returns_qualified_result():
    principal = bounded_demo_role("analyst")
    response = gateway_answer(QUESTION, role=principal.role)
    metric = next(item for item in response.evidence if item["type"] == "metric")
    assert metric["comparison"]["prior"]["value"] == 42
    assert metric["comparison"]["current"]["value"] == 24


def test_stale_metric_does_not_block_a_policy_question(monkeypatch):
    monkeypatch.setattr(
        "careops.freshness.load_sources",
        lambda: _sources("2026-10-03T00:00:00+00:00"),
    )
    result = protected_query(_token(), POLICY_QUESTION, config=CONFIG)
    assert result["outcome"] == "allow"
    assert result["model_called"] is False
    assert result["retrieval_attempted"] is True
    assert result["decision"]["freshness"]["freshness"] == "not_applicable"
    assert "tool:get_metric" not in result["decision"]["tools_invoked"]
