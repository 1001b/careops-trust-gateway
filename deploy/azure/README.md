# deploy/azure

Bicep template for a disposable CareOps demo environment.

## Resources

- Log Analytics workspace
- Key Vault (RBAC authorization)
- PostgreSQL Flexible Server (Burstable) + `careops` database + `VECTOR` extension allow-list
- Container Apps environment + app with system-assigned managed identity
- Scale: min 0 / max 2 (demo)

## Parameters

Copy `parameters.example.json` → `parameters.local.json` (gitignored pattern recommended) and set:

- `postgresAdminPassword` (strong, local only)
- `containerImage`
- `location` / `namePrefix` if needed

Never commit real passwords or API keys.

## Post-deploy checklist

1. Grant Container App MI **Key Vault Secrets User**
2. Create secrets `DATABASE-URL`, `GEMINI-API-KEY` (or OpenAI)
3. Open Postgres firewall for Container Apps egress / temporary admin IP
4. Run `python -m careops.storage.postgres` against the server
5. Hit `/health` and guided `/api/demo` scenarios

## Teardown

```bash
az group delete -n rg-careops-demo --yes --no-wait
```
