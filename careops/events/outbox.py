"""One transaction writes the committed change and the outbox row."""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

from careops.events.schema import SchemaError, validate_event
from careops.paths import ROOT

CHANGES_PATH = ROOT / "careops_changes.db"


class OutboxWriteError(Exception):
    pass


class BrokerUnavailable(Exception):
    def __init__(self):
        super().__init__("broker_unavailable")


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.isolation_level = None
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS committed_changes (
            change_id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            subject_key TEXT NOT NULL,
            state_json TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            event_id TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS outbox (
            event_id TEXT PRIMARY KEY,
            event_json TEXT NOT NULL,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL,
            last_error TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS deliveries (
            event_id TEXT NOT NULL,
            consumer TEXT NOT NULL,
            status TEXT NOT NULL,
            detail TEXT,
            PRIMARY KEY (event_id, consumer)
        )
        """
    )
    return conn


class ChangeStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else CHANGES_PATH

    def commit(self, event: dict, *, fail_outbox: bool = False) -> dict:
        validate_event(event)
        change_id = str(uuid.uuid4())
        subject_key = "|".join(event["subject_refs"])
        state = {
            "change_id": change_id,
            "event_id": event["event_id"],
            "event_type": event["event_type"],
            "organization_id": event["organization_id"],
            "subject_refs": event["subject_refs"],
            "occurred_at": event["occurred_at"],
            "payload": event["payload"],
        }
        conn = _connect(self.path)
        try:
            conn.execute("BEGIN")
            conn.execute(
                """
                INSERT INTO committed_changes (
                    change_id, organization_id, subject_key, state_json, occurred_at, event_id
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    change_id,
                    event["organization_id"],
                    subject_key,
                    json.dumps(state, sort_keys=True),
                    event["occurred_at"],
                    event["event_id"],
                ),
            )
            if fail_outbox:
                raise OutboxWriteError("outbox_failed")
            conn.execute(
                """
                INSERT INTO outbox (event_id, event_json, status, attempts, last_error)
                VALUES (?, ?, 'pending', 0, NULL)
                """,
                (event["event_id"], json.dumps(event, sort_keys=True)),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        return {"committed": True, "change_id": change_id, "event_id": event["event_id"], "published": False}

    def counts(self) -> dict:
        conn = _connect(self.path)
        try:
            changes = conn.execute("SELECT COUNT(*) AS n FROM committed_changes").fetchone()["n"]
            outbox = conn.execute("SELECT COUNT(*) AS n FROM outbox").fetchone()["n"]
            return {"changes": changes, "outbox": outbox}
        finally:
            conn.close()

    def outbox_row(self, event_id: str) -> dict | None:
        conn = _connect(self.path)
        try:
            row = conn.execute("SELECT * FROM outbox WHERE event_id = ?", (event_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def pending(self) -> list[dict]:
        conn = _connect(self.path)
        try:
            rows = conn.execute(
                "SELECT event_json, attempts FROM outbox WHERE status = 'pending' ORDER BY event_id"
            ).fetchall()
            return [{"event": json.loads(row["event_json"]), "attempts": row["attempts"]} for row in rows]
        finally:
            conn.close()

    def events(self) -> list[dict]:
        conn = _connect(self.path)
        try:
            rows = conn.execute("SELECT event_json FROM outbox ORDER BY event_id").fetchall()
            return [json.loads(row["event_json"]) for row in rows]
        finally:
            conn.close()

    def record_delivery(self, event_id: str, consumer: str, status: str, detail: str | None) -> None:
        conn = _connect(self.path)
        try:
            conn.execute(
                """
                INSERT INTO deliveries (event_id, consumer, status, detail)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(event_id, consumer) DO UPDATE SET status = excluded.status, detail = excluded.detail
                """,
                (event_id, consumer, status, detail),
            )
        finally:
            conn.close()

    def delivery(self, event_id: str, consumer: str) -> dict | None:
        conn = _connect(self.path)
        try:
            row = conn.execute(
                "SELECT * FROM deliveries WHERE event_id = ? AND consumer = ?",
                (event_id, consumer),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def mark_attempt(self, event_id: str, *, status: str, error: str | None) -> None:
        conn = _connect(self.path)
        try:
            conn.execute(
                """
                UPDATE outbox
                SET status = ?, attempts = attempts + 1, last_error = ?
                WHERE event_id = ?
                """,
                (status, error, event_id),
            )
        finally:
            conn.close()


def publish_pending(store: ChangeStore, broker) -> list[dict]:
    """Deliver pending outbox rows. A down broker leaves the row pending."""
    results = []
    for item in store.pending():
        event = item["event"]
        try:
            outcome = broker.deliver(event)
        except BrokerUnavailable:
            store.mark_attempt(event["event_id"], status="pending", error="broker_unavailable")
            results.append({"event_id": event["event_id"], "published": False, "error": "broker_unavailable"})
            continue
        except SchemaError as exc:
            store.mark_attempt(event["event_id"], status="dead_letter", error=exc.code)
            results.append({"event_id": event["event_id"], "published": False, "error": exc.code})
            continue
        for row in outcome:
            store.record_delivery(
                event["event_id"],
                row["consumer"],
                "applied" if row["status"] == "applied" else "failed",
                row.get("error"),
            )
        failed = [row for row in outcome if row["status"] == "failed"]
        if failed:
            store.mark_attempt(event["event_id"], status="pending", error="consumer_unavailable")
            results.append({
                "event_id": event["event_id"],
                "published": False,
                "error": "consumer_unavailable",
                "consumers": outcome,
            })
            continue
        store.mark_attempt(event["event_id"], status="published", error=None)
        results.append({"event_id": event["event_id"], "published": True, "consumers": outcome})
    return results
