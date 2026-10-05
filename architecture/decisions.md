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

**Consequence:** Offline CI uses a deterministic local embedder; OpenAI embeddings/Responses synthesis are optional and key-gated.
