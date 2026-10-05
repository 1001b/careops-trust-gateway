from __future__ import annotations
import json
from pathlib import Path

from careops.bootstrap import bootstrap
from careops.gateway import answer
from careops.metrics import get_metric
from careops.policy import search_policy
from careops.router import classify

ROOT = Path(__file__).resolve().parents[1]


def main():
    bootstrap()
    cases = json.loads((ROOT / "evals/cases.json").read_text())
    passed = 0
    for c in cases:
        ok = False
        if c["kind"] == "policy":
            ids = [x["id"] for x in search_policy(c["query"], role=c["role"])]
            ok = all(x in ids for x in c.get("expect_ids", [])) and all(x not in ids for x in c.get("forbid_ids", []))
            detail = ids
        elif c["kind"] == "metric":
            value = get_metric("available_appointments", state=c["state"], payer_network=c["network"], period=c["period"]).value
            ok = value == c["expect"]
            detail = value
        elif c["kind"] == "router":
            value = classify(c["question"])
            ok = value == c["expect"]
            detail = value
        elif c["kind"] == "gateway":
            value = answer(c["question"], role=c["role"]).status
            ok = value == c["expect_status"]
            detail = value
        else:
            detail = "unknown case"
        status = "PASS" if ok else "FAIL"
        print(f"{status:4} {c['id']}: {c['description']} -> {detail}")
        passed += int(ok)
    print(f"\n{passed}/{len(cases)} evaluations passed")
    raise SystemExit(0 if passed == len(cases) else 1)


if __name__ == "__main__":
    main()
