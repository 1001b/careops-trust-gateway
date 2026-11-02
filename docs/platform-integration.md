# Platform integration

**Status:** TC-01 specification freeze. No runtime change.
**Baseline:** git tag `careops-v0.3-qualified`.

CareOps is one data product with one quantitative implementation, served to a person, a CLI, an HTTP demo, and a thin MCP adapter. v0.4 adds identity, events, temporal context, and a control plane beside that core. Those additions do not become a second system of record.

## What the domain owns

The synthetic healthcare-operations fixture owns providers, payer networks, appointment slots, and the policies in `knowledge/`. The trusted product is `available_appointments`, calculated only by `careops.metrics.get_metric`, described by `semantic/metrics.json`, and modeled for build-time review under `dbt/models/bronze`, `silver`, and `gold`.

Shared standards sit next to that product: the metric contract (spec 001), the protected query and governance contract (spec 002), and the episode, event, workflow, and proposal contracts (spec 003). Identity, policy, and the decision trace are cross-cutting. They are not copied into each adapter.

| Concern | Central | Left at the edge |
|---|---|---|
| Metric formula and policy decision | `careops/metrics.py`, `careops/policy.py` | adapters call in |
| Who the caller is, on the demo | request body role, preserved | demo, CLI, MCP `search_policy` |
| Who the caller is, on `POST /v1/query` | verified claims | the route, when TC-03 adds it |
| Current truth | SQLite by default, Postgres when configured | not the event log, not the temporal store |
| Change propagation | outbox row in the same commit as the state change | publisher and consumers, later |
| Historical color | temporal episodes | omitted when that store is down |

## Preserve, then extend

| Keep | Extend later, without replacing the keep column |
|---|---|
| 42 → 24, delta -18, -42.9% on Texas / Aetna | contract version, classification, freshness SLO |
| `tool:get_metric` and `gold_provider_availability_daily` | conceptual names in the system design stay aliases |
| bronze / silver / gold | grain, referential, reconciliation, and a separate fanout fixture |
| `POST /api/demo` role switch and inline page | `POST /v1/query` with no caller-selected role |
| `GatewayResponse` statuses `ok` and `insufficient_evidence` | query outcomes and the wider workflow trace |
| ADR-001 through ADR-007 in this file's sibling `architecture/decisions.md` | ADR-009 through ADR-018 appended there |
| Lean SQLite demo, no API key required | optional Postgres, broker, and temporal-store profiles |

The system design's folder sketch (`app/api`, `careops/control`, `docs/adr`, and the rest) describes responsibilities. It is not a move ticket.

## One migration sequence

Each phase preserves the phase before it. This document is phase 1. Later phases are not authorized by the freeze.

```text
Phase 0  tag careops-v0.3-qualified
Phase 1  this spec set and the ADR appendix
Phase 2  trusted semantic data product
Phase 3  identity and governance
Phase 4  freshness, trace, one dependency failure
Phase 5  conformance and contract replay
Phase 6  events and outbox
Phase 7  ontology and temporal context
Phase 8  control plane and workflow state
Phase 9  one controlled delegation and one promotion
Phase 10 public qualification — README only; no new runtime
```

## Tradeoffs

**Bounded workers over a free mesh.** Three specialists can request a capability only through the control plane. That gives up peer-to-peer convenience and keeps routing, depth, budget, and termination in one place.

**Typed memory over a second database of record.** Episodes, including a Graphiti-backed store when that profile is on, improve historical questions. They are durable and still non-authoritative. The cost is an extra store that must degrade cleanly.

**Outbox over a dual write, and at-least-once over an exactly-once claim.** A committed change and its event row share a transaction. Consumers tolerate duplicates. The synchronous query does not wait on the broker.

**Inspectable demo over throughput.** The lean profile is SQLite plus deterministic stand-ins for the decision gate, the transport, and the temporal store. The production-profile Azure path qualifies the same contracts with Postgres and can be torn down. Neither profile is a claim of production scale.

## Regression constraints

These behaviors are the floor. A later phase that changes them has to show a new qualification, not a silent drift.

| Behavior | Where it is locked |
|---|---|
| Prior week 42, last week 24, delta -18, percent -42.9 | `tests/test_gateway.py` `test_metric_values_are_deterministic`; evals `metric-prior-week`, `metric-last-week` |
| Analyst sees `network-policy-v2` and not `network-policy-v1` | `test_current_policy_excludes_stale_version`; eval `time-current-policy` |
| Analyst does not retrieve `credentialing-escalation`; `clinical_admin` does | `test_authorization_prevents_restricted_retrieval`; evals `auth-analyst-restricted`, `auth-admin-restricted` |
| Flagship wording routes `mixed` and returns metric plus policy evidence | `test_mixed_question_routes_to_both_paths` |
| Unknown question and unauthorized credentialing question abstain | `test_unknown_request_abstains`, `test_restricted_question_abstains_for_analyst`; eval `gateway-unknown` |
| Provider resolution returns confidence and a review signal | `test_entity_resolution_exposes_confidence_and_review_signal` |
| Naive and governed retrieval share corpus, chunks, and embedder | `tests/test_rag_v2.py` |
| Case harness | `python -m evals.run` against `evals/cases.json` |
| Unsafe lexical baseline remains available | `examples/naive_rag.py` |
| Default path needs no model API key | gateway and CLI |
| Demo role switch remains | `POST /api/demo`, `careops/cli.py --role`, MCP `search_policy(role=)` |

There is no web-test module on this tag. The demo page is the inline handler in `app/main.py`.

Known gap, not a license to merge the two answer functions: `careops.agent.pipeline` always measures Texas / Aetna, while `careops.gateway.answer` parses state and still defaults to Texas. The flagship question hits both the same way.

## Planes in one paragraph

Authority is the metric tool, the policy filter, and the database behind them. Control today is only intent routing; the workflow store is later and is not allowed to commit. Execution today is an optional synthesizer; the three specialists arrive with the control plane and cannot call a commit API. Context today is retrieval; episodes join it later and drop out of the answer when their store is unavailable. Events, once added, describe commits. They do not answer the live query.
