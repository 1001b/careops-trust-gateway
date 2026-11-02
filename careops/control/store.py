"""Durable workflow ledger. Nothing in this module writes the metric or the event outbox."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from careops.control.errors import ControlError
from careops.control.lineage import make_finding, repeat_finding, verify_finding
from careops.control.loop import loop_signature
from careops.control.registry import resolve_capability
from careops.control.schema import OPEN_STATUSES, PROPOSAL_TRANSITIONS, WORKFLOW_STATUSES
from careops.paths import ROOT

CONTROL_PATH = ROOT / "careops_control.db"

_WORKFLOW_FIELDS = (
    "workflow_id",
    "workflow_version",
    "status",
    "authority_snapshot_version",
    "active_tasks",
    "completed_tasks",
    "proposal_ids",
    "model_budget",
    "tool_budget",
    "delegation_count",
    "created_at",
    "updated_at",
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.isolation_level = None
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS workflows (
            workflow_id TEXT PRIMARY KEY,
            body_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            body_json TEXT NOT NULL,
            UNIQUE (workflow_id, idempotency_key)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS task_results (
            task_id TEXT PRIMARY KEY,
            body_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS proposals (
            proposal_id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL,
            body_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS decisions (
            decision_id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL,
            body_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS findings (
            finding_id TEXT PRIMARY KEY,
            body_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS citations (
            citation_id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL,
            body_json TEXT NOT NULL
        )
        """
    )
    return conn


def _load(conn: sqlite3.Connection, table: str, column: str, value: str) -> dict | None:
    row = conn.execute(f"SELECT body_json FROM {table} WHERE {column} = ?", (value,)).fetchone()
    if row is None:
        return None
    return json.loads(row["body_json"])


def _save(conn: sqlite3.Connection, table: str, column: str, value: str, body: dict, extra: tuple = ()) -> None:
    payload = json.dumps(body, sort_keys=True)
    if table == "workflows":
        conn.execute(
            """
            INSERT INTO workflows (workflow_id, body_json) VALUES (?, ?)
            ON CONFLICT(workflow_id) DO UPDATE SET body_json = excluded.body_json
            """,
            (value, payload),
        )
        return
    if table == "tasks":
        workflow_id, idempotency_key = extra
        conn.execute(
            """
            INSERT INTO tasks (task_id, workflow_id, idempotency_key, body_json)
            VALUES (?, ?, ?, ?)
            """,
            (value, workflow_id, idempotency_key, payload),
        )
        return
    if table == "task_results":
        conn.execute(
            "INSERT INTO task_results (task_id, body_json) VALUES (?, ?)",
            (value, payload),
        )
        return
    if table == "proposals":
        conn.execute(
            """
            INSERT INTO proposals (proposal_id, workflow_id, body_json) VALUES (?, ?, ?)
            ON CONFLICT(proposal_id) DO UPDATE SET body_json = excluded.body_json
            """,
            (value, extra[0], payload),
        )
        return
    if table == "decisions":
        conn.execute(
            "INSERT INTO decisions (decision_id, workflow_id, body_json) VALUES (?, ?, ?)",
            (value, extra[0], payload),
        )
        return
    if table == "findings":
        conn.execute(
            """
            INSERT INTO findings (finding_id, body_json) VALUES (?, ?)
            ON CONFLICT(finding_id) DO UPDATE SET body_json = excluded.body_json
            """,
            (value, payload),
        )
        return
    if table == "citations":
        conn.execute(
            "INSERT INTO citations (citation_id, workflow_id, body_json) VALUES (?, ?, ?)",
            (value, extra[0], payload),
        )
        return
    raise ControlError("store_unknown")


class ControlStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else CONTROL_PATH

    def _transaction(self, fn):
        conn = _connect(self.path)
        try:
            conn.execute("BEGIN")
            result = fn(conn)
            conn.execute("COMMIT")
            return result
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def open_workflow(
        self,
        *,
        authority_snapshot_version: str,
        model_budget: int = 2,
        tool_budget: int = 2,
        max_delegation_depth: int = 2,
        loop_threshold: int = 2,
        organization_id: str = "tenant-demo",
    ) -> dict:
        now = _now()
        workflow = {
            "workflow_id": str(uuid.uuid4()),
            "workflow_version": 1,
            "status": "RUNNING",
            "authority_snapshot_version": authority_snapshot_version,
            "active_tasks": [],
            "completed_tasks": [],
            "proposal_ids": [],
            "model_budget": int(model_budget),
            "tool_budget": int(tool_budget),
            "delegation_count": 0,
            "max_delegation_depth": int(max_delegation_depth),
            "loop_threshold": int(loop_threshold),
            "signature_counts": {},
            "organization_id": organization_id,
            "domain_mutated": False,
            "created_at": now,
            "updated_at": now,
        }
        missing = [field for field in _WORKFLOW_FIELDS if field not in workflow]
        if missing:
            raise ControlError("workflow_incomplete")

        def write(conn):
            _save(conn, "workflows", "workflow_id", workflow["workflow_id"], workflow)
            return workflow

        return self._transaction(write)

    def get_workflow(self, workflow_id: str) -> dict:
        conn = _connect(self.path)
        try:
            workflow = _load(conn, "workflows", "workflow_id", workflow_id)
        finally:
            conn.close()
        if workflow is None:
            raise ControlError("workflow_unknown")
        return workflow

    def get_task(self, task_id: str) -> dict:
        conn = _connect(self.path)
        try:
            task = _load(conn, "tasks", "task_id", task_id)
        finally:
            conn.close()
        if task is None:
            raise ControlError("task_unknown")
        return task

    def get_proposal(self, proposal_id: str) -> dict:
        conn = _connect(self.path)
        try:
            proposal = _load(conn, "proposals", "proposal_id", proposal_id)
        finally:
            conn.close()
        if proposal is None:
            raise ControlError("proposal_unknown")
        return proposal

    def get_decision(self, decision_id: str) -> dict:
        conn = _connect(self.path)
        try:
            decision = _load(conn, "decisions", "decision_id", decision_id)
        finally:
            conn.close()
        if decision is None:
            raise ControlError("decision_unknown")
        return decision

    def get_finding(self, finding_id: str) -> dict:
        conn = _connect(self.path)
        try:
            finding = _load(conn, "findings", "finding_id", finding_id)
        finally:
            conn.close()
        if finding is None:
            raise ControlError("finding_unknown")
        return finding

    def set_status(self, workflow_id: str, expected_version: int, status: str) -> dict:
        if status not in WORKFLOW_STATUSES or status in {"STALE", "BUDGET_EXCEEDED", "LOOP_DETECTED"}:
            raise ControlError("status_unknown")

        def write(conn):
            workflow = self._locked(conn, workflow_id, expected_version)
            if workflow["status"] not in OPEN_STATUSES:
                raise ControlError("workflow_closed")
            workflow["status"] = status
            self._touch(workflow)
            _save(conn, "workflows", "workflow_id", workflow_id, workflow)
            return workflow

        return self._transaction(write)

    def create_task(
        self,
        workflow_id: str,
        *,
        expected_version: int,
        idempotency_key: str,
        requested_capability: str,
        objective: str,
        evidence_refs: list[str] | None = None,
        context_refs: list[str] | None = None,
        delegation_depth: int = 0,
        parent_task_id: str = "",
        deadline: str = "",
        correlation_id: str = "",
        causation_id: str = "",
    ) -> dict:
        capability = resolve_capability(requested_capability)
        refs = list(evidence_refs or [])

        def write(conn):
            existing = conn.execute(
                "SELECT body_json FROM tasks WHERE workflow_id = ? AND idempotency_key = ?",
                (workflow_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                return ("task", json.loads(existing["body_json"]))
            workflow = self._locked(conn, workflow_id, expected_version)
            if workflow["status"] not in OPEN_STATUSES:
                raise ControlError("workflow_closed")
            if int(delegation_depth) > int(workflow["max_delegation_depth"]):
                raise ControlError("delegation_depth_exceeded")
            signature = loop_signature(objective=objective, evidence_refs=refs, status=workflow["status"])
            seen = int(workflow["signature_counts"].get(signature, 0))
            if seen >= int(workflow["loop_threshold"]):
                workflow["status"] = "LOOP_DETECTED"
                self._touch(workflow)
                _save(conn, "workflows", "workflow_id", workflow_id, workflow)
                return ("loop", workflow)
            workflow["signature_counts"][signature] = seen + 1
            task = {
                "workflow_id": workflow_id,
                "workflow_version": workflow["workflow_version"],
                "task_id": str(uuid.uuid4()),
                "parent_task_id": parent_task_id,
                "requested_capability": capability["requested_capability"],
                "assigned_agent": capability["assigned_agent"],
                "objective": objective,
                "authority_snapshot_version": workflow["authority_snapshot_version"],
                "evidence_refs": refs,
                "context_refs": list(context_refs or []),
                "allowed_tools": capability["allowed_tools"],
                "budget": {"model": workflow["model_budget"], "tool": workflow["tool_budget"]},
                "delegation_depth": int(delegation_depth),
                "deadline": deadline,
                "idempotency_key": idempotency_key,
                "correlation_id": correlation_id,
                "causation_id": causation_id,
                "status": "OPEN",
                "domain_mutated": False,
            }
            _save(conn, "tasks", "task_id", task["task_id"], task, (workflow_id, idempotency_key))
            workflow["active_tasks"].append(task["task_id"])
            if int(delegation_depth) > 0:
                workflow["delegation_count"] += 1
            self._touch(workflow)
            _save(conn, "workflows", "workflow_id", workflow_id, workflow)
            return ("task", task)

        kind, payload = self._transaction(write)
        if kind == "loop":
            raise ControlError("loop_detected")
        return payload

    def ingest_result(self, task_id: str, *, expected_version: int, body: dict) -> dict:
        """Apply one task result. A second ingest returns the first transition."""

        def write(conn):
            prior = _load(conn, "task_results", "task_id", task_id)
            if prior is not None:
                return prior
            task = _load(conn, "tasks", "task_id", task_id)
            if task is None:
                raise ControlError("task_unknown")
            workflow = self._locked(conn, task["workflow_id"], expected_version)
            stored = {
                "task_id": task_id,
                "agent_id": task["assigned_agent"],
                "status": "COMPLETED",
                "finding_refs": list(body.get("finding_refs") or []),
                "evidence_refs": list(body.get("evidence_refs") or []),
                "context_refs_produced": list(body.get("context_refs_produced") or []),
                "proposals": list(body.get("proposals") or []),
                "confidence": body.get("confidence"),
                "requires_review": bool(body.get("requires_review")),
                "delegation_request": body.get("delegation_request"),
                "completed_at": _now(),
                "domain_mutated": False,
            }
            _save(conn, "task_results", "task_id", task_id, stored)
            task["status"] = "COMPLETED"
            conn.execute(
                "UPDATE tasks SET body_json = ? WHERE task_id = ?",
                (json.dumps(task, sort_keys=True), task_id),
            )
            workflow["active_tasks"] = [item for item in workflow["active_tasks"] if item != task_id]
            workflow["completed_tasks"].append(task_id)
            self._touch(workflow)
            _save(conn, "workflows", "workflow_id", workflow["workflow_id"], workflow)
            return stored

        return self._transaction(write)

    def consume_budget(self, workflow_id: str, *, expected_version: int, kind: str, calls: int = 1) -> dict:
        field = {"model": "model_budget", "tool": "tool_budget"}.get(kind)
        if field is None:
            raise ControlError("budget_unknown")

        def write(conn):
            workflow = self._locked(conn, workflow_id, expected_version)
            if workflow["status"] == "BUDGET_EXCEEDED":
                raise ControlError("budget_exceeded")
            if workflow["status"] not in OPEN_STATUSES:
                raise ControlError("workflow_closed")
            if int(workflow[field]) < int(calls):
                workflow["status"] = "BUDGET_EXCEEDED"
                self._touch(workflow)
                _save(conn, "workflows", "workflow_id", workflow_id, workflow)
                return ("exceeded", workflow)
            workflow[field] = int(workflow[field]) - int(calls)
            self._touch(workflow)
            _save(conn, "workflows", "workflow_id", workflow_id, workflow)
            return ("ok", workflow)

        kind_out, workflow = self._transaction(write)
        if kind_out == "exceeded":
            raise ControlError("budget_exceeded")
        return workflow

    def create_proposal(
        self,
        workflow_id: str,
        *,
        expected_version: int,
        proposed_by: str,
        target_entity: str,
        action: str,
        expected_entity_version: str,
        evidence_refs: list[str] | None = None,
    ) -> dict:
        def write(conn):
            workflow = self._locked(conn, workflow_id, expected_version)
            if workflow["status"] not in OPEN_STATUSES:
                raise ControlError("workflow_closed")
            now = _now()
            proposal = {
                "proposal_id": str(uuid.uuid4()),
                "workflow_id": workflow_id,
                "proposed_by": proposed_by,
                "target_entity": target_entity,
                "action": action,
                "expected_entity_version": str(expected_entity_version),
                "evidence_refs": list(evidence_refs or []),
                "status": "DRAFT",
                "domain_mutated": False,
                "created_at": now,
                "updated_at": now,
            }
            _save(conn, "proposals", "proposal_id", proposal["proposal_id"], proposal, (workflow_id,))
            workflow["proposal_ids"].append(proposal["proposal_id"])
            self._touch(workflow)
            _save(conn, "workflows", "workflow_id", workflow_id, workflow)
            return proposal

        return self._transaction(write)

    def transition_proposal(
        self,
        proposal_id: str,
        new_status: str,
        *,
        expected_workflow_version: int,
        current_entity_version: str | None = None,
    ) -> dict:
        """Move the ledger. A matching entity version still does not write domain state."""

        def write(conn):
            proposal = _load(conn, "proposals", "proposal_id", proposal_id)
            if proposal is None:
                raise ControlError("proposal_unknown")
            workflow = self._locked(conn, proposal["workflow_id"], expected_workflow_version)
            allowed = PROPOSAL_TRANSITIONS[proposal["status"]]
            if new_status not in allowed:
                raise ControlError("proposal_transition_invalid")
            status = new_status
            if new_status in {"COMMITTING", "COMMITTED"}:
                if current_entity_version is None:
                    raise ControlError("entity_version_required")
                if str(current_entity_version) != proposal["expected_entity_version"]:
                    status = "STALE"
                    workflow["status"] = "STALE"
            proposal["status"] = status
            proposal["domain_mutated"] = False
            proposal["updated_at"] = _now()
            _save(conn, "proposals", "proposal_id", proposal_id, proposal, (proposal["workflow_id"],))
            self._touch(workflow)
            _save(conn, "workflows", "workflow_id", workflow["workflow_id"], workflow)
            return proposal

        return self._transaction(write)

    def record_decision(
        self,
        workflow_id: str,
        *,
        expected_version: int,
        question: str,
        available_options: list[str],
        selected_option: str,
        evidence_refs: list[str] | None = None,
    ) -> dict:
        def write(conn):
            workflow = self._locked(conn, workflow_id, expected_version)
            decision = {
                "decision_id": str(uuid.uuid4()),
                "workflow_id": workflow_id,
                "question": question,
                "available_options": list(available_options),
                "selected_option": selected_option,
                "evidence_refs": list(evidence_refs or []),
                "workflow_version": workflow["workflow_version"],
                "created_at": _now(),
                "authoritative": False,
                "domain_mutated": False,
            }
            _save(conn, "decisions", "decision_id", decision["decision_id"], decision, (workflow_id,))
            self._touch(workflow)
            _save(conn, "workflows", "workflow_id", workflow_id, workflow)
            return decision

        return self._transaction(write)

    def record_finding(self, **fields) -> dict:
        finding = make_finding(**fields)

        def write(conn):
            _save(conn, "findings", "finding_id", finding["finding_id"], finding)
            return finding

        return self._transaction(write)

    def repeat_claim(self, finding_id: str, *, producer: str) -> dict:
        def write(conn):
            existing = _load(conn, "findings", "finding_id", finding_id)
            if existing is None:
                raise ControlError("finding_unknown")
            repeated = repeat_finding(existing, producer=producer)
            _save(conn, "findings", "finding_id", repeated["finding_id"], repeated)
            return repeated

        return self._transaction(write)

    def verify_claim(self, finding_id: str) -> dict:
        def write(conn):
            existing = _load(conn, "findings", "finding_id", finding_id)
            if existing is None:
                raise ControlError("finding_unknown")
            verified = verify_finding(existing)
            _save(conn, "findings", "finding_id", finding_id, verified)
            return verified

        return self._transaction(write)

    def cite_context(self, workflow_id: str, expected_version: int, episode: dict) -> dict:
        """Record a context episode as provenance. The citation does not adopt domain truth."""

        def write(conn):
            workflow = self._locked(conn, workflow_id, expected_version)
            citation = {
                "citation_id": str(uuid.uuid4()),
                "workflow_id": workflow_id,
                "episode_id": episode["episode_id"],
                "validity_type": episode["validity_type"],
                "authority": False,
                "store_authority": False,
                "provenance": "context_plane",
                "domain_mutated": False,
            }
            _save(conn, "citations", "citation_id", citation["citation_id"], citation, (workflow_id,))
            self._touch(workflow)
            _save(conn, "workflows", "workflow_id", workflow_id, workflow)
            return citation

        return self._transaction(write)

    def _locked(self, conn: sqlite3.Connection, workflow_id: str, expected_version: int) -> dict:
        workflow = _load(conn, "workflows", "workflow_id", workflow_id)
        if workflow is None:
            raise ControlError("workflow_unknown")
        if int(workflow["workflow_version"]) != int(expected_version):
            raise ControlError("stale_workflow")
        return workflow

    def _touch(self, workflow: dict) -> None:
        workflow["workflow_version"] = int(workflow["workflow_version"]) + 1
        workflow["updated_at"] = _now()
        workflow["domain_mutated"] = False
