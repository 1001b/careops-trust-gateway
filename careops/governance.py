"""Versioned field policy for the protected route. The demo gateway does not call this."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .paths import SEMANTIC_DIR
from .semantic import metric_definition

FIELD_ACTIONS = {"allow", "mask", "hash", "deny"}
MASK = "***"
METRIC_CONTRACT_FIELDS = (
    "metric_id",
    "contract_version",
    "owner",
    "authority",
    "source",
    "grain",
    "dimensions",
    "effective_from",
    "classification",
    "freshness_slo_minutes",
)
POLICY_FIELDS = ("policy_id", "policy_version", "organization_id", "rules")
RULE_FIELDS = (
    "rule_id",
    "principal_roles",
    "organization_scope",
    "allowed_purposes",
    "allowed_classes",
    "field_actions",
)

# Synthetic field used only to prove deny/mask. It is not a metric input.
SYNTHETIC_CREDENTIAL_NOTES = {
    "P002": "synthetic restricted credential note",
}


class ContractError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str
    rule_id: str | None
    policy_version: int


def load_policy() -> dict:
    return json.loads((SEMANTIC_DIR / "governance.json").read_text(encoding="utf-8"))


def validate_contracts() -> None:
    policy = load_policy()
    missing = [field for field in POLICY_FIELDS if field not in policy]
    if missing:
        raise ContractError(f"policy_missing:{','.join(missing)}")
    if not isinstance(policy["policy_version"], int):
        raise ContractError("policy_version")
    if not policy["organization_id"]:
        raise ContractError("organization_id")
    if not policy["rules"]:
        raise ContractError("rules")
    for rule in policy["rules"]:
        absent = [field for field in RULE_FIELDS if field not in rule]
        if absent:
            raise ContractError(f"rule_missing:{','.join(absent)}")
        unknown = set(rule["field_actions"].values()) - FIELD_ACTIONS
        if unknown:
            raise ContractError(f"field_action:{','.join(sorted(unknown))}")
    contract = metric_definition("available_appointments")
    absent = [field for field in METRIC_CONTRACT_FIELDS if field not in contract]
    if absent:
        raise ContractError(f"metric_missing:{','.join(absent)}")
    if contract["authority"] != "tool:get_metric":
        raise ContractError("metric_authority")
    if not isinstance(contract["contract_version"], int):
        raise ContractError("metric_version")


def authorize(*, role: str, tenant: str, purpose: str) -> AccessDecision:
    policy = load_policy()
    version = int(policy["policy_version"])
    if tenant != policy["organization_id"]:
        return AccessDecision(False, "cross_tenant", None, version)
    for rule in policy["rules"]:
        if role not in rule["principal_roles"]:
            continue
        if purpose not in rule["allowed_purposes"]:
            return AccessDecision(False, "unauthorized_purpose", rule["rule_id"], version)
        return AccessDecision(True, "allow", rule["rule_id"], version)
    return AccessDecision(False, "unauthorized_role", None, version)


def field_actions_for(role: str) -> dict[str, str]:
    policy = load_policy()
    for rule in policy["rules"]:
        if role in rule["principal_roles"]:
            return dict(rule["field_actions"])
    return {}


def _apply_action(value: object, action: str) -> object | None:
    if action == "deny":
        return None
    if action == "mask":
        return MASK
    if action == "hash":
        return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]
    return value


def apply_field_policy(evidence: list[dict], role: str) -> list[dict]:
    actions = field_actions_for(role)
    cleaned: list[dict] = []
    for item in evidence:
        if item.get("type") != "metric":
            cleaned.append(item)
            continue
        contributors = []
        for row in item.get("contributors", []):
            candidate = dict(row)
            note = SYNTHETIC_CREDENTIAL_NOTES.get(row.get("provider_id"))
            if note is not None:
                candidate["credential_notes"] = note
            visible = {}
            for key, value in candidate.items():
                transformed = _apply_action(value, actions.get(key, "allow"))
                if transformed is None:
                    continue
                visible[key] = transformed
            contributors.append(visible)
        cleaned.append({**item, "contributors": contributors})
    return cleaned
