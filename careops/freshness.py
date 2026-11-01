"""Source-age gate. The demo clock and the load timestamp are metadata, not a model opinion."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, time, timezone

from .metrics import REFERENCE_DATE
from .paths import SEMANTIC_DIR
from .semantic import metric_definition

PRODUCT = "available_appointments"


class FreshnessError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Freshness:
    source_id: str
    data_product: str
    state: str
    fresh: bool
    loaded_at: str | None
    evaluated_at: str | None
    age_minutes: int | None
    slo_minutes: int | None

    def as_dict(self) -> dict:
        body = asdict(self)
        body.pop("fresh", None)
        body["freshness"] = self.state
        return body


def load_sources() -> dict:
    return json.loads((SEMANTIC_DIR / "source_freshness.json").read_text(encoding="utf-8"))


def evaluation_clock() -> datetime:
    """Same reproducible instant as the qualified demo, at noon UTC."""
    return datetime.combine(REFERENCE_DATE, time(12, 0), tzinfo=timezone.utc)


def not_applicable(source_id: str, data_product: str) -> Freshness:
    return Freshness(
        source_id=source_id,
        data_product=data_product,
        state="not_applicable",
        fresh=True,
        loaded_at=None,
        evaluated_at=None,
        age_minutes=None,
        slo_minutes=None,
    )


def evaluate(source_id: str) -> Freshness:
    record = load_sources().get(source_id)
    if not isinstance(record, dict) or "loaded_at" not in record:
        raise FreshnessError("source_metadata_missing")
    product = record.get("data_product") or PRODUCT
    contract = metric_definition(product)
    loaded = datetime.fromisoformat(record["loaded_at"])
    if loaded.tzinfo is None:
        loaded = loaded.replace(tzinfo=timezone.utc)
    now = evaluation_clock()
    age = int((now - loaded).total_seconds() // 60)
    slo = int(contract["freshness_slo_minutes"])
    state = "fresh" if age <= slo else "stale"
    return Freshness(
        source_id=source_id,
        data_product=contract["metric_id"],
        state=state,
        fresh=state == "fresh",
        loaded_at=loaded.isoformat(),
        evaluated_at=now.isoformat(),
        age_minutes=age,
        slo_minutes=slo,
    )


def _product(item: Freshness, reason: str | None = None) -> dict:
    body = item.as_dict()
    if reason is not None:
        body["reason"] = reason
    return body


def readiness() -> dict:
    """Name the governed product. A stale or missing source is not a bare boolean."""
    contract = metric_definition(PRODUCT)
    source_id = contract["source"]
    try:
        item = evaluate(source_id)
    except (FreshnessError, KeyError) as exc:
        code = getattr(exc, "code", "source_metadata_missing")
        unknown = Freshness(
            source_id=source_id,
            data_product=contract["metric_id"],
            state="unknown",
            fresh=False,
            loaded_at=None,
            evaluated_at=None,
            age_minutes=None,
            slo_minutes=int(contract["freshness_slo_minutes"]),
        )
        return {"status": "not_ready", "affected": [_product(unknown, code)]}
    if item.fresh:
        return {"status": "ready", "products": [_product(item)]}
    return {"status": "not_ready", "affected": [_product(item)]}
