# 001 — Trusted semantic data product

**Status:** TC-01 specification freeze. No runtime change.
**Baseline:** git tag `careops-v0.3-qualified`.
**Authority:** v0.4 system design, with this file recording where an example name differs from the tagged code.
**Implements later:** TC-02. This document does not add models, fixtures, or tests.

## Decision

`available_appointments` remains one data product with one quantitative implementation: `careops.metrics.get_metric`. A semantic document may describe that product. It may not calculate it. A model may explain a result that the tool already computed. It may not become the source of the number.

The qualified Texas / Aetna result stays:

| Period | Value |
|---|---|
| Prior week | 42 |
| Last week | 24 |
| Delta | -18 |
| Percent change | -42.9 |

Locked by `tests/test_gateway.py` (`test_metric_values_are_deterministic`) and `evals/cases.json` (`metric-prior-week`, `metric-last-week`). The fixed demo clock is 2026-10-05.

## Baseline contract

`semantic/metrics.json`, loaded by `careops.semantic.metric_definition`, currently contains:

| Field | Baseline value |
|---|---|
| Key | `available_appointments` |
| `definition` | Bookable appointment slots for active providers currently eligible for the requested payer network. |
| `grain` | `provider_id`, `slot_date`, `payer_network`, `state` |
| `authority` | `tool:get_metric` |
| `owner` | `care-operations-data` |
| `freshness` | `daily` |
| `dimensions` | `state`, `payer_network`, `provider_id` |
| `source` | `gold_provider_availability_daily` |

`MetricResult` returns `metric`, `value`, `filters`, `period_start`, `period_end`, `authority`, `source`, `definition`, and `grain`. There is no `contract_version` on the object or in the JSON file. Freshness is a label, not a gate. This freeze does not invent a version number for the baseline file.

## Target contract

TC-02 adds the missing fields on this same metric. The system-design example is an illustration, not a rename of the qualified object. `contract_version: 3` in that example is not a version this repository has published.

Qualified identifiers remain in force:

| System-design example | Qualified code | Freeze rule |
|---|---|---|
| `authority: governed_metric_service` | `tool:get_metric` | The service is `careops.metrics.get_metric`. The authority string stays `tool:get_metric`. |
| `source: fct_appointment_capacity` | `gold_provider_availability_daily` | Runtime and dbt gold keep the current name. |
| `owner: care-operations` | `care-operations-data` | Owner string stays until a versioned contract change. |
| grain `organization_id`, `provider_id`, `network_id`, `service_date` | `provider_id`, `slot_date`, `payer_network`, `state` | TC-02 may add `organization_id`. It does not rename the columns that produce 42 and 24. |
| `staging/` then `dim_*` / `fct_*` | `dbt/models/{bronze,silver,gold}` | Conceptual layout only. Section 38 of the system design forbids a cosmetic move. |

```yaml
metric_id: available_appointments
contract_version:            # unpublished; do not write 3 into the baseline file
status:
owner: care-operations-data
authority: tool:get_metric
source: gold_provider_availability_daily
grain: [provider_id, slot_date, payer_network, state]
dimensions: [state, payer_network, provider_id]
effective_from:
classification:
freshness_slo_minutes:       # target example is 1440, meaning the existing "daily" label
allowed_purposes:
compatibility:
deprecation:
```

Rules for the future field set:

- `authority` remains `tool:get_metric`.
- A change to definition, grain, filter semantics, or the numeric result requires a new `contract_version`.
- Missing version bump on a semantic or access change is a failed replay (see ADR-013). The replay command is TC-05.
- `classification` and `allowed_purposes` are inputs to field policy. They do not by themselves mask rows.
- Freshness becomes a check, `source_age <= freshness_slo`, in TC-04. Failure outcome is `stale_evidence` and the model is not called. The word `daily` does not perform that check today.

## Grain

Two grains exist and both stay:

| Layer | Grain | Where |
|---|---|---|
| Gold table | one row per `slot_id` | `dbt/models/gold/gold_provider_availability_daily.sql` |
| Metric | sum of `is_bookable` over `state`, `payer_network`, and a week window | `careops.metrics.get_metric` |

`dbt/models/schema.yml` checks `slot_id` not null and unique, and `is_bookable` in `{0, 1}`, on silver providers, silver slots, and gold. There is no referential-integrity test, no reconciliation test, and no fanout fixture.

The system design's fanout illustration is a separate broken model: two network rows times ten capacity rows becoming twenty. That is not a description of current gold. Current gold uses `EXISTS` for network eligibility, so an effective-dated second network row does not multiply slots. P003 already has two Aetna rows (`eligible` through 2026-09-30, `pending` from 2026-10-01) in `data/raw/provider_networks.csv`. P002 is inactive from 2026-09-30. Those rows are the qualified decline, not the defect.

TC-02 adds a naive-join fixture that fails its grain test and leaves `get_metric` on the clean data at 42 and 24.

dbt layout stays:

```text
dbt/models/bronze/
dbt/models/silver/
dbt/models/gold/
```

dbt is build-time documentation of the bookability predicate. Request-time authority is the SQLite or Postgres read in `get_metric`. The dbt project is optional and is not required to run the control-plane demo.

## What is already enforced

- Bookability requires an open slot, an active provider, and an eligible payer-network row effective on the slot date. The same predicate is in the gold SQL model and in the runtime metric path.
- Policy text is time-valid. `knowledge/index.json` gives `network-policy-v1` an end date of 2025-12-31 and `network-policy-v2` a start of 2026-01-01. As of the demo clock, analyst retrieval returns v2 and excludes v1 (`test_current_policy_excludes_stale_version`, eval `time-current-policy`).
- Restricted credentialing text is class `restricted` and role `clinical_admin` only. Analyst retrieval does not return it.

## What is declared and not enforced

`semantic/access.json` sets `provider_detail` to `masked`, `standard`, or `full` by role. No metric, gateway, or retrieval path reads that field. This freeze does not describe masking as implemented.

`semantic/entities.json` records a provider resolver (`canonical_key`, `display_field`, `resolver`, `authority`). That is entity resolution with a confidence score (`careops.entities.resolve_provider`). It is not an ontology and it is not a second metric authority.

## Authority boundary

| Question | Authority |
|---|---|
| How many bookable slots | `get_metric` |
| What the metric means | `semantic/metrics.json` |
| Which policy text is current and visible | `careops.policy.search_policy` and `careops.rag.filters` |
| How to phrase the evidence | Optional model synthesis in `careops.agent.llm` |

`careops.agent.llm` tells the model that quantitative values come from `evidence.metric` or a successful `get_metric` tool result. The governed agent path fills `evidence.metric` from `compare_availability` before synthesis. The naive path has no metric tool.

Known adapter gap, left for TC-05: `careops.gateway.answer` parses state from the question and defaults to `TX`. `careops.agent.pipeline` calls `compare_availability(state="TX", payer_network="Aetna")` for every metric question. Both paths agree on the flagship Texas / Aetna question. They are not a general conformance pair.

## Flagship facts that are not on the tag

The system-design investigation adds state this baseline does not have. Later cards may add it. They may not rewrite the 42 → 24 result to fit the story.

| Design example | On `careops-v0.3-qualified` |
|---|---|
| `credentialing_case:P002` at version 7 | No credentialing-case table or entity version |
| `network-policy-v3` effective Oct 15 | Policies stop at `network-policy-v2`, effective 2026-01-01 |
| Episode timeline Oct 1–Oct 18 | Decline is already explained by P002 inactive 2026-09-30 and P003 network `pending` from 2026-10-01 |

P002 and P003 are real fixture ids. The case version, the third policy, and the episode calendar are not.

## Non-goals

This version does not add:

- a generic autonomous-agent framework
- a complete enterprise identity product, or a production OAuth/OIDC platform
- full event sourcing, a microservice mesh, or a production broker cluster
- a replacement for the temporal-context store, or a general-purpose knowledge graph
- a universal enterprise ontology
- a warehouse table format or a container orchestrator added as a keyword
- a compliance-certification claim, or a claim of production adoption
- a claim that MCP or an agent wire protocol provides orchestration or finality
- a claim that the metric contract is already versioned, classified, or freshness-gated

## Acceptance traced here

| Id | How this freeze meets it |
|---|---|
| ARCH-03 | One implementation: `careops.metrics.get_metric`. |
| ARCH-04 | Numbers come from that tool. The model prompt forbids invented metrics. |
| ARCH-05 | Non-goals are listed above. |
| ARCH-07 | Ontology work is deferred. `entities.json` cannot redefine the metric. See ADR-014. |
