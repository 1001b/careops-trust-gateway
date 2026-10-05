from __future__ import annotations

import sqlite3
from dataclasses import dataclass, asdict
from datetime import date, timedelta
from typing import Any

from .bootstrap import bootstrap
from .paths import DB_PATH
from .semantic import metric_definition

REFERENCE_DATE = date(2026, 10, 5)  # fixed demo clock for reproducible evaluations


@dataclass
class MetricResult:
    metric: str
    value: int | float
    filters: dict[str, Any]
    period_start: str
    period_end: str
    authority: str
    source: str
    definition: str
    grain: list[str]

    def as_dict(self):
        return asdict(self)


def _period_bounds(period: str):
    this_monday = REFERENCE_DATE - timedelta(days=REFERENCE_DATE.weekday())
    if period == "last_week":
        start = this_monday - timedelta(days=7)
        end = this_monday - timedelta(days=1)
    elif period == "prior_week":
        start = this_monday - timedelta(days=14)
        end = this_monday - timedelta(days=8)
    else:
        raise ValueError(f"Unsupported demo period: {period}")
    return start, end


def _ensure_db():
    if not DB_PATH.exists():
        bootstrap()


def get_metric(metric: str, *, state: str, payer_network: str, period: str) -> MetricResult:
    if metric != "available_appointments":
        raise KeyError(f"Demo implements only available_appointments, got {metric}")
    _ensure_db()
    definition = metric_definition(metric)
    start, end = _period_bounds(period)
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(is_bookable), 0)
            FROM gold_provider_availability_daily
            WHERE state = ? AND payer_network = ?
              AND date(slot_date) BETWEEN date(?) AND date(?)
            """,
            (state, payer_network, start.isoformat(), end.isoformat()),
        ).fetchone()
        value = int(row[0])
    finally:
        conn.close()
    return MetricResult(
        metric=metric,
        value=value,
        filters={"state": state, "payer_network": payer_network},
        period_start=start.isoformat(),
        period_end=end.isoformat(),
        authority=definition["authority"],
        source=definition["source"],
        definition=definition["definition"],
        grain=definition["grain"],
    )


def compare_availability(*, state: str, payer_network: str):
    prior = get_metric("available_appointments", state=state, payer_network=payer_network, period="prior_week")
    current = get_metric("available_appointments", state=state, payer_network=payer_network, period="last_week")
    delta = current.value - prior.value
    pct = None if prior.value == 0 else round(delta / prior.value * 100.0, 1)
    return {"prior": prior.as_dict(), "current": current.as_dict(), "delta": delta, "pct_change": pct}


def provider_contributors(*, state: str, payer_network: str):
    _ensure_db()
    prior_start, prior_end = _period_bounds("prior_week")
    cur_start, cur_end = _period_bounds("last_week")
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            """
            SELECT provider_id, provider_name,
                   SUM(CASE WHEN date(slot_date) BETWEEN date(?) AND date(?) THEN is_bookable ELSE 0 END) AS prior_slots,
                   SUM(CASE WHEN date(slot_date) BETWEEN date(?) AND date(?) THEN is_bookable ELSE 0 END) AS current_slots
            FROM gold_provider_availability_daily
            WHERE state = ? AND payer_network = ?
              AND date(slot_date) BETWEEN date(?) AND date(?)
            GROUP BY provider_id, provider_name
            ORDER BY (current_slots - prior_slots) ASC, provider_id
            """,
            (prior_start.isoformat(), prior_end.isoformat(), cur_start.isoformat(), cur_end.isoformat(),
             state, payer_network, prior_start.isoformat(), cur_end.isoformat()),
        ).fetchall()
    finally:
        conn.close()
    return [
        {"provider_id": r[0], "provider_name": r[1], "prior_slots": int(r[2]), "current_slots": int(r[3]), "delta": int(r[3]-r[2])}
        for r in rows if int(r[3]-r[2]) != 0
    ]
