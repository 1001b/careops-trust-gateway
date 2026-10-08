# Azure adaptation — operator playbook

Run these yourself. Do not commit secrets. Resource names are employer-neutral.

## A) Local regression (required before cloud)

```bash
# from repo root
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev,web,postgres,rag]'
unset CAREOPS_DATABASE_URL
python -m careops.bootstrap
python -m pytest -q
python -m evals.run
uvicorn app.main:app --port 8000
```

```bash
curl -s http://127.0.0.1:8000/health
curl -s -X POST http://127.0.0.1:8000/api/demo \
  -H 'Content-Type: application/json' \
  -d '{"question":"Why did in-network appointment availability in Texas decline last week?","role":"analyst","mode":"governed","synthesize":false}'
```

```bash
# ACA requires linux/amd64. On Apple Silicon use buildx (legacy docker build --platform often fails).
docker run --privileged --rm tonistiigi/binfmt --install all
docker buildx create --name careops-amd64 --driver docker-container --use 2>/dev/null || docker buildx use careops-amd64
docker buildx inspect --bootstrap
```


## B) Azure login + resource group

```bash
az login
az account show
az group create -n rg-careops-demo -l eastus2
```

## C) Build/push image (GHCR example)

```bash
# ensure gh is the correct GitHub user
gh auth status
# PAT needs write:packages (+ read:packages). Do not type angle brackets.
export GHCR_USER=1001b   # your GitHub username/org that owns the package
printf '%s\n' "$GHCR_PAT" | docker login ghcr.io -u "$GHCR_USER" --password-stdin
# or: printf '%s\n' "$(gh auth token)" | docker login ghcr.io -u "$GHCR_USER" --password-stdin
docker buildx build --platform linux/amd64 \
  -t "ghcr.io/${GHCR_USER}/careops-trust-gateway:latest" \
  --push .
# Must show Architecture: amd64 (digest must change from the old arm64 image)
docker buildx imagetools inspect "ghcr.io/${GHCR_USER}/careops-trust-gateway:latest"
# Make package public (required for ACA pull without registry credentials):
#   https://github.com/users/1001b/packages/container/careops-trust-gateway/settings
#   → Change visibility → Public
# Verify anonymous pull works (should NOT be 401):
#   curl -sI "https://ghcr.io/v2/1001b/careops-trust-gateway/manifests/latest" | head -n1
```



## D) Deploy Bicep

```bash
cp deploy/azure/parameters.example.json deploy/azure/parameters.local.json
# edit (local only): containerImage = your public GHCR image
# Prefer password on CLI (not committed). databaseUrl is computed in Bicep.
export PGPW='YourStrongPasswordHere'
az deployment group create \
  -g rg-careops-demo \
  -f deploy/azure/main.bicep \
  -p @deploy/azure/parameters.local.json \
  --parameters postgresAdminPassword="$PGPW"
```

Save outputs: Key Vault name, Postgres FQDN, Container App FQDN, MI principalId.
Phase 1 success = public `https://<containerAppFqdn>/health` green.

## E) Phase 2 (after /health): KV refs + MI + DB bootstrap + UAT

Uses existing Phase 1 resources only (`deploy/azure/phase2.sh`). Does not recreate the RG.

```bash
chmod +x deploy/azure/phase2.sh
export PG_ADMIN_PASSWORD='...'          # same password as Phase 1
# optional:
# export GEMINI_API_KEY='...'           # new key; never paste into chat

# Stepwise (recommended) or: ./deploy/azure/phase2.sh all
./deploy/azure/phase2.sh discover
./deploy/azure/phase2.sh rbac
./deploy/azure/phase2.sh secrets
./deploy/azure/phase2.sh wire
./deploy/azure/phase2.sh bootstrap   # needs psql + pip install -e '.[postgres,rag]'
./deploy/azure/phase2.sh verify
```

Success: `/health` still ok with `database=postgres`; `/api/demo` returns governed evidence. With Gemini set, `/health` may show `llm_configured=true`.

## F) Public UAT

```bash
curl -s https://<CA_FQDN>/health
curl -s -X POST https://<CA_FQDN>/api/demo \
  -H 'Content-Type: application/json' \
  -d '{"question":"Why did in-network appointment availability in Texas decline last week?","role":"analyst","mode":"governed","synthesize":false}'
```

## G) Teardown

```bash
az group delete -n rg-careops-demo --yes --no-wait
```

Record successful cloud steps in your private qualification notes (do not commit secrets or subscription-specific IDs).
