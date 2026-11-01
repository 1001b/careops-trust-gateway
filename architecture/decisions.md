# Architecture Decisions

## ADR-001 — Separate reasoning from authority

**Decision:** Quantitative enterprise facts are produced by deterministic tools backed by governed data, not by model-generated arithmetic over retrieved context.

**Why:** LLMs are useful for intent interpretation and synthesis but are a poor authority boundary for financial, operational, identity, or compliance-sensitive facts.

**Consequence:** More tool/schema work up front; substantially better reproducibility and auditability.

## ADR-002 — Authorization before retrieval

**Decision:** Caller role/context is part of the retrieval request. Unauthorized sources are filtered before content can become model context.

**Why:** Post-generation redaction cannot undo disclosure to the model or its surrounding runtime.

## ADR-003 — Time-aware policy retrieval

**Decision:** Knowledge items carry effective dates and authority/version metadata.

**Why:** Semantic similarity alone cannot determine whether a policy is currently valid.

## ADR-004 — Explicit semantic contracts

**Decision:** Metrics have explicit definition, grain, authority, owner, freshness expectation, and supported dimensions.

**Why:** A metric name is not enough to keep dashboards, analysts, and agents aligned.

## ADR-005 — Abstention is success when evidence is insufficient

**Decision:** The gateway returns `insufficient_evidence` when the governed surface cannot support an answer.

**Why:** Always-answer behavior optimizes for fluency rather than trust.

## ADR-006 — Protocol adapters remain thin

**Decision:** MCP is an adapter over governed domain services; policy and authority rules do not live in the MCP handlers.

**Why:** Trust boundaries should survive protocol/framework changes.

## ADR-007 — Fair naive vs governed RAG (v0.2)

**Decision:** Naive and governed RAG share the same corpus, chunking, and embedder. Governance differs only by pre-retrieval authz/effective-date filtering, metric tool access, and sufficiency checks.

**Why:** Comparing a governed system to a deliberately weaker lexical baseline overstates the architecture. A fair experiment isolates the control plane.

**Consequence:** Offline CI uses a deterministic local embedder; LLM synthesis is optional and key-gated. Gemini and OpenAI are interchangeable synthesizers behind the same system prompt and governed tool allowlist; embeddings may remain local so fairness does not require a vendor embedding API.

ADR-001 through ADR-007 are the qualified baseline. ADR-009 through ADR-018 freeze v0.4 boundaries. They do not revise the earlier decisions. Specs live in `specs/` and `docs/platform-integration.md`. No package move accompanies this appendix.

## ADR-009 — Semantic-contract authority

**Extends:** ADR-004.

**Decision:** `available_appointments` has one quantitative implementation, `careops.metrics.get_metric`, identified by authority `tool:get_metric` and source `gold_provider_availability_daily`. A later contract version may add classification, purpose, and a freshness SLO. Example names in the system design (`governed_metric_service`, `fct_appointment_capacity`, a published `contract_version` of 3) are aliases until a versioned change says otherwise.

**Why:** Replacing the working metric to match an illustration would fork the qualified 42 → 24 result.

**Consequence:** TC-02 extends the JSON contract and adds a fanout fixture beside the current gold model. It does not rename bronze, silver, or gold.

## ADR-010 — Identity-derived authorization on the protected route

**Decision:** `POST /api/demo`, the CLI `--role` flag, and MCP `search_policy(role=)` keep a caller-selected role. `POST /v1/query` takes principal, role, tenant, purpose, and scopes from a verified token. The protected body has no role field. Invalid tokens fail before retrieval. Valid tokens without a grant fail before retrieval and before a model call.

**Why:** The demo's role switch is how a reviewer sees authorization. Copying that switch onto the protected route would make the caller the authority for its own access.

**Consequence:** Tests may use a local issuer and JWKS. No external identity provider is required. The FastAPI version string stays `0.2.0` until a later qualification.

## ADR-011 — Freshness before model use

**Extends:** ADR-003.

**Decision:** Policy effective dates already drop stale text. A data product that misses its freshness SLO returns `stale_evidence` and does not call a model. The current freshness value `daily` is a label, not that gate. The target check is source age against the SLO (the design example is 1440 minutes).

**Why:** A contract that only describes freshness does not stop a model from answering on stale inputs.

**Consequence:** TC-04 implements one gate. This appendix does not.

## ADR-012 — One core, many adapters

**Extends:** ADR-006.

**Decision:** REST, CLI, the demo, and MCP call the same metric and policy functions. Adapters do not reimplement formulas. `careops.gateway.answer` and `careops.agent.answer` both remain. CI must be able to qualify control-plane behavior with a deterministic decision fixture and no live model.

**Why:** Trust boundaries should survive a protocol change, and qualification should not depend on a hosted model being up.

**Consequence:** The known Texas / Aetna hardcode in the agent metric path is a conformance gap for a later card, not a reason to delete either answer function.

## ADR-013 — Replay and versioned change

**Decision:** A change to metric meaning, grain, access, or the authoritative number requires a new contract or policy version. Replay compares access, masking, grain, freshness, the authoritative number, and the response contract. An expanded grant, a missing version bump, or an unexpected numeric drift fails the check.

**Why:** Otherwise two consumers can disagree about which definition they ran, with no artifact that shows the drift.

**Consequence:** The replay command arrives with the conformance work. This appendix only freezes the failure rules.

## ADR-014 — Ontology does not own the metric

**Decision:** The ontology names a bounded set of entities and relationships. It answers what kinds of things exist. `get_metric` and the policy filter answer what is true and what may be shown. `semantic/entities.json` stays a provider resolver, not an ontology.

**Why:** A semantic layer that can overwrite a governed aggregate is a second, unaudited calculator.

**Consequence:** Relationship types are fixed in spec 003. Adding a relationship is a contract change, not a runtime side effect of retrieval.

## ADR-015 — Temporal context is a degradable sidecar

**Decision:** Episodes are durable historical context. Graphiti is the named adapter behind `TemporalContextStore`. Ingestion order is domain event, then episode, then the store. `reference_time` and `observed_at` stay distinct. If the store is unavailable, historical context is omitted and the metric and policy answers still return. The store is not invented around.

**Why:** Memory can be wrong, stale, or down. The governed answer cannot depend on it.

**Consequence:** Only an Authority Plane commit may mark an episode `COMMITTED`. The October episode calendar and `network-policy-v3` in the system design are not on the qualified tag.

## ADR-016 — Four planes over the current modules

**Decision:** Authority, control, execution, and context are boundaries, not a new tree. Authority is the metric tool, the policy filter, and the database. Control today is intent routing. Execution today is the optional synthesizer. Context today is retrieval. Later cards add workflow state, three specialists, and episodes beside those modules.

**Why:** A package split that only mirrors the diagram would churn qualified imports and hide the regression surface.

**Consequence:** The conceptual tree in the system design is documentation. `architecture/decisions.md` remains the ADR file.

## ADR-017 — Controlled delegation, replaceable transport

**Decision:** v0.4 has three specialists: `DataQualityAgent`, `PolicyAgent`, and `TemporalContextAgent`. A specialist requests another capability through the control plane. The CI transport is in-process `dispatch(task) -> result`. A wire protocol may carry the same envelopes and does not route, budget, authorize, or commit.

**Why:** An open mesh cannot enforce depth, loops, or ownership, and a transport brand is not an orchestration model.

**Consequence:** Direct peer calls are out of contract. The demo HTTP rate limit is not the workflow budget.

## ADR-018 — Promotion, concurrency, and finality

**Decision:** A proposal is durable and non-authoritative. A decision gate may return `COMPLETE`, `VERIFY_MORE`, `HUMAN_REVIEW`, or `REJECT`. Authorization is a separate deterministic allow or deny. Commit is a transaction that succeeds only at the expected entity version, then emits one domain event. Jev may implement the decision-gate interface and cannot authorize or commit. CI uses a deterministic gate. Human review remains available for a consequential action. Delivery to consumers is at-least-once with idempotent handlers, not an exactly-once claim.

**Why:** A model recommendation, a stored proposal, or a second agent's agreement is not an enterprise fact. Two workflows can read the same version; one commit must invalidate the other.

**Consequence:** Execution finality (a finished task or a checkpoint) is not domain finality (a committed row). Findings carry `evidence_root_ids`. Shared roots are repetition, not corroboration. The flagship `credentialing_case` version is specified in spec 003 and does not exist on the qualified tag.
