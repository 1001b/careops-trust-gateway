"""In-process transport and the three bounded consumers. Delivery is at-least-once."""

from __future__ import annotations

import json
import logging

from careops.events.outbox import BrokerUnavailable
from careops.events.schema import SchemaError, validate_event

LOG = logging.getLogger("careops.events")
_DROPPED = {"answer", "credential_notes", "evidence", "provider_name", "secret", "token"}


def _redact(payload: dict) -> dict:
    return {key: value for key, value in payload.items() if key not in _DROPPED}


def subject_key(event: dict) -> str:
    return "|".join(event["subject_refs"])


class InProcessBroker:
    """Deterministic stand-in. A failed consumer does not stop the others."""

    def __init__(self, consumers: list | None = None, *, available: bool = True):
        self.consumers = list(consumers or [])
        self.available = available

    def deliver(self, event: dict) -> list[dict]:
        if not self.available:
            raise BrokerUnavailable()
        validate_event(event)
        outcomes = []
        for consumer in self.consumers:
            try:
                consumer.handle(event)
            except Exception as exc:
                outcomes.append({"consumer": consumer.name, "status": "failed", "error": exc.__class__.__name__})
                continue
            outcomes.append({"consumer": consumer.name, "status": "applied"})
        return outcomes


class AuditConsumer:
    name = "audit"

    def __init__(self):
        self.records: list[dict] = []
        self._seen: set[str] = set()

    def handle(self, event: dict) -> None:
        if event["event_id"] in self._seen:
            return
        self._seen.add(event["event_id"])
        record = {
            "event_id": event["event_id"],
            "event_type": event["event_type"],
            "event_version": event["event_version"],
            "organization_id": event["organization_id"],
            "trace_id": event["trace_id"],
            "correlation_id": event["correlation_id"],
            "causation_id": event["causation_id"],
            "payload": _redact(event["payload"]),
        }
        self.records.append(record)
        LOG.info("%s", json.dumps(record, sort_keys=True))


class ProjectionConsumer:
    """Current view per organization and subject. Older events stay in history."""

    name = "projection"

    def __init__(self):
        self.current: dict[tuple[str, str], dict] = {}
        self.history: list[dict] = []
        self._seen: set[str] = set()

    def handle(self, event: dict) -> None:
        if event["event_id"] in self._seen:
            return
        self._seen.add(event["event_id"])
        retained = {
            "event_id": event["event_id"],
            "event_type": event["event_type"],
            "organization_id": event["organization_id"],
            "subject_refs": list(event["subject_refs"]),
            "occurred_at": event["occurred_at"],
            "trace_id": event["trace_id"],
            "correlation_id": event["correlation_id"],
            "causation_id": event["causation_id"],
            "payload": _redact(event["payload"]),
        }
        self.history.append(retained)
        key = (event["organization_id"], subject_key(event))
        current = self.current.get(key)
        if current is None or event["occurred_at"] >= current["occurred_at"]:
            self.current[key] = retained

    def for_organization(self, organization_id: str) -> list[dict]:
        return [row for (org, _subject), row in self.current.items() if org == organization_id]


class EpisodeStore:
    """Local ContextEpisode seam. This is not a graph store."""

    name = "temporal_context"

    def __init__(self):
        self.episodes: list[dict] = []
        self._seen: set[str] = set()

    def handle(self, event: dict) -> None:
        if event["event_id"] in self._seen:
            return
        self._seen.add(event["event_id"])
        self.episodes.append(
            {
                "episode_id": event["event_id"],
                "organization_id": event["organization_id"],
                "event_type": event["event_type"],
                "reference_time": event["occurred_at"],
                "observed_at": event["observed_at"],
                "source_type": "domain_event",
                "source_id": event["event_id"],
                "subject_refs": list(event["subject_refs"]),
                "classification": event["classification"],
                "validity_type": "COMMITTED",
                "trace_id": event["trace_id"],
                "correlation_id": event["correlation_id"],
                "causation_id": event["causation_id"],
            }
        )

    def for_organization(self, organization_id: str) -> list[dict]:
        return [row for row in self.episodes if row["organization_id"] == organization_id]


def replay_projection(events: list[dict]) -> ProjectionConsumer:
    """Rebuild the current projection from history. Arrival order does not matter."""
    projection = ProjectionConsumer()
    ordered = sorted(events, key=lambda event: (event["occurred_at"], event["event_id"]))
    for event in ordered:
        projection.handle(event)
    return projection


class DeadLetter:
    def __init__(self):
        self.items: list[dict] = []

    def add(self, event: dict, reason: str) -> None:
        self.items.append(
            {
                "event_id": event.get("event_id"),
                "event_type": event.get("event_type"),
                "reason": reason,
                "trace_id": event.get("trace_id"),
                "correlation_id": event.get("correlation_id"),
                "causation_id": event.get("causation_id"),
            }
        )


def dispatch(events: list[dict], consumers: list, dead_letter: DeadLetter) -> list[dict]:
    """Validate, then fan out. One bad event does not stop the next event."""
    outcomes = []
    for event in events:
        try:
            validate_event(event)
        except SchemaError as exc:
            dead_letter.add(event, exc.code)
            outcomes.append({"event_id": event.get("event_id"), "status": "dead_letter", "reason": exc.code})
            continue
        consumer_results = []
        for consumer in consumers:
            try:
                consumer.handle(event)
            except Exception as exc:
                consumer_results.append({"consumer": consumer.name, "status": "failed", "error": exc.__class__.__name__})
                continue
            consumer_results.append({"consumer": consumer.name, "status": "applied"})
        outcomes.append({"event_id": event["event_id"], "status": "delivered", "consumers": consumer_results})
    return outcomes
