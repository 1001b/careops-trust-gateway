"""Bounded ontology. These names describe context. They do not calculate a metric."""

from __future__ import annotations

import json

from careops.paths import SEMANTIC_DIR

ENTITIES = frozenset({
    "AppointmentCapacity",
    "ChangeEvent",
    "CredentialingCase",
    "DataProduct",
    "Incident",
    "MetricContract",
    "Network",
    "Organization",
    "Policy",
    "Provider",
})

RELATIONSHIPS = frozenset({
    "AFFECTS",
    "CONTRIBUTED_TO",
    "DERIVED_FROM",
    "GOVERNED_BY",
    "OBSERVED_IN",
    "OWNED_BY",
    "PARTICIPATES_IN",
    "RELATED_TO",
    "SUPERSEDES",
})

VALIDITY_TYPES = frozenset({
    "OBSERVED",
    "INFERRED",
    "PROPOSED",
    "VERIFIED",
    "COMMITTED",
    "REJECTED",
    "SUPERSEDED",
})


class OntologyError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def load_ontology() -> dict:
    return json.loads((SEMANTIC_DIR / "ontology.json").read_text(encoding="utf-8"))


def validate_ontology(document: dict | None = None) -> dict:
    document = load_ontology() if document is None else document
    entities = set(document.get("entities") or [])
    relationships = set(document.get("relationships") or [])
    if entities != set(ENTITIES) or relationships != set(RELATIONSHIPS):
        raise OntologyError("ontology_out_of_bounds")
    return document
