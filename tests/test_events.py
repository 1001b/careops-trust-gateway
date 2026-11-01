"""TC-06: outbox, in-process delivery, and the three bounded consumers."""

from __future__ import annotations

import json
import logging

import pytest

from careops.events import (
    AuditConsumer,
    ChangeStore,
    DeadLetter,
    EpisodeStore,
    InProcessBroker,
    OutboxWriteError,
    ProjectionConsumer,
    SchemaError,
    build_event,
    dispatch,
    publish_pending,
    replay_projection,
)
from careops.identity import IdentityConfig, issue_token
from careops.metrics import get_metric
from careops.protected import protected_query

CONFIG = IdentityConfig(secret="test-secret")
QUESTION = "Why did in-network appointment availability in Texas decline last week?"


def _token() -> str:
    return issue_token(
        CONFIG,
        principal="analyst-1",
        role="analyst",
        tenant="tenant-demo",
        purpose="operations_analysis",
    )


def _network(
    org: str = "tenant-demo",
    *,
    when: str,
    status: str,
    event_id: str,
    version: int = 1,
    extra: dict | None = None,
):
    payload = {"provider_id": "P002", "payer_network": "Aetna", "to_status": status}
    if extra:
        payload.update(extra)
    return build_event(
        event_type="provider.network_status_changed",
        organization_id=org,
        subject_refs=["provider:P002", "payer_network:Aetna"],
        payload=payload,
        occurred_at=when,
        observed_at="2026-10-05T12:00:00+00:00",
        trace_id="trace-net",
        correlation_id="corr-net",
        causation_id="cause-net",
        event_version=version,
        event_id=event_id,
    )


class _Down:
    name = "down"

    def handle(self, _event):
        raise RuntimeError("consumer_unavailable")


def test_state_and_outbox_commit_together_or_not_at_all(tmp_path):
    store = ChangeStore(tmp_path / "changes.db")
    event = _network(when="2026-10-02T00:00:00+00:00", status="pending", event_id="evt-1")
    with pytest.raises(OutboxWriteError):
        store.commit(event, fail_outbox=True)
    assert store.counts() == {"changes": 0, "outbox": 0}
    committed = store.commit(event)
    assert committed["committed"] is True
    assert committed["published"] is False
    assert store.counts() == {"changes": 1, "outbox": 1}
    assert store.outbox_row("evt-1")["status"] == "pending"


def test_duplicate_delivery_applies_once():
    event = _network(when="2026-10-02T00:00:00+00:00", status="pending", event_id="evt-dup")
    projection = ProjectionConsumer()
    audit = AuditConsumer()
    projection.handle(event)
    projection.handle(event)
    audit.handle(event)
    audit.handle(event)
    assert len(projection.history) == 1
    assert len(projection.current) == 1
    assert len(audit.records) == 1
    assert audit.records[0]["trace_id"] == "trace-net"
    assert audit.records[0]["correlation_id"] == "corr-net"
    assert audit.records[0]["causation_id"] == "cause-net"


def test_older_event_does_not_regress_the_current_projection():
    newer = _network(when="2026-10-02T00:00:00+00:00", status="pending", event_id="evt-new")
    older = _network(when="2026-10-01T00:00:00+00:00", status="eligible", event_id="evt-old")
    projection = ProjectionConsumer()
    projection.handle(newer)
    projection.handle(older)
    current = projection.for_organization("tenant-demo")
    assert len(current) == 1
    assert current[0]["payload"]["to_status"] == "pending"
    assert len(projection.history) == 2
    assert {row["event_id"] for row in projection.history} == {"evt-new", "evt-old"}
    rebuilt = replay_projection([older, newer])
    again = replay_projection([newer, older])
    assert rebuilt.current == projection.current == again.current


def test_unsupported_version_is_dead_lettered_and_the_next_event_proceeds(tmp_path):
    bad = _network(when="2026-10-02T00:00:00+00:00", status="pending", event_id="evt-bad", version=2)
    good = _network(when="2026-10-03T00:00:00+00:00", status="eligible", event_id="evt-good")
    store = ChangeStore(tmp_path / "changes.db")
    with pytest.raises(SchemaError) as caught:
        store.commit(bad)
    assert caught.value.code == "event_version_unsupported"
    assert store.counts() == {"changes": 0, "outbox": 0}
    projection = ProjectionConsumer()
    dead = DeadLetter()
    outcomes = dispatch([bad, good], [projection], dead)
    assert outcomes[0]["status"] == "dead_letter"
    assert outcomes[0]["reason"] == "event_version_unsupported"
    assert outcomes[1]["status"] == "delivered"
    assert [row["event_id"] for row in projection.history] == ["evt-good"]
    assert dead.items[0]["trace_id"] == "trace-net"


def test_a_down_consumer_leaves_the_commit_and_lets_the_other_apply(tmp_path):
    store = ChangeStore(tmp_path / "changes.db")
    event = _network(when="2026-10-02T00:00:00+00:00", status="pending", event_id="evt-down")
    audit = AuditConsumer()
    broker = InProcessBroker([_Down(), audit])
    committed = store.commit(event)
    first = publish_pending(store, broker)
    row = store.outbox_row("evt-down")
    assert committed["committed"] is True
    assert first[0]["published"] is False
    assert row["status"] == "pending"
    assert row["attempts"] == 1
    assert row["last_error"] == "consumer_unavailable"
    assert store.delivery("evt-down", "audit")["status"] == "applied"
    assert store.delivery("evt-down", "down")["status"] == "failed"
    assert len(audit.records) == 1
    publish_pending(store, broker)
    assert store.outbox_row("evt-down")["attempts"] == 2
    assert len(audit.records) == 1


def test_broker_outage_keeps_the_query_path_and_does_not_report_success(tmp_path):
    store = ChangeStore(tmp_path / "changes.db")
    event = _network(when="2026-10-02T00:00:00+00:00", status="pending", event_id="evt-broker")
    broker = InProcessBroker([AuditConsumer()], available=False)
    store.commit(event)
    result = publish_pending(store, broker)
    row = store.outbox_row("evt-broker")
    assert result[0]["published"] is False
    assert result[0]["error"] == "broker_unavailable"
    assert row["status"] == "pending"
    assert row["attempts"] == 1
    assert "success" not in json.dumps(result)
    assert get_metric("available_appointments", state="TX", payer_network="Aetna", period="prior_week").value == 42
    assert get_metric("available_appointments", state="TX", payer_network="Aetna", period="last_week").value == 24
    protected = protected_query(_token(), QUESTION, config=CONFIG)
    metric = next(item for item in protected["evidence"] if item["type"] == "metric")
    assert metric["comparison"]["prior"]["value"] == 42
    assert metric["comparison"]["current"]["value"] == 24
    assert protected["outcome"] == "allow"


def test_replay_rebuilds_the_same_current_state():
    first = _network(when="2026-10-01T00:00:00+00:00", status="eligible", event_id="evt-a")
    second = _network(when="2026-10-02T00:00:00+00:00", status="pending", event_id="evt-b")
    left = replay_projection([first, second, second])
    right = replay_projection([second, first])
    assert left.current == right.current
    assert left.for_organization("tenant-demo")[0]["payload"]["to_status"] == "pending"
    assert len(left.history) == 2


def test_projection_and_episodes_stay_inside_the_organization():
    demo = _network(when="2026-10-02T00:00:00+00:00", status="pending", event_id="evt-demo")
    other = _network(
        "tenant-other",
        when="2026-10-02T00:00:00+00:00",
        status="eligible",
        event_id="evt-other",
    )
    projection = ProjectionConsumer()
    episodes = EpisodeStore()
    for event in (demo, other):
        projection.handle(event)
        episodes.handle(event)
    demo_rows = projection.for_organization("tenant-demo")
    assert len(demo_rows) == 1
    assert demo_rows[0]["event_id"] == "evt-demo"
    assert all(row["organization_id"] == "tenant-demo" for row in episodes.for_organization("tenant-demo"))
    assert episodes.for_organization("tenant-demo")[0]["validity_type"] == "COMMITTED"
    assert "evt-other" not in {row["episode_id"] for row in episodes.for_organization("tenant-demo")}


def test_additive_payload_field_is_accepted(caplog, tmp_path):
    caplog.set_level(logging.INFO, logger="careops.events")
    event = _network(
        when="2026-10-02T00:00:00+00:00",
        status="pending",
        event_id="evt-add",
        extra={"note": "reviewed"},
    )
    store = ChangeStore(tmp_path / "changes.db")
    audit = AuditConsumer()
    episodes = EpisodeStore()
    broker = InProcessBroker([audit, ProjectionConsumer(), episodes])
    store.commit(event)
    result = publish_pending(store, broker)
    assert result[0]["published"] is True
    assert audit.records[0]["payload"]["to_status"] == "pending"
    assert audit.records[0]["payload"]["note"] == "reviewed"
    assert episodes.episodes[0]["trace_id"] == "trace-net"
    text = caplog.text
    assert "trace-net" in text
    assert "synthetic restricted credential note" not in text
    assert "test-secret" not in text


def test_required_event_types_reference_contracts_and_not_the_metric_value(tmp_path):
    store = ChangeStore(tmp_path / "changes.db")
    events = [
        _network(when="2026-10-02T00:00:00+00:00", status="pending", event_id="evt-network"),
        build_event(
            event_type="policy.version_activated",
            organization_id="tenant-demo",
            subject_refs=["policy:provider-detail-access"],
            payload={"policy_id": "provider-detail-access", "policy_version": 1},
            occurred_at="2026-10-02T00:00:00+00:00",
            trace_id="trace-policy",
            correlation_id="corr-policy",
            causation_id="cause-policy",
            event_id="evt-policy",
        ),
        build_event(
            event_type="data_product.freshness_breached",
            organization_id="tenant-demo",
            subject_refs=["data_product:available_appointments"],
            payload={"data_product": "available_appointments", "source_id": "gold_provider_availability_daily"},
            occurred_at="2026-10-02T00:00:00+00:00",
            trace_id="trace-fresh",
            correlation_id="corr-fresh",
            causation_id="cause-fresh",
            event_id="evt-fresh",
        ),
        build_event(
            event_type="contract.version_published",
            organization_id="tenant-demo",
            subject_refs=["metric:available_appointments"],
            payload={"metric_id": "available_appointments", "contract_version": 1},
            occurred_at="2026-10-02T00:00:00+00:00",
            trace_id="trace-contract",
            correlation_id="corr-contract",
            causation_id="cause-contract",
            event_id="evt-contract",
        ),
    ]
    for event in events:
        store.commit(event)
    blob = json.dumps(store.events())
    assert store.counts() == {"changes": 4, "outbox": 4}
    assert "is_bookable" not in blob
    assert '"current": 24' not in blob
    assert '"prior": 42' not in blob
