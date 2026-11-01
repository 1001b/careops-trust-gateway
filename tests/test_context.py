"""TC-07: bounded ontology, temporal context, and a non-authoritative graph copy."""

from __future__ import annotations

import inspect
import json

import pytest

from careops.context.episode import narrate, present, project_episode
from careops.context.graphiti_adapter import GraphitiTemporalStore, graphiti_runtime_available
from careops.context.ontology import ENTITIES, RELATIONSHIPS, VALIDITY_TYPES, OntologyError, validate_ontology
from careops.context.store import ContextUnavailable, LocalTemporalStore
from careops.events import EpisodeStore, build_event
from careops.identity import IdentityConfig, issue_token
from careops.metrics import get_metric
from careops.paths import SEMANTIC_DIR
from careops.protected import protected_query

CONFIG = IdentityConfig(secret="test-secret")
QUESTION = "Why did in-network appointment availability in Texas decline last week?"
POLICY_QUESTION = "What is the current network eligibility policy?"
AS_OF = "2026-10-05"
P002 = "provider:P002"


def _token(**overrides) -> str:
    fields = {
        "principal": "analyst-1",
        "role": "analyst",
        "tenant": "tenant-demo",
        "purpose": "operations_analysis",
    }
    fields.update(overrides)
    return issue_token(CONFIG, **fields)


def _store() -> LocalTemporalStore:
    return LocalTemporalStore.from_fixture()


def _ids(rows: list[dict]) -> set[str]:
    return {row["episode_id"] for row in rows}


def test_ontology_is_the_bounded_v04_set():
    document = validate_ontology()
    assert set(document["entities"]) == set(ENTITIES)
    assert set(document["relationships"]) == set(RELATIONSHIPS)
    wider = {
        "entities": sorted(ENTITIES) + ["Patient"],
        "relationships": sorted(RELATIONSHIPS),
    }
    with pytest.raises(OntologyError) as caught:
        validate_ontology(wider)
    assert caught.value.code == "ontology_out_of_bounds"


def test_domain_event_projects_one_episode_without_making_the_copy_authoritative():
    event = build_event(
        event_type="provider.network_status_changed",
        organization_id="tenant-demo",
        subject_refs=["provider:P002", "network:Aetna"],
        payload={"provider_id": "P002", "payer_network": "Aetna", "to_status": "inactive", "provider_name": "hidden"},
        occurred_at="2026-09-30T00:00:00+00:00",
        observed_at="2026-10-05T12:00:00+00:00",
        trace_id="trace-net",
        correlation_id="corr-net",
        causation_id="cause-net",
        event_id="evt-project",
    )
    episode = project_episode(event)
    for field in (
        "episode_id",
        "organization_id",
        "event_type",
        "reference_time",
        "observed_at",
        "source_type",
        "source_id",
        "subject_refs",
        "payload",
        "classification",
        "provenance",
        "validity_type",
        "trace_id",
        "correlation_id",
        "causation_id",
    ):
        assert field in episode
    assert episode["episode_id"] == "evt-project"
    assert episode["reference_time"] == "2026-09-30T00:00:00+00:00"
    assert episode["observed_at"] == "2026-10-05T12:00:00+00:00"
    assert episode["reference_time"] != episode["observed_at"]
    assert episode["validity_type"] == "COMMITTED"
    assert episode["provenance"]["store_authority"] is False
    assert episode["provenance"]["validity_means"] == "source_transition_committed"
    assert episode["trace_id"] == "trace-net"
    assert "provider_name" not in episode["payload"]
    store = EpisodeStore()
    store.handle(event)
    stored = store.for_organization("tenant-demo")[0]
    assert stored["validity_type"] == "COMMITTED"
    assert stored["provenance"]["store_authority"] is False


def test_fixtures_keep_three_clocks_and_only_known_validity():
    rows = json.loads((SEMANTIC_DIR / "episodes.json").read_text(encoding="utf-8"))
    inferred = next(row for row in rows if row["episode_id"] == "ep-p002-inferred")
    assert inferred["reference_time"][:10] == "2026-09-20"
    assert inferred["observed_at"][:10] == "2026-09-21"
    assert inferred["reference_time"] != inferred["observed_at"]
    assert "effective_from" not in inferred["payload"]
    eligible = next(row for row in rows if row["episode_id"] == "ep-p002-eligible")
    assert eligible["payload"]["effective_from"] == "2026-01-01"
    assert eligible["payload"]["effective_to"] == "2026-09-29"
    assert {row["validity_type"] for row in rows} <= set(VALIDITY_TYPES)
    text = (SEMANTIC_DIR / "ontology.json").read_text(encoding="utf-8")
    text += (SEMANTIC_DIR / "episodes.json").read_text(encoding="utf-8")
    for banned in ("Huron", "IDEXX", "Palmetto", "Rula", "Ben Chen", "Carla Diaz"):
        assert banned not in text


def test_supersession_keeps_history_and_current_uses_the_later_relationship():
    store = _store()
    earlier = store.preceding("tenant-demo", P002, "2026-09-15")
    assert _ids(earlier) == {"ep-p002-eligible"}
    assert store.current("tenant-demo", P002, "2026-09-15")[0]["episode_id"] == "ep-p002-eligible"
    around = store.preceding("tenant-demo", P002, AS_OF)
    assert {"ep-p002-eligible", "ep-p002-inactive"} <= _ids(around)
    assert all(row["reference_time"][:10] <= AS_OF for row in around)
    current = store.current("tenant-demo", P002, AS_OF)
    assert [row["episode_id"] for row in current] == ["ep-p002-inactive"]
    assert current[0]["payload"]["status"] == "inactive"
    assert "ep-p002-eligible" not in _ids(current)


def test_future_effective_evidence_stays_out_of_an_earlier_reference_time():
    future = {
        "episode_id": "ep-ref-before-effective-after",
        "organization_id": "tenant-demo",
        "event_type": "policy.version_activated",
        "reference_time": "2026-09-01T00:00:00+00:00",
        "observed_at": "2026-09-01T00:00:00+00:00",
        "source_type": "fixture",
        "source_id": "ep-ref-before-effective-after",
        "subject_refs": ["policy:network-eligibility"],
        "payload": {
            "relationship": "SUPERSEDES",
            "policy_id": "network-eligibility",
            "effective_from": "2026-10-15",
        },
        "classification": "internal",
        "provenance": {"store_authority": False},
        "validity_type": "COMMITTED",
        "trace_id": "",
        "correlation_id": "",
        "causation_id": "",
    }
    store = LocalTemporalStore(_store().episodes + [future])
    visible = _ids(store.visible("tenant-demo", AS_OF))
    assert "ep-policy-future" not in visible
    assert "ep-ref-before-effective-after" not in visible
    later = _ids(store.visible("tenant-demo", "2026-10-20"))
    assert "ep-policy-future" in later
    assert "ep-ref-before-effective-after" in later


def test_same_provider_ref_in_another_tenant_is_not_retrieved():
    store = _store()
    demo = store.preceding("tenant-demo", P002, AS_OF)
    assert "ep-other-tenant-p002" not in _ids(demo)
    assert all(row["organization_id"] == "tenant-demo" for row in demo)
    other = store.preceding("tenant-other", P002, AS_OF)
    assert _ids(other) == {"ep-other-tenant-p002"}


def test_temporal_adjacency_is_labeled_context():
    store = _store()
    text = narrate(store.preceding("tenant-demo", P002, AS_OF))
    assert "Relevant historical context" in text
    assert "preceded" in text
    assert "caused" not in text.lower()


def test_inferred_and_proposed_episodes_keep_their_validity():
    store = _store()
    rows = store.preceding("tenant-demo", P002, AS_OF)
    inferred = present(next(row for row in rows if row["episode_id"] == "ep-p002-inferred"))
    proposed = next(row for row in rows if row["episode_id"] == "ep-p002-proposed")
    assert inferred["validity_type"] == "INFERRED"
    assert proposed["validity_type"] == "PROPOSED"
    assert inferred["payload"]["status"] == "eligible"
    assert _ids(store.current("tenant-demo", P002, AS_OF)) == {"ep-p002-inactive"}


def test_older_context_does_not_replace_the_authoritative_metric():
    store = _store()
    historical = store.current("tenant-demo", P002, "2026-09-15")[0]
    assert historical["payload"]["status"] == "eligible"
    assert historical["provenance"]["store_authority"] is False
    inferred = next(
        row for row in store.preceding("tenant-demo", P002, AS_OF) if row["validity_type"] == "INFERRED"
    )
    assert inferred["payload"]["status"] == "eligible"
    assert get_metric("available_appointments", state="TX", payer_network="Aetna", period="prior_week").value == 42
    assert get_metric("available_appointments", state="TX", payer_network="Aetna", period="last_week").value == 24
    for row in store.episodes:
        assert "42" not in json.dumps(row["payload"])
        assert "24" not in json.dumps(row["payload"])


def test_flagship_keeps_metric_policy_and_context_on_separate_planes():
    result = protected_query(_token(), QUESTION, config=CONFIG)
    metric = next(item for item in result["evidence"] if item["type"] == "metric")
    policy = next(item for item in result["evidence"] if item["type"] == "policy")
    temporal = next(item for item in result["evidence"] if item["type"] == "temporal_context")
    assert metric["comparison"]["prior"]["value"] == 42
    assert metric["comparison"]["current"]["value"] == 24
    assert policy["documents"]
    assert temporal["authority"] is False
    assert temporal["causality"] == "not_asserted"
    ids = _ids(temporal["episodes"])
    assert "ep-p002-inactive" in ids
    assert "ep-p002-eligible" in ids
    assert "ep-policy-future" not in ids
    assert "ep-other-tenant-p002" not in ids
    inferred = next(row for row in temporal["episodes"] if row["episode_id"] == "ep-p002-inferred")
    proposed = next(row for row in temporal["episodes"] if row["episode_id"] == "ep-p002-proposed")
    assert inferred["validity_type"] == "INFERRED"
    assert proposed["validity_type"] == "PROPOSED"
    assert "caused" not in result["answer"].lower()
    assert "preceded" in result["answer"]
    planes = result["decision"]["evidence_planes"]
    assert planes == {
        "authoritative_structured": True,
        "policy": True,
        "temporal_context": "available",
        "model": False,
    }
    assert "ep-p002" not in json.dumps(result["decision"])


def test_policy_retrieval_does_not_read_the_temporal_store():
    result = protected_query(_token(), POLICY_QUESTION, config=CONFIG)
    assert result["outcome"] == "allow"
    assert any(item["type"] == "policy" for item in result["evidence"])
    assert not any(item.get("type") == "temporal_context" for item in result["evidence"])
    assert result["decision"]["evidence_planes"]["policy"] is True
    assert result["decision"]["evidence_planes"]["authoritative_structured"] is False
    assert result["decision"]["evidence_planes"]["temporal_context"] == "not_requested"


def test_context_outage_leaves_the_authoritative_answer(monkeypatch):
    monkeypatch.setattr("careops.context.query.load_store", lambda: GraphitiTemporalStore(None))
    result = protected_query(_token(), QUESTION, config=CONFIG)
    metric = next(item for item in result["evidence"] if item["type"] == "metric")
    assert metric["comparison"]["prior"]["value"] == 42
    assert metric["comparison"]["current"]["value"] == 24
    assert result["outcome"] == "allow"
    assert any(item["type"] == "policy" for item in result["evidence"])
    assert not any(item.get("type") == "temporal_context" for item in result["evidence"])
    assert "Historical context is unavailable." in result["answer"]
    assert "caused" not in result["answer"].lower()
    assert "ep-p002" not in result["answer"]
    assert result["decision"]["evidence_planes"]["temporal_context"] == "unavailable"
    assert result["decision"]["evidence_planes"]["authoritative_structured"] is True
    assert result["decision"]["evidence_planes"]["policy"] is True


def test_graphiti_adapter_uses_ontology_types_and_refuses_a_missing_client():
    class Fake:
        def __init__(self):
            self.added = []

        def add_episode(self, body):
            self.added.append(body)

        def search(self, query):
            assert query["authority"] is False
            assert query["group_id"] == "tenant-demo"
            return _store().episodes

    fake = Fake()
    adapter = GraphitiTemporalStore(fake)
    episode = next(row for row in _store().episodes if row["episode_id"] == "ep-p002-eligible")
    body = adapter.add_episode(episode)
    assert body["authority"] is False
    assert body["store_authority"] is False
    assert body["edge_type"] == "PARTICIPATES_IN"
    assert "Provider" in body["entity_types"]
    assert "Network" in body["entity_types"]
    assert fake.added == [body]
    demo = adapter.preceding("tenant-demo", P002, AS_OF)
    assert "ep-other-tenant-p002" not in _ids(demo)
    assert "ep-policy-future" not in _ids(demo)
    missing = GraphitiTemporalStore(None)
    assert missing.available is False
    with pytest.raises(ContextUnavailable) as caught:
        missing.preceding("tenant-demo", P002, AS_OF)
    assert caught.value.code == "graphiti_unavailable"
    outside = dict(episode)
    outside["payload"] = {"relationship": "EMPLOYS"}
    with pytest.raises(ContextUnavailable) as rejected:
        adapter.add_episode(outside)
    assert rejected.value.code == "relationship_outside_ontology"


def test_metric_policy_and_event_contracts_do_not_name_graphiti():
    import careops.conformance as conformance
    import careops.events.schema as schema
    import careops.metrics as metrics
    import careops.policy as policy
    import careops.protected as protected

    for module in (metrics, policy, protected, conformance, schema):
        assert "graphiti" not in inspect.getsource(module)
    project = (SEMANTIC_DIR.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert "graphiti" not in project
    assert graphiti_runtime_available() in {True, False}


@pytest.mark.skip(reason="live Graphiti service is not part of CI and was not executed")
def test_live_graphiti_service_was_not_executed():
    raise AssertionError("live Graphiti qualification must not be claimed from this test")
