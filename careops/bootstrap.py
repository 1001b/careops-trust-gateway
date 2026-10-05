from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from .paths import DB_PATH, RAW_DIR

TABLES = {
    "providers": ("providers.csv", """
        CREATE TABLE providers (
            provider_id TEXT PRIMARY KEY,
            provider_name TEXT NOT NULL,
            state TEXT NOT NULL,
            active_from TEXT NOT NULL,
            inactive_from TEXT
        )
    """),
    "provider_networks": ("provider_networks.csv", """
        CREATE TABLE provider_networks (
            provider_id TEXT NOT NULL,
            payer_network TEXT NOT NULL,
            effective_from TEXT NOT NULL,
            effective_to TEXT,
            status TEXT NOT NULL
        )
    """),
    "appointment_slots": ("appointment_slots.csv", """
        CREATE TABLE appointment_slots (
            slot_id TEXT PRIMARY KEY,
            provider_id TEXT NOT NULL,
            slot_date TEXT NOT NULL,
            payer_network TEXT NOT NULL,
            status TEXT NOT NULL
        )
    """),
}


def _none_if_blank(value: str):
    return None if value == "" else value


def bootstrap(db_path: Path = DB_PATH) -> Path:
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(db_path)
    try:
        for table, (filename, ddl) in TABLES.items():
            conn.execute(ddl)
            with (RAW_DIR / filename).open(newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                cols = reader.fieldnames or []
                placeholders = ",".join("?" for _ in cols)
                col_sql = ",".join(cols)
                conn.executemany(
                    f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders})",
                    [[_none_if_blank(row[c]) for c in cols] for row in rows],
                )

        conn.execute("DROP VIEW IF EXISTS gold_provider_availability_daily")
        conn.execute("""
            CREATE VIEW gold_provider_availability_daily AS
            SELECT
                s.slot_id,
                s.provider_id,
                p.provider_name,
                p.state,
                s.slot_date,
                s.payer_network,
                CASE
                    WHEN s.status = 'open'
                     AND date(s.slot_date) >= date(p.active_from)
                     AND (p.inactive_from IS NULL OR date(s.slot_date) < date(p.inactive_from))
                     AND EXISTS (
                         SELECT 1
                         FROM provider_networks n
                         WHERE n.provider_id = s.provider_id
                           AND n.payer_network = s.payer_network
                           AND n.status = 'eligible'
                           AND date(s.slot_date) >= date(n.effective_from)
                           AND (n.effective_to IS NULL OR date(s.slot_date) <= date(n.effective_to))
                     )
                    THEN 1 ELSE 0
                END AS is_bookable
            FROM appointment_slots s
            JOIN providers p ON p.provider_id = s.provider_id
        """)
        conn.commit()
    finally:
        conn.close()
    return db_path


if __name__ == "__main__":
    path = bootstrap()
    print(f"Bootstrapped synthetic demo database: {path}")
