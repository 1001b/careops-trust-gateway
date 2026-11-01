"""TC-02: the governed data product, the fanout defect, and the unchanged metric."""

from __future__ import annotations

import sqlite3

from careops.bootstrap import bootstrap
from careops.metrics import get_metric
from careops.paths import DB_PATH
from careops.semantic import metric_definition

REQUIRED_CONTRACT_FIELDS = (
    "metric_id",
    "contract_version",
    "status",
    "owner",
    "authority",
    "source",
    "grain",
    "dimensions",
    "effective_from",
    "classification",
    "freshness_slo_minutes",
    "allowed_purposes",
    "compatibility",
    "deprecation",
    "fact_grain",
    "consumers",
)


def setup_module():
    bootstrap()


def _connect():
    return sqlite3.connect(DB_PATH)


def test_metric_contract_publishes_version_without_replacing_authority():
    contract = metric_definition("available_appointments")
    missing = [field for field in REQUIRED_CONTRACT_FIELDS if field not in contract]
    assert missing == []
    assert contract["contract_version"] == 1
    assert contract["owner"] == "care-operations-data"
    assert contract["authority"] == "tool:get_metric"
    assert contract["source"] == "gold_provider_availability_daily"
    assert contract["fact_grain"] == "slot_id"
    assert contract["grain"] == ["provider_id", "slot_date", "payer_network", "state"]
    assert contract["dimensions"] == ["state", "payer_network", "provider_id"]
    assert len(contract["dimensions"]) >= 2
    assert contract["effective_from"] == "2026-01-01"
    assert contract["classification"] == "internal"
    assert contract["freshness_slo_minutes"] == 1440
    assert contract["freshness"] == "daily"
    assert contract["consumers"] == ["careops.metrics.get_metric"]


def test_clean_fixture_keeps_authoritative_result_and_reconciles():
    prior = get_metric("available_appointments", state="TX", payer_network="Aetna", period="prior_week")
    current = get_metric("available_appointments", state="TX", payer_network="Aetna", period="last_week")
    assert prior.contract_version == 1
    assert current.contract_version == 1
    assert prior.value == 42
    assert current.value == 24
    assert prior.source == "gold_provider_availability_daily"

    conn = _connect()
    try:
        for result in (prior, current):
            reconciled = conn.execute(
                """
                SELECT COALESCE(SUM(is_bookable), 0)
                FROM gold_provider_availability_daily
                WHERE state = ? AND payer_network = ?
                  AND date(slot_date) BETWEEN date(?) AND date(?)
                """,
                (result.filters["state"], result.filters["payer_network"], result.period_start, result.period_end),
            ).fetchone()[0]
            assert int(reconciled) == result.value
    finally:
        conn.close()


def test_gold_grain_uniqueness_nulls_and_referential_checks():
    conn = _connect()
    try:
        gold_rows, gold_distinct = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT slot_id) FROM gold_provider_availability_daily"
        ).fetchone()
        slot_rows = conn.execute("SELECT COUNT(*) FROM appointment_slots").fetchone()[0]
        assert gold_rows == gold_distinct
        assert gold_rows == slot_rows

        nulls = conn.execute(
            """
            SELECT COUNT(*) FROM gold_provider_availability_daily
            WHERE slot_id IS NULL OR provider_id IS NULL OR state IS NULL OR payer_network IS NULL
            """
        ).fetchone()[0]
        assert nulls == 0

        slot_orphans = conn.execute(
            """
            SELECT COUNT(*)
            FROM appointment_slots s
            LEFT JOIN providers p ON p.provider_id = s.provider_id
            WHERE p.provider_id IS NULL
            """
        ).fetchone()[0]
        network_orphans = conn.execute(
            """
            SELECT COUNT(*)
            FROM provider_networks n
            LEFT JOIN providers p ON p.provider_id = n.provider_id
            WHERE p.provider_id IS NULL
            """
        ).fetchone()[0]
        assert slot_orphans == 0
        assert network_orphans == 0
    finally:
        conn.close()


def test_fanout_fixture_fails_slot_uniqueness_and_does_not_change_metric():
    """The naive join fails a slot_id uniqueness check. get_metric does not read it."""
    current = get_metric("available_appointments", state="TX", payer_network="Aetna", period="last_week")
    conn = _connect()
    try:
        naive_rows, naive_distinct, naive_bookable = conn.execute(
            """
            SELECT
                COUNT(*),
                COUNT(DISTINCT g.slot_id),
                COALESCE(SUM(g.is_bookable), 0)
            FROM gold_provider_availability_daily g
            JOIN provider_networks n
              ON n.provider_id = g.provider_id
             AND n.payer_network = g.payer_network
            WHERE g.state = ? AND g.payer_network = ?
              AND date(g.slot_date) BETWEEN date(?) AND date(?)
            """,
            ("TX", "Aetna", current.period_start, current.period_end),
        ).fetchone()
    finally:
        conn.close()

    uniqueness_passed = naive_rows == naive_distinct
    assert uniqueness_passed is False
    assert naive_rows > naive_distinct
    assert int(naive_bookable) > current.value
    assert current.value == 24
    assert current.source == "gold_provider_availability_daily"
    assert metric_definition("available_appointments")["consumers"] == ["careops.metrics.get_metric"]
