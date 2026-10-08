# Azure deployment — CareOps Trust Gateway

Architecture decision / deployment walkthrough for a disposable hiring demo. Not a beginner Azure tutorial.

## Architecture

```text
Internet → Azure Container Apps (ca-*-api)
              ↓ Managed Identity
         Azure Key Vault  (GEMINI/OPENAI + DATABASE URL)
              ↓
         FastAPI app (uvicorn) → governed CareOps core
              ↓
         Azure Database for PostgreSQL Flexible Server
              ├── operational tables + gold view
              └── document_chunks + pgvector
```

| Service | Why |
|---|---|
| Container Apps | Simple HTTPS container host; scale-to-zero friendly |
| PostgreSQL Flexible Server | Production-style structured authority + pgvector |
| Key Vault + system-assigned MI | Secrets never in image/Git; browser never sees LLM keys |
| Log Analytics | Minimal request/ops visibility |

Optional later: ACR if GHCR/public registry is undesirable; App Insights if Log Analytics is insufficient.

## Identity

1. Container App gets **system-assigned Managed Identity**.
2. Grant that identity **Key Vault Secrets User** on the demo vault.
3. Container App secret refs pull `DATABASE-URL` and `GEMINI-API-KEY` (or `OPENAI-API-KEY`) from Key Vault.

Least privilege: no Contributor on the subscription for the app identity; vault secret read only.

## Data

- Same synthetic CSVs as local (`data/raw`, `knowledge/`).
- Bootstrap: `python -m careops.storage.postgres` (schema + seed + local-hash chunk embeddings).
- Metrics still come from `gold_provider_availability_daily` (governed SQL), not from the LLM.
- Local SQLite remains default when `CAREOPS_DATABASE_URL` is unset.

## Deployment (reproduce)

```bash
# 0) Prereqs: az login, Docker, gh auth (correct account)
az account show

# 1) Resource group
az group create -n rg-careops-demo -l eastus2

# 2) Build/push image (example: GHCR)
docker build -t ghcr.io/<you>/careops-trust-gateway:latest .
docker push ghcr.io/<you>/careops-trust-gateway:latest

# 3) Deploy infra (edit parameters.example.json first — no real secrets in Git)
cp deploy/azure/parameters.example.json deploy/azure/parameters.local.json
# edit image + postgres password locally
az deployment group create \
  -g rg-careops-demo \
  -f deploy/azure/main.bicep \
  -p @deploy/azure/parameters.local.json

# 4) After deploy: create Key Vault secrets, grant MI access, allow Postgres access,
#    enable vector extension if needed, then:
export CAREOPS_DATABASE_URL='postgresql://...'
python -m careops.storage.postgres

# 5) Verify
curl https://<container-app-fqdn>/health
```

Exact role assignments and firewall steps are environment-specific; record what you actually ran in the private qualification log.

## Cost (demo-oriented)

- PostgreSQL Burstable `Standard_B1ms` + 32GB is the main continuous cost.
- Container Apps `minReplicas: 0` reduces idle compute.
- LLM spend is usage-based (Gemini/OpenAI); keep synthesis optional.
- Log Analytics 30-day retention is enough for a demo.

## Production evolution

- Private endpoints / VNet integration
- Azure AD auth for operators
- Managed Postgres HA, PITR policy aligned to compliance
- Separate staging/prod + promotion pipeline
- Stronger WAF / abuse controls than in-app rate limits
- Enterprise secret rotation and CMK if required

## Teardown

```bash
az group delete -n rg-careops-demo --yes --no-wait
```

Also delete any pushed demo images/tags you no longer need. The stack is intentionally disposable.
