"""Finding lineage. Repetition is not corroboration and is not verification."""

from __future__ import annotations

import uuid

from careops.context.ontology import VALIDITY_TYPES
from careops.control.errors import ControlError

_REFUSED_ON_INSERT = frozenset({"VERIFIED", "COMMITTED"})


def corroboration(findings: list[dict]) -> dict:
    roots: list[str] = []
    seen: set[str] = set()
    for finding in findings:
        for root in finding.get("evidence_root_ids") or []:
            if root not in seen:
                seen.add(root)
                roots.append(root)
    return {
        "finding_count": len(findings),
        "independent_count": len(seen),
        "evidence_root_ids": roots,
    }


def make_finding(
    *,
    validity_type: str,
    evidence_root_ids: list[str],
    producer: str,
    source_refs: list[str] | None = None,
    derived_from: list[str] | None = None,
    confidence: float = 0.0,
    finding_id: str | None = None,
) -> dict:
    if validity_type not in VALIDITY_TYPES:
        raise ControlError("validity_unknown")
    if validity_type == "COMMITTED":
        raise ControlError("commit_requires_authority_plane")
    if validity_type in _REFUSED_ON_INSERT:
        raise ControlError("validity_upgrade_refused")
    if not evidence_root_ids:
        raise ControlError("evidence_root_required")
    return {
        "finding_id": finding_id or str(uuid.uuid4()),
        "validity_type": validity_type,
        "source_refs": list(source_refs or []),
        "evidence_root_ids": list(evidence_root_ids),
        "derived_from": list(derived_from or []),
        "producer": producer,
        "producer_agent": producer,
        "confidence": confidence,
    }


def repeat_finding(existing: dict, *, producer: str) -> dict:
    """A second producer keeps the same validity and the same evidence roots."""
    if existing["validity_type"] == "COMMITTED":
        raise ControlError("commit_requires_authority_plane")
    return {
        "finding_id": str(uuid.uuid4()),
        "validity_type": existing["validity_type"],
        "source_refs": list(existing.get("source_refs") or []),
        "evidence_root_ids": list(existing["evidence_root_ids"]),
        "derived_from": [existing["finding_id"]],
        "producer": producer,
        "producer_agent": producer,
        "confidence": existing.get("confidence") or 0.0,
    }


def verify_finding(existing: dict) -> dict:
    """Deterministic verification is the only path to VERIFIED. It never writes COMMITTED."""
    if existing["validity_type"] == "COMMITTED":
        raise ControlError("commit_requires_authority_plane")
    verified = dict(existing)
    verified["validity_type"] = "VERIFIED"
    verified["verification"] = "deterministic"
    return verified
