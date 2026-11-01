"""Graphiti sits behind TemporalContextStore. A missing client is an unavailable context plane."""

from __future__ import annotations

from careops.context.ontology import RELATIONSHIPS, validate_ontology
from careops.context.store import ContextUnavailable, LocalTemporalStore


def graphiti_runtime_available() -> bool:
    try:
        import graphiti_core  # noqa: F401
    except ImportError:
        return False
    return True


class GraphitiTemporalStore:
    """Adapter. Returned episodes still pass through the application store rules."""

    def __init__(self, client=None):
        self.client = client

    @property
    def available(self) -> bool:
        return self.client is not None

    def _backed(self, organization_id: str, as_of: str) -> LocalTemporalStore:
        if not self.available:
            raise ContextUnavailable("graphiti_unavailable")
        raw = self.client.search({"group_id": organization_id, "as_of": as_of, "authority": False})
        return LocalTemporalStore(list(raw))

    def visible(self, organization_id: str, as_of: str) -> list[dict]:
        return self._backed(organization_id, as_of).visible(organization_id, as_of)

    def preceding(self, organization_id: str, subject_ref: str, as_of: str) -> list[dict]:
        return self._backed(organization_id, as_of).preceding(organization_id, subject_ref, as_of)

    def current(self, organization_id: str, subject_ref: str, as_of: str) -> list[dict]:
        return self._backed(organization_id, as_of).current(organization_id, subject_ref, as_of)

    def add_episode(self, episode: dict) -> dict:
        if not self.available:
            raise ContextUnavailable("graphiti_unavailable")
        validate_ontology()
        relationship = (episode.get("payload") or {}).get("relationship")
        if relationship is not None and relationship not in RELATIONSHIPS:
            raise ContextUnavailable("relationship_outside_ontology")
        body = {
            "entity_types": _entity_types(episode),
            "edge_type": relationship,
            "group_id": episode["organization_id"],
            "reference_time": episode["reference_time"],
            "observed_at": episode["observed_at"],
            "validity_type": episode["validity_type"],
            "authority": False,
            "store_authority": False,
        }
        self.client.add_episode(body)
        return body


def _entity_types(episode: dict) -> list[str]:
    found = []
    for ref in episode.get("subject_refs") or []:
        kind = ref.split(":", 1)[0]
        mapped = {
            "provider": "Provider",
            "network": "Network",
            "policy": "Policy",
            "metric": "MetricContract",
            "data_product": "DataProduct",
            "organization": "Organization",
        }.get(kind)
        if mapped and mapped not in found:
            found.append(mapped)
    return found or ["ChangeEvent"]
