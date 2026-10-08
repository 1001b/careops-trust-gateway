#!/usr/bin/env bash
# Phase 2 against the EXISTING Phase 1 stack (no RG recreate, no Bicep rewrite).
# Prerequisites: Phase 1 /health green; export PG_ADMIN_PASSWORD; optional GEMINI_API_KEY.
set -Eeuo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-careops-demo}"
CONTAINER_APP="${CONTAINER_APP:-ca-careops-demo-api-eastus2}"
PG_ADMIN_USER="${PG_ADMIN_USER:-careopsadmin}"
PG_DATABASE="${PG_DATABASE:-careops}"
STEP="${1:-all}"

die(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
log(){ printf '\n==> %s\n' "$*"; }
need(){ command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"; }

need az; need curl; need python3
az account show >/dev/null 2>&1 || die "Run: az login"
[[ -n "${PG_ADMIN_PASSWORD:-}" ]] || die "Export PG_ADMIN_PASSWORD (same password used in Phase 1)"

discover(){
  log "Discovering resources in $RESOURCE_GROUP"
  KEY_VAULT="$(az keyvault list -g "$RESOURCE_GROUP" --query "[0].name" -o tsv)"
  PG_SERVER="$(az postgres flexible-server list -g "$RESOURCE_GROUP" --query "[0].name" -o tsv)"
  [[ -n "$KEY_VAULT" ]] || die "No Key Vault in $RESOURCE_GROUP"
  [[ -n "$PG_SERVER" ]] || die "No Postgres Flexible Server in $RESOURCE_GROUP"
  PG_FQDN="$(az postgres flexible-server show -g "$RESOURCE_GROUP" -n "$PG_SERVER" --query fullyQualifiedDomainName -o tsv)"
  PRINCIPAL_ID="$(az containerapp show -g "$RESOURCE_GROUP" -n "$CONTAINER_APP" --query identity.principalId -o tsv)"
  [[ -n "$PRINCIPAL_ID" && "$PRINCIPAL_ID" != "null" ]] || die "Container App has no system-assigned identity"
  APP_FQDN="$(az containerapp show -g "$RESOURCE_GROUP" -n "$CONTAINER_APP" --query properties.configuration.ingress.fqdn -o tsv)"
  KV_ID="$(az keyvault show -n "$KEY_VAULT" -g "$RESOURCE_GROUP" --query id -o tsv)"
  DATABASE_URL="$(PG_ADMIN_USER="$PG_ADMIN_USER" PG_ADMIN_PASSWORD="$PG_ADMIN_PASSWORD" PG_FQDN="$PG_FQDN" PG_DATABASE="$PG_DATABASE" python3 - <<'PY'
from urllib.parse import quote
import os
print("postgresql://{}:{}@{}:5432/{}?sslmode=require".format(
    quote(os.environ["PG_ADMIN_USER"], safe=""),
    quote(os.environ["PG_ADMIN_PASSWORD"], safe=""),
    os.environ["PG_FQDN"],
    quote(os.environ["PG_DATABASE"], safe=""),
))
PY
)"
  printf 'Key Vault:     %s\n' "$KEY_VAULT"
  printf 'Postgres:      %s (%s)\n' "$PG_SERVER" "$PG_FQDN"
  printf 'Container App: %s\n' "$CONTAINER_APP"
  printf 'MI principal:  %s\n' "$PRINCIPAL_ID"
  printf 'App FQDN:      %s\n' "$APP_FQDN"
}

rbac(){
  discover
  log "Grant deployer Key Vault Secrets Officer + MI Secrets User"
  ME="$(az ad signed-in-user show --query id -o tsv)"
  az role assignment create \
    --assignee-object-id "$ME" \
    --assignee-principal-type User \
    --role "Key Vault Secrets Officer" \
    --scope "$KV_ID" \
    --only-show-errors -o none || true
  az role assignment create \
    --assignee-object-id "$PRINCIPAL_ID" \
    --assignee-principal-type ServicePrincipal \
    --role "Key Vault Secrets User" \
    --scope "$KV_ID" \
    --only-show-errors -o none || true
  log "Waiting 30s for RBAC propagation"
  sleep 30
}

secrets(){
  discover
  log "Writing Key Vault secrets (values never printed)"
  az keyvault secret set --vault-name "$KEY_VAULT" --name DATABASE-URL --value "$DATABASE_URL" -o none
  if [[ -n "${GEMINI_API_KEY:-}" ]]; then
    az keyvault secret set --vault-name "$KEY_VAULT" --name GEMINI-API-KEY --value "$GEMINI_API_KEY" -o none
  fi
}

wire_aca(){
  discover
  log "Pointing ACA secrets at Key Vault refs"
  # Secret id includes a version; strip it so ACA tracks latest
  DB_URI="$(az keyvault secret show --vault-name "$KEY_VAULT" --name DATABASE-URL --query id -o tsv | python3 -c 'import sys; u=sys.stdin.read().strip(); print(u.rsplit("/",1)[0])')"

  for i in $(seq 1 24); do
    if az containerapp secret set -g "$RESOURCE_GROUP" -n "$CONTAINER_APP" \
      --secrets "database-url=keyvaultref:${DB_URI},identityref:system" \
      --only-show-errors -o none; then
      break
    fi
    printf 'KV ref not ready (%s/24); sleeping 10s\n' "$i"
    sleep 10
    [[ "$i" -lt 24 ]] || die "ACA could not bind database-url Key Vault ref"
  done

  if [[ -n "${GEMINI_API_KEY:-}" ]]; then
    G_URI="$(az keyvault secret show --vault-name "$KEY_VAULT" --name GEMINI-API-KEY --query id -o tsv | python3 -c 'import sys; u=sys.stdin.read().strip(); print(u.rsplit("/",1)[0])')"
    az containerapp secret set -g "$RESOURCE_GROUP" -n "$CONTAINER_APP" \
      --secrets "gemini-api-key=keyvaultref:${G_URI},identityref:system" \
      --only-show-errors -o none
    az containerapp update -g "$RESOURCE_GROUP" -n "$CONTAINER_APP" \
      --set-env-vars \
        "CAREOPS_DATABASE_URL=secretref:database-url" \
        "GEMINI_API_KEY=secretref:gemini-api-key" \
        "CAREOPS_LLM_PROVIDER=auto" \
      --only-show-errors -o none
  else
    az containerapp update -g "$RESOURCE_GROUP" -n "$CONTAINER_APP" \
      --set-env-vars "CAREOPS_DATABASE_URL=secretref:database-url" \
      --only-show-errors -o none
  fi
}

bootstrap_db(){
  discover
  command -v psql >/dev/null 2>&1 || die "Install psql (brew install libpq && brew link --force libpq)"
  log "Firewall: allow Azure services + current client IP"
  az postgres flexible-server firewall-rule create \
    -g "$RESOURCE_GROUP" --server-name "$PG_SERVER" \
    --name AllowAzureServices \
    --start-ip-address 0.0.0.0 --end-ip-address 0.0.0.0 \
    --only-show-errors -o none || true
  MYIP="$(curl -fsS --max-time 10 https://api.ipify.org)"
  az postgres flexible-server firewall-rule create \
    -g "$RESOURCE_GROUP" --server-name "$PG_SERVER" \
    --name AllowCurrentClient \
    --start-ip-address "$MYIP" --end-ip-address "$MYIP" \
    --only-show-errors -o none || true

  log "Allowlist vector extension"
  az postgres flexible-server parameter set \
    -g "$RESOURCE_GROUP" --server-name "$PG_SERVER" \
    --name azure.extensions --value VECTOR \
    --only-show-errors -o none || \
  az postgres flexible-server parameter set \
    -g "$RESOURCE_GROUP" --server-name "$PG_SERVER" \
    --name azure.extensions --value vector \
    --only-show-errors -o none

  log "Schema + synthetic seed + local-hash embeddings"
  ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
  (
    cd "$ROOT"
    export CAREOPS_DATABASE_URL="$DATABASE_URL"
    python3 -m careops.storage.postgres
  )
}

verify(){
  discover
  log "Health + governed demo (no synthesis)"
  for i in $(seq 1 24); do
    code="$(curl -sS -o /tmp/careops-p2-health.json -w '%{http_code}' --max-time 15 \
      "https://${APP_FQDN}/health" || echo ERR)"
    printf 'health=%s (%s/24)\n' "$code" "$i"
    [[ "$code" =~ ^2 ]] && break
    sleep 10
  done
  cat /tmp/careops-p2-health.json; echo
  [[ "$(python3 -c 'import json;print(json.load(open("/tmp/careops-p2-health.json"))["status"])')" == "ok" ]] \
    || die "Health not ok"

  curl -sS -X POST "https://${APP_FQDN}/api/demo" \
    -H 'Content-Type: application/json' \
    -d '{"question":"Why did in-network appointment availability in Texas decline last week?","role":"analyst","mode":"governed","synthesize":false}' \
    | tee /tmp/careops-p2-demo.json
  echo
  log "Phase 2 verify submitted. Inspect /tmp/careops-p2-demo.json for governed evidence."
}

all(){
  rbac
  secrets
  wire_aca
  bootstrap_db
  verify
  log "PHASE 2 complete (KV refs + bootstrap + UAT probe)."
}

case "$STEP" in
  discover) discover ;;
  rbac) rbac ;;
  secrets) secrets ;;
  wire|wire_aca) wire_aca ;;
  bootstrap|bootstrap_db) bootstrap_db ;;
  verify) verify ;;
  all) all ;;
  *) echo "Usage: $0 {discover|rbac|secrets|wire|bootstrap|verify|all}"; exit 2 ;;
esac
