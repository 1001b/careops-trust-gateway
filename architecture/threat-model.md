# Lightweight Threat Model

This reference implementation is intentionally small, but its controls are organized around failure modes that become important when agents consume enterprise healthcare data.

| Failure mode | Naive behavior | Control demonstrated here |
|---|---|---|
| Unauthorized retrieval | Retrieve first, redact later | Role/class filtering before content is returned |
| Stale policy | Semantically similar old and new docs are both supplied | Effective-date + authority filtering |
| Metric fabrication | Model infers/calculates KPI from context | Deterministic metric tool backed by governed source |
| Semantic drift | Different consumers interpret the same metric differently | Explicit metric definition, grain, owner, source and freshness |
| Insufficient evidence | Model produces the most plausible answer anyway | `insufficient_evidence` is a valid gateway outcome |
| Identity ambiguity | Fuzzy match silently becomes truth | Resolver returns confidence and review signal |
| Protocol coupling | Governance logic lives inside one agent framework | MCP remains a thin adapter over domain services |

## Deliberately out of scope for the demo

A production deployment would additionally need enterprise identity, tenant isolation, secrets management, network controls, PHI classification/minimum-necessary enforcement, immutable audit retention, model/vendor risk controls, incident response, and deployment/SLO design.
