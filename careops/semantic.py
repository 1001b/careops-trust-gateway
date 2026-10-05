from __future__ import annotations

import json
from .paths import SEMANTIC_DIR


def _load(name: str):
    return json.loads((SEMANTIC_DIR / name).read_text(encoding="utf-8"))


def metric_definition(metric: str):
    metrics = _load("metrics.json")
    if metric not in metrics:
        raise KeyError(f"Unknown governed metric: {metric}")
    return metrics[metric]


def access_policy():
    return _load("access.json")


def entity_definition(entity_type: str):
    entities = _load("entities.json")
    if entity_type not in entities:
        raise KeyError(f"Unknown governed entity: {entity_type}")
    return entities[entity_type]
