"""Compare two contract snapshots. A wider grant or an unversioned break fails closed."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from careops.conformance import RESPONSE_FIELDS
from careops.paths import SEMANTIC_DIR

PERMISSIVE = {"deny": 0, "mask": 1, "hash": 2, "allow": 3}
SEMANTIC_FIELDS = (
    "metric_id",
    "definition",
    "dimensions",
    "authority",
    "source",
    "fact_grain",
    "owner",
    "status",
    "allowed_purposes",
    "classification",
    "compatibility",
    "effective_from",
    "deprecation",
    "consumers",
)


def current_snapshot() -> dict:
    metric = json.loads((SEMANTIC_DIR / "metrics.json").read_text(encoding="utf-8"))
    policy = json.loads((SEMANTIC_DIR / "governance.json").read_text(encoding="utf-8"))
    access = json.loads((SEMANTIC_DIR / "access.json").read_text(encoding="utf-8"))
    return {
        "metric": metric["available_appointments"],
        "policy": policy,
        "access": access,
        "authoritative": {"prior": 42, "current": 24},
        "response_fields": list(RESPONSE_FIELDS),
    }


def _rules(policy: dict) -> dict:
    return {rule["rule_id"]: rule for rule in policy.get("rules", [])}


def _access_findings(base: dict, candidate: dict) -> list[dict]:
    findings = []
    base_roles = base["access"]["roles"]
    cand_roles = candidate["access"]["roles"]
    for role, grant in cand_roles.items():
        previous = base_roles.get(role)
        if previous is None:
            findings.append({"kind": "access", "change": "expansion", "detail": f"role {role} added"})
            continue
        added = set(grant["document_classes"]) - set(previous["document_classes"])
        removed = set(previous["document_classes"]) - set(grant["document_classes"])
        if added:
            findings.append({"kind": "access", "change": "expansion", "detail": f"{role} classes {sorted(added)}"})
        if removed:
            findings.append({"kind": "access", "change": "restriction", "detail": f"{role} classes {sorted(removed)}"})
    for role in base_roles:
        if role not in cand_roles:
            findings.append({"kind": "access", "change": "restriction", "detail": f"role {role} removed"})

    base_rules = _rules(base["policy"])
    cand_rules = _rules(candidate["policy"])
    for rule_id, rule in cand_rules.items():
        previous = base_rules.get(rule_id)
        if previous is None:
            findings.append({"kind": "access", "change": "expansion", "detail": f"rule {rule_id} added"})
            continue
        added = set(rule["allowed_classes"]) - set(previous["allowed_classes"])
        removed = set(previous["allowed_classes"]) - set(rule["allowed_classes"])
        if added:
            findings.append({"kind": "access", "change": "expansion", "detail": f"{rule_id} classes {sorted(added)}"})
        if removed:
            findings.append({"kind": "access", "change": "restriction", "detail": f"{rule_id} classes {sorted(removed)}"})
        for field, action in rule["field_actions"].items():
            old = previous["field_actions"].get(field)
            if old is None:
                findings.append({"kind": "masking", "change": "expansion", "detail": f"{rule_id} {field} added as {action}"})
                continue
            if action == old:
                continue
            direction = "expansion" if PERMISSIVE[action] > PERMISSIVE[old] else "restriction"
            findings.append({"kind": "masking", "change": direction, "detail": f"{rule_id} {field} {old}->{action}"})
        for field in previous["field_actions"]:
            if field not in rule["field_actions"]:
                findings.append({"kind": "masking", "change": "restriction", "detail": f"{rule_id} {field} removed"})
    for rule_id in base_rules:
        if rule_id not in cand_rules:
            findings.append({"kind": "access", "change": "restriction", "detail": f"rule {rule_id} removed"})
    return findings


def compare(base: dict, candidate: dict) -> dict:
    findings: list[dict] = []
    base_metric = base["metric"]
    cand_metric = candidate["metric"]
    if base_metric.get("grain") != cand_metric.get("grain"):
        findings.append({"kind": "grain", "change": "changed", "detail": "metric grain differs"})
    if base_metric.get("freshness") != cand_metric.get("freshness") or base_metric.get(
        "freshness_slo_minutes"
    ) != cand_metric.get("freshness_slo_minutes"):
        findings.append({"kind": "freshness", "change": "changed", "detail": "freshness declaration differs"})
    semantic = [
        field
        for field in SEMANTIC_FIELDS
        if base_metric.get(field) != cand_metric.get(field)
    ]
    if semantic:
        findings.append({"kind": "contract", "change": "semantic", "detail": ",".join(semantic)})
    extra = sorted(set(cand_metric) - set(base_metric))
    if extra:
        findings.append({"kind": "contract", "change": "additive", "detail": ",".join(extra)})
    if base.get("authoritative") != candidate.get("authoritative"):
        findings.append({
            "kind": "metric",
            "change": "drift",
            "detail": f"{base.get('authoritative')} -> {candidate.get('authoritative')}",
        })
    findings.extend(_access_findings(base, candidate))
    missing = [field for field in base["response_fields"] if field not in candidate["response_fields"]]
    added = [field for field in candidate["response_fields"] if field not in base["response_fields"]]
    if missing:
        findings.append({"kind": "response", "change": "break", "detail": ",".join(missing)})
    if added:
        findings.append({"kind": "response", "change": "additive", "detail": ",".join(added)})

    version_bumped = cand_metric.get("contract_version") != base_metric.get("contract_version")
    policy_bumped = candidate["policy"].get("policy_version") != base["policy"].get("policy_version")
    ok = True
    for finding in findings:
        if finding["change"] == "expansion" or (finding["kind"] == "response" and finding["change"] == "break"):
            ok = False
            continue
        if finding["kind"] in {"grain", "freshness", "metric", "contract"} and not version_bumped:
            ok = False
        elif finding["kind"] in {"masking", "access"} and not policy_bumped:
            ok = False
    return {"ok": ok, "findings": findings}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay a governed contract change")
    parser.add_argument("--base", required=True)
    parser.add_argument("--candidate", required=True)
    args = parser.parse_args(argv)
    base = json.loads(Path(args.base).read_text(encoding="utf-8"))
    candidate = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
    report = compare(base, candidate)
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
