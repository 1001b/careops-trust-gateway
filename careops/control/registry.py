"""Bounded specialist capabilities. This is not a discovery service."""

from __future__ import annotations

from careops.control.errors import ControlError
from careops.control.schema import CAPABILITIES


def resolve_capability(name: str) -> dict:
    try:
        record = CAPABILITIES[name]
    except KeyError as exc:
        raise ControlError("capability_unknown") from exc
    return {
        "requested_capability": name,
        "assigned_agent": record["assigned_agent"],
        "allowed_tools": list(record["allowed_tools"]),
    }
