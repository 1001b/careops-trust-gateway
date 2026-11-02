# 003 — Context and control contracts

**Status:** TC-01 specification freeze. No runtime change on the qualified tag.
**Baseline:** git tag `careops-v0.3-qualified` has none of these objects.
**TC-06:** `careops/events` commits a change and its outbox row in one transaction, then publishes through an in-process transport. Consumers are an audit log, a current-state projection, and a local episode store. `get_metric` and `POST /v1/query` do not import this package.
**TC-07:** `careops/context` is the temporal plane. CI uses `LocalTemporalStore` and the synthetic fixture in `semantic/episodes.json`. `GraphitiTemporalStore` is the optional adapter and is not a health dependency of the synchronous query. `validity_type=COMMITTED` means the episode was projected from a committed authoritative event. It does not make the graph copy authoritative.
**TC-08:** `careops/control` stores workflows, tasks, proposals, and decisions in a local SQLite file. A proposal status of `COMMITTED` is ledger state. It does not write the metric or the event outbox. CI uses `FixtureDecisionGate`. Jev is not required.
**TC-09:** One promotion runs the three specialists through the control plane. A matching authority version writes one domain event and projects a `COMMITTED` episode. A decision, an approval, or a second workflow still at the old version does not.
**Implements later:** public qualification.

Names below are the contracts. Graphiti is the named temporal-context adapter. Jev is an optional decision-gate adapter. An agent wire protocol is an optional transport. A Kafka-compatible broker is an optional local profile. None of them are imported by the tagged tree. CI uses deterministic local stand-ins, and the architecture stays valid without the optional processes.

## Planes, mapped onto the current tree

| Plane | Question | On the tag | Later home, when a card adds it |
|---|---|---|---|
| Authority | What is officially true? | `careops/metrics.py`, `careops/policy.py`, `semantic/`, `knowledge/`, SQLite or Postgres | same modules; commit path added beside them |
| Control | What may happen next? | route classification only (`careops/router.py`) | workflow, proposal, and gate modules |
| Execution | What are we doing? | optional single synthesizer in `careops/agent/` | three specialist workers |
| Context | What might matter? | policy retrieval and RAG | ontology plus temporal episodes |

Persistence is not authority. A workflow row, a proposal, and an episode may all be durable and still be non-authoritative. Only an Authority Plane commit is domain-final.

The package sketch in the system design (`careops/control`, `careops/ontology`, `docs/adr`, and the rest) is conceptual. This card does not move files.

## Ontology

Meaning, not truth. The bounded set is Organization, Provider, Network, AppointmentCapacity, DataProduct, MetricContract, Policy, Incident, ChangeEvent, CredentialingCase.

Relationships: `PARTICIPATES_IN`, `OWNED_BY`, `DERIVED_FROM`, `GOVERNED_BY`, `AFFECTS`, `SUPERSEDES`, `OBSERVED_IN`, `CONTRIBUTED_TO`, `RELATED_TO`.

`semantic/entities.json` is a provider resolver. It is not this ontology. The ontology cannot change `get_metric` or policy allow/deny.

## ContextEpisode and TemporalContextStore

Preferred ingestion is `DomainEvent`, then `ContextEpisode`, then the temporal store.

```yaml
episode_id:
organization_id:
event_type:
reference_time:
observed_at:
source_type:
source_id:
subject_refs:
payload:
classification:
provenance:
validity_type:
```

`reference_time` is when the domain fact happened. `observed_at` is when this system learned it. Those timestamps are not interchangeable. A later-effective policy does not leak backward into an earlier reference time. Temporal adjacency is not causality.

```text
OBSERVED | INFERRED | PROPOSED | VERIFIED | COMMITTED | REJECTED | SUPERSEDED
```

The store may hold every type. Only an Authority Plane commit writes `COMMITTED`, and that word describes the source transition. A graph copy of a `COMMITTED` episode is still context. Repetition cannot promote `INFERRED` to `VERIFIED` or `COMMITTED`.

`TemporalContextStore` operations the later adapter must provide:

- append an episode for one organization
- retrieve episodes for a subject at a reference time, same organization only
- refuse a cross-organization read

If the store is down: the metric path, the policy path, and the authoritative answer still run; historical context is omitted; nothing is invented to fill the gap.

The design's October episode calendar and `network-policy-v3` are not in `knowledge/index.json`. TC-07 reconciles new episodes with the existing P002 inactive date (2026-09-30) and P003 `pending` date (2026-10-01). It does not replace the 42 → 24 metric.

## Domain event

Events propagate committed changes. They are not the query path and not a second current-state store. Delivery is at-least-once, so consumers are idempotent. The authoritative write and the outbox insert commit in one transaction. The publisher may retry. A down broker does not block `get_metric`.

```yaml
event_id:
event_type:
event_version:
organization_id:
subject_refs:
occurred_at:
observed_at:
producer:
correlation_id:
causation_id:
trace_id:
classification:
schema_version:
payload:
```

Illustrative types, not an open-ended catalog: `provider.network_status_changed`, `provider.credentialing_status_changed`, `policy.version_activated`, `contract.version_published`, `data_product.freshness_breached`, `credentialing.review_requested`.

Exactly-once delivery is not a claim. Full event sourcing is a non-goal.

## Specialists

Three workers, no more in this version.

| Agent | Question it may answer | It cannot |
|---|---|---|
| `DataQualityAgent` | Is the data product trustworthy? | Commit data, rewrite policy, create a metric value |
| `PolicyAgent` | Which policy applies, and is approval required? | Override deterministic authorization, rewrite policy, commit |
| `TemporalContextAgent` | What changed before this question? | Define current state, treat adjacency as cause, commit |

A specialist that needs another capability asks the control plane. The example path is TemporalContextAgent requesting `policy_interpretation`, and the control plane dispatching PolicyAgent. There is no direct peer channel.

## Task and result

```yaml
workflow_id:
workflow_version:
task_id:
parent_task_id:
from_agent:
requested_capability:
assigned_agent:
objective:
authority_snapshot_version:
evidence_refs:
context_refs:
allowed_tools:
budget:
delegation_depth:
deadline:
idempotency_key:
correlation_id:
causation_id:
```

```yaml
task_id:
agent_id:
status:
finding_refs:
evidence_refs:
context_refs_produced:
proposals:
confidence:
requires_review:
delegation_request:
completed_at:
```

A result may include a proposal. It may not include a commit instruction.

Transport is replaceable:

```text
dispatch(task) -> result
```

The CI transport is in-process and deterministic. A wire-protocol adapter may carry the same envelopes. The transport does not route, budget, authorize, or commit.

## Workflow state

Durable, not domain truth.

```yaml
workflow_id:
workflow_version:
status:
authority_snapshot_version:
active_tasks:
completed_tasks:
proposal_ids:
model_budget:
tool_budget:
delegation_count:
created_at:
updated_at:
```

Terminal statuses: `COMPLETE`, `INSUFFICIENT_EVIDENCE`, `HUMAN_REVIEW`, `DENIED`, `STALE`, `FAILED`, `BUDGET_EXCEEDED`, `LOOP_DETECTED`. Running statuses include `RUNNING` and `WAITING`.

Controls, specified here and enforced in TC-08:

- `max_delegation_depth` example is 2
- a repeated signature of objective + evidence roots + workflow state ends as `LOOP_DETECTED`
- budgets cover model calls, tool calls, elapsed time, and delegation count
- task dispatch and result ingestion are idempotent
- a restart resumes the workflow and does not commit twice

The demo HTTP rate limit is not this budget.

## Proposal, decision, authorization, commit

```yaml
proposal_id:
workflow_id:
proposed_by:
target_entity:
action:
expected_entity_version:
evidence_refs:
status:
```

The design example `credentialing_case:P002` at version 7, action `open_credentialing_review`, is the flagship proposal. That entity does not exist on the tag. Adding it must leave `available_appointments` unchanged.

Lifecycle: `DRAFT` → `PENDING_REVIEW` → `APPROVED` or `REJECTED` → `COMMITTING` → `COMMITTED`, `STALE`, or `FAILED`.

A proposal may live indefinitely and still not be domain state.

| Stage | Nature | Question |
|---|---|---|
| Decision | May be probabilistic | Is the evidence sufficient? |
| Authorization | Deterministic | May this actor perform this action? |
| Commit | Transaction | Is the entity still at the expected version? |

Decision values: `COMPLETE`, `VERIFY_MORE`, `HUMAN_REVIEW`, `REJECT`. Human review is a first-class stop for a consequential action.

`DecisionGate.decide(request) -> result` is the interface. CI ships a deterministic fixture. Jev may implement the same interface on a live profile. Jev does not orchestrate, authorize tools, or commit. CareOps stays valid with the fixture alone.

Optimistic concurrency: two workflows can read version 7; the first commit moves it to 8; the second, still expecting 7, becomes `STALE` and must rehydrate and write a new proposal if the action is still justified. There is no last-writer-wins path.

Finding lineage:

```yaml
finding_id:
validity_type:
source_refs:
evidence_root_ids:
derived_from:
producer_agent:
confidence:
```

Two findings that share one evidence root are one observation repeated. They are not independent corroboration. Deterministic verification may set `VERIFIED`. Only the Authority Plane commit sets `COMMITTED`.

## Promotion sequence

Hydrate from authoritative state. Specialists work. Delegation, if any, crosses the control plane. Typed findings converge. A proposal is stored. The decision gate runs. Deterministic authorization runs. Human approval runs when required. The expected version is checked. Only then does the transaction commit, emit one domain event, and project that committed event back as a `COMMITTED` episode.

Execution finality is a finished task, a routing choice, or a durable checkpoint. Domain finality is the committed row. A checkpoint is not a domain fact.

## Acceptance traced here

| Id | How this freeze meets it |
|---|---|
| ARCH-07 | Ontology defines relationships and does not own the metric or the policy decision. |
| ARCH-08 | Episodes add history. They are not a quantitative or authorization source. |
| ARCH-09 | Temporal store down: historical context omitted, governed answer still available. |
| ARCH-10 | No agent, store, decision component, transport, or vote commits. |
| ARCH-11 | Transport is `dispatch(task) -> result` and is subordinate to the control plane. |
| ARCH-12 | Proposal lifecycle is distinct from domain rows. |
| ARCH-13 | Workflow terminal status is distinct from a committed entity version. |
| ARCH-14 | Validity types and `evidence_root_ids` are required on findings. |
