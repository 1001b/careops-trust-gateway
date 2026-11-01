"""TC-05: one governed result across core, REST, and CLI, plus contract replay."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from careops.agent.pipeline import retrieve_evidence
from careops.cli import respond
from careops.conformance import project_gateway_payload
from careops.gateway import answer
from careops.identity import IdentityConfig, issue_token
from careops.protected import protected_query
from evals.replay import compare, current_snapshot

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evals" / "fixtures" / "replay"
CONFIG = IdentityConfig(secret="test-secret")
FLAGSHIP = "Why did in-network appointment availability in Texas decline last week?"
CALIFORNIA = "How many bookable slots were available in California last week?"
UNKNOWN = "What is the moon made of?"
CREDENTIALING = "What is the credentialing escalation procedure?"


def _token(**overrides) -> str:
    fields = {
        "principal": "analyst-1",
        "role": "analyst",
        "tenant": "tenant-demo",
        "purpose": "operations_analysis",
    }
    fields.update(overrides)
    return issue_token(CONFIG, **fields)


def _surfaces(question: str) -> tuple[dict, dict, dict]:
    core = project_gateway_payload(answer(question, role="analyst").as_dict())
    cli = respond(question, "analyst")["governed"]
    rest = protected_query(_token(), question, config=CONFIG)["governed"]
    return core, cli, rest


def test_core_rest_and_cli_return_the_same_governed_result():
    core, cli, rest = _surfaces(FLAGSHIP)
    assert core == cli == rest
    assert core["metric_id"] == "available_appointments"
    assert core["contract_version"] == 1
    assert core["value"] == {"prior": 42, "current": 24}
    assert core["as_of"] == "2026-10-05"
    assert core["freshness"] == "fresh"
    assert core["outcome"] == "allow"
    assert core["reason_code"] == "ok"
    assert "gold_provider_availability_daily" in core["provenance"]


def test_abstain_and_deny_use_the_same_reasons():
    for question in (UNKNOWN, CREDENTIALING):
        core, cli, rest = _surfaces(question)
        assert core == cli == rest
        assert core["outcome"] == "abstain"
        assert core["reason_code"] == "insufficient_evidence"
        assert core["value"] is None
    denied = protected_query(_token(role="guest", principal="guest-1"), FLAGSHIP, config=CONFIG)
    assert denied["outcome"] == "deny"
    assert denied["governed"]["outcome"] == "deny"
    assert denied["governed"]["reason_code"] == "unauthorized_role"
    assert denied["governed"]["value"] is None


def test_agent_scope_matches_the_gateway():
    flagship = retrieve_evidence(FLAGSHIP, mode="governed", role="analyst")
    assert flagship.metric["comparison"]["prior"]["value"] == 42
    assert flagship.metric["comparison"]["current"]["value"] == 24
    assert flagship.metric["comparison"]["current"]["filters"]["state"] == "TX"
    core = answer(CALIFORNIA, role="analyst")
    agent = retrieve_evidence(CALIFORNIA, mode="governed", role="analyst")
    core_metric = next(item for item in core.evidence if item["type"] == "metric")
    assert core_metric["comparison"]["current"]["filters"]["state"] == "CA"
    assert agent.metric["comparison"]["current"]["filters"]["state"] == "CA"
    assert core_metric["comparison"]["current"]["value"] == agent.metric["comparison"]["current"]["value"]


def test_adapters_do_not_own_the_metric_or_the_policy():
    for rel in ("careops/cli.py", "app/main.py", "mcp_server/server.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "is_bookable" not in text
        assert "SUM(" not in text
        assert "field_actions" not in text
    pipeline = (ROOT / "careops/agent/pipeline.py").read_text(encoding="utf-8")
    assert 'state="TX"' not in pipeline
    assert "is_bookable" not in pipeline


def test_replay_baseline_matches_the_live_contracts():
    baseline = json.loads((FIXTURES / "baseline.json").read_text(encoding="utf-8"))
    assert baseline == current_snapshot()


def test_safe_additive_change_passes_and_unversioned_break_fails():
    base = json.loads((FIXTURES / "baseline.json").read_text(encoding="utf-8"))
    safe = json.loads((FIXTURES / "safe_additive.json").read_text(encoding="utf-8"))
    unsafe = json.loads((FIXTURES / "unsafe_unversioned.json").read_text(encoding="utf-8"))
    safe_report = compare(base, safe)
    unsafe_report = compare(base, unsafe)
    assert safe_report["ok"] is True
    assert any(item["kind"] == "contract" and item["change"] == "additive" for item in safe_report["findings"])
    assert unsafe_report["ok"] is False
    assert any(item["kind"] == "grain" for item in unsafe_report["findings"])


def test_replay_exposes_access_masking_freshness_metric_and_response_drift():
    base = current_snapshot()

    freshness = json.loads(json.dumps(base))
    freshness["metric"]["freshness_slo_minutes"] = 60
    freshness_report = compare(base, freshness)
    assert any(item["kind"] == "freshness" for item in freshness_report["findings"])
    assert freshness_report["ok"] is False

    drifted = json.loads(json.dumps(base))
    drifted["authoritative"]["current"] = 25
    drift_report = compare(base, drifted)
    assert any(item["kind"] == "metric" and item["change"] == "drift" for item in drift_report["findings"])
    assert drift_report["ok"] is False

    widened = json.loads(json.dumps(base))
    widened["policy"]["rules"][0]["field_actions"]["provider_name"] = "allow"
    widened["access"]["roles"]["analyst"]["document_classes"].append("restricted")
    widened_report = compare(base, widened)
    assert any(item["kind"] == "masking" and item["change"] == "expansion" for item in widened_report["findings"])
    assert any(item["kind"] == "access" and item["change"] == "expansion" for item in widened_report["findings"])
    assert widened_report["ok"] is False

    broken = json.loads(json.dumps(base))
    broken["response_fields"] = [field for field in broken["response_fields"] if field != "outcome"]
    broken_report = compare(base, broken)
    assert any(item["kind"] == "response" and item["change"] == "break" for item in broken_report["findings"])
    assert broken_report["ok"] is False


def _replay(candidate: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "evals.replay", "--base", str(FIXTURES / "baseline.json"), "--candidate", str(FIXTURES / candidate)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_replay_command_exits_nonzero_on_the_unsafe_fixture():
    safe = _replay("safe_additive.json")
    unsafe = _replay("unsafe_unversioned.json")
    assert safe.returncode == 0
    assert unsafe.returncode != 0
    assert any(item["kind"] == "grain" for item in json.loads(unsafe.stdout)["findings"])


def test_mcp_claim_stays_at_the_four_qualified_tools():
    text = (ROOT / "mcp_server" / "server.py").read_text(encoding="utf-8")
    names = re.findall(r'@mcp\.tool\(name="([^"]+)"\)', text)
    assert names == ["get_metric", "explain_metric", "search_policy", "resolve_entity"]
    assert "/v1/query" not in text
    assert "is_bookable" not in text
