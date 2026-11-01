# 002 — Identity-bound query API

**Status:** TC-01 specification freeze. No runtime change.
**Baseline:** git tag `careops-v0.3-qualified`.
**TC-03:** `POST /v1/query` verifies a local HS256 token and applies `semantic/governance.json` before retrieval. The demo role switch remains a server-side allowlist.
**TC-04:** A protected metric route checks source age before retrieval. A miss returns `stale_evidence` with `model_called` false. Every protected request emits one decision envelope from `careops.trace`. A metric-dependency timeout returns `dependency_failure` on that same trace. `GET /ready` names the affected data product.

## Decision

Authentication answers who is calling. Authorization answers what that caller may see or do. The qualified demo still accepts a caller-selected role, and that stays the regression surface. `POST /v1/query` derives principal, role, tenant, purpose, and scopes from a verified token. The protected body has no role field.

A probabilistic decision is not authorization, and authorization is not a commit. Those three words stay separate in the workflow trace (system design section 21 and section 28).

## Qualified demo surface

| Surface | Identity behavior |
|---|---|
| `POST /api/demo` | Body field `role` is `analyst`, `operations`, or `clinical_admin`. Default `analyst`. Also accepts `mode` (`governed`, `naive`, `gateway`) and `synthesize`. |
| `GET /api/demo-info` | Lists those roles and modes. |
| `GET /` | Inline page in `app/main.py` posts the selected role to `/api/demo`. |
| `python -m careops.cli --role` | Same three roles. Default `analyst`. Calls `careops.gateway.answer`. |
| MCP `search_policy` | Argument `role` defaults to `analyst`. |
| MCP `get_metric` | No role argument. Defaults `state=TX`, `payer_network=Aetna`, `period=last_week`. |

`app/main.py` reports FastAPI `version="0.2.0"`. This freeze does not change that string. The qualification marker is the git tag.

The demo response adds `request_id`, `latency_ms`, `role`, and `mode` around the core payload. `request_id` exists only in the HTTP handler. `careops.gateway.answer` has no correlation id. The in-process rate limit (`CAREOPS_RATE_LIMIT_PER_MIN`, default 20) is a demo HTTP control, not a workflow budget.

`DEMO_ENABLED=0` returns 503 from `/api/demo`.

Two deployment profiles stay distinct. The lean demo is the SQLite path a reviewer can run without extra infrastructure. The production-profile path already in this tag is Azure infrastructure around the same contracts (Postgres, Key Vault, Bicep). Neither profile includes a token issuer, a broker, or a temporal-graph process.

## Protected route

TC-03 adds `POST /v1/query`.

- Signed bearer token.
- Checks: signature, issuer, audience, expiry.
- Tests use a deterministic local issuer and JWKS. No external identity provider.
- Claims supply principal, role, tenant, purpose, and scopes.
- The JSON body carries the question and filters the core already understands. It does not carry `role`.

| Condition | Result | Before |
|---|---|---|
| Missing, forged, expired, unsigned, wrong issuer, or wrong audience | 401 | Retrieval, evidence assembly, model |
| Token valid, role, purpose, or scope not granted | 403 | Retrieval, evidence assembly, model |
| Token valid, tenant does not own the requested scope | 403 | Retrieval, evidence assembly, model |

The tagged code has no tenant column, no JWT check, and no 401/403 identity path. Today's role check is the document-class filter in `careops.policy.search_policy` and `careops.rag.filters.filter_chunks`.

## Two traces, two vocabularies

`GatewayResponse` on the tag is `status`, `route`, `answer`, `evidence`, `provenance`, `as_of`. Observed statuses are `ok` and `insufficient_evidence`.

The synchronous query outcome, once the protected route exists, is one of `allow`, `deny`, `abstain`, `stale_evidence`, `dependency_failure`.

| Baseline status | Query outcome |
|---|---|
| `ok` | `allow` |
| `insufficient_evidence` | `abstain` |
| no baseline equivalent | `deny`, `stale_evidence`, or `dependency_failure` |

Policy effective-date filtering is not `stale_evidence`. A stale policy is dropped. If nothing authorized remains, the baseline returns `insufficient_evidence`. `stale_evidence` means the data product missed `freshness_slo` (the design example is 1440 minutes for the current `daily` label). That path sets `model_called` false.

A multi-agent workflow trace is a wider record. It keeps the query fields and adds the control-plane fields from the system design. `decision`, `authorization`, and `outcome` are different columns:

```json
{
  "trace_id": "",
  "workflow_id": "",
  "workflow_version": 0,
  "principal_ref": "",
  "organization_ref": "",
  "route": "",
  "metric_contract": "available_appointments",
  "metric_contract_version": "",
  "policy_version": "",
  "matched_rule_ids": [],
  "source_ids": [],
  "freshness": {},
  "agents": [],
  "tools_invoked": [],
  "model_called": false,
  "proposal_id": "",
  "decision": "",
  "authorization": "",
  "expected_entity_version": null,
  "committed_entity_version": null,
  "outcome": "",
  "reason_code": ""
}
```

| Field | Who writes it | Example values |
|---|---|---|
| `decision` | Decision gate, which may be probabilistic | `COMPLETE`, `VERIFY_MORE`, `HUMAN_REVIEW`, `REJECT` |
| `authorization` | Deterministic policy | `ALLOW` or `DENY` |
| `outcome` | Authority-plane result of the attempt | `allow`, `deny`, `abstain`, `stale_evidence`, `dependency_failure`, or workflow terminal states in spec 003 |

`HUMAN_REVIEW` does not mean the database changed. `ALLOW` does not mean the commit succeeded. A commit still has to pass the expected entity version.

On a query that never opens a workflow, `workflow_id`, `agents`, `proposal_id`, and the version fields stay empty. TC-04 emits the query envelope from `careops.trace.emit` for `POST /v1/query` only. The demo path still returns today's `GatewayResponse`.

Envelope rules:

- One `trace_id` crosses HTTP, core, and any later event.
- Logs carry component, duration, outcome, and reason code.
- Logs omit secrets, tokens, and restricted document bodies.
- OpenTelemetry export is out of scope.
- One dependency timeout is implemented: class `dependency_timeout`, the same `trace_id`, and no invented metric. There is no retry framework.

## Governance contract

`semantic/access.json` is a role-to-class map. `provider_detail` values `masked`, `standard`, and `full` are not read by the evidence path. The target contract, enforced before evidence and model input, is:

```yaml
policy_id: provider-detail-access
policy_version:
rule_id: provider_detail_analyst
principal_roles: [analyst]
organization_scope: same_tenant
allowed_purposes: [operations_analysis]
allowed_classes: [standard_provider_detail]
field_actions:
  credential_notes: deny
  provider_name: mask
effective_from:
effective_to:
owner:
reviewer:
```

`field_actions` are `allow`, `mask`, `hash`, and `deny`. The example version `4` in the system design is not a published policy version. Denied and masked values are absent from model input.

Until TC-03, the enforced control remains document class plus role, applied before content is returned.

## Conformance target

Later cards compare one governed result across the core, `POST /v1/query`, and the CLI/demo adapter. MCP stays at `get_metric`, `explain_metric`, `search_policy`, and `resolve_entity` unless a separate qualification extends it.

Compared fields: metric id, contract version, value, period, authorization outcome, freshness, provenance, and denial or abstention reason. Adapters do not grow formulas. `careops.gateway.answer` and `careops.agent.answer` both stay. The agent metric path hardcodes Texas / Aetna; the gateway parses state and defaults to Texas. They match on the flagship question. Closing the general case is TC-05.

## Acceptance traced here

| Id | How this freeze meets it |
|---|---|
| ARCH-02 | No employer names, no production identity product, no certification claim. |
| ARCH-04 | Unauthorized and stale-data paths specify that the model is not called. |
| ARCH-06 | Demo role, CLI `--role`, and MCP `search_policy(role=)` stay on the tag. |
| ARCH-09 | A down side store or a timed-out dependency omits that evidence and does not invent a metric. |
