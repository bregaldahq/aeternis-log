---
name: flow-tenancy
description: >-
  End-to-end reference for multi-tenant isolation in AeternisLog (F14): API key
  → tenant resolution, tenant scoping of every MongoDB query, per-tenant Fabric
  channel and signing identity selection, and the chaincode's identity-derived
  tenant with tenant-scoped composite keys. Use for ANY change that touches
  auth config, tenantFrom, query filters, batch ids, tenant_channels /
  tenant_identities, or chaincode tenant logic — and when onboarding a new
  tenant. Pair with the integrity-reviewer agent.
---

# Flow: tenancy and isolation (API → Mongo → Fabric → chaincode)

Isolation is enforced at **two independent layers** that must agree:

1. **API/Mongo layer:** the tenant is resolved from the API key, and every
   query and update filters on `{tenant, domain}`.
2. **Ledger layer:** the chaincode derives the tenant from the **signed client
   identity** and keys state by `(tenant, batchID)`. A caller cannot name
   another tenant.

## The chain, hop by hop

| # | Hop | Where | Rule |
|---|---|---|---|
| 1 | Config | `api/pkg/config/config.go:42-69` (`AuthConfig`, `KeyToTenant`) | `auth.api_keys` → tenant `default`; `auth.tenants[{id, keys}]` → that id. Env `AUTH_TENANTS="a:k1,k2;b:k3"`. Keys can be `sha256:<hex>` |
| 2 | Auth middleware | `api/internal/middleware/middleware.go:187` (`APIKeyAuth`), `:217` (`matchAPIKey`) | Key from `auth.header_name` (default `X-API-Key`) or `Authorization: Bearer`. Constant-time compare over **all** keys (no early exit). Sets `c.Set("tenant", t)` |
| 3 | Handler | `api/internal/handlers/records.go:42` (`tenantFrom`) | Context tenant, else `"default"` (auth disabled) |
| 4 | Mongo | `api/internal/database/collections.go` | Every filter has `tenant` + `domain`. Unique index `(tenant, domain, id)` |
| 5 | Batch id | `api/internal/merkle/batch_processor.go:245` | `<tenant>-<domain>-<uuid8>`, a naming convention only (not security) |
| 6 | Channel | `api/pkg/config/config.go:171` (`ChannelForTenant`) | `fabric.tenant_channels[tenant]`, else `fabric.channel` |
| 7 | Identity | `api/internal/fabric/gateway.go:63` (`gatewayFor`) | `fabric.tenant_identities[tenant]` gateway session, else the default identity |
| 8 | Chaincode | `hybrid-architecture/chaincode/logchaincode.go:48` (`clientTenant`), `:64` (`batchKey`) | Tenant = cert attribute `tenant`, else MSP ID. Key = composite `("batch", tenant, batchID)` |

## Isolation levels in practice

| Deployment | API isolation | Ledger isolation |
|---|---|---|
| Auth off | none (all `default`) | one partition (default identity) |
| Auth on, no tenant identities/channels (**today's default**) | full per tenant | **org-level only**: every tenant anchors under the default identity, so all batches share one partition keyed by the default identity's tenant/MSP. Tenants are separated by the batch-id prefix and the API layer |
| + `tenant_channels` | full | separate ledger (channel) per mapped tenant |
| + `tenant_identities` (cert attr `tenant=<id>`) | full | cryptographic partition per tenant **within** a channel |

Provisioning a real tenant: `prod/scripts/register-tenant-identity.sh <tenant>`
→ mount the bundle → `fabric.tenant_identities.<tenant>` (yaml only, no env
override) → optional `create-tenant-channel.sh` + `fabric.tenant_channels`
→ make sure the tenant-scoped chaincode is deployed (`upgrade-chaincode.sh`).
See `docs/tenant-isolation-guarantee.md`.

## Invariants (release blockers)

1. The tenant never comes from a request body, query, path, or a
   client-controlled header other than the authenticated key.
2. Every Mongo read and write on `records` includes `tenant` **and**
   `domain`, including aggregations (`FindUnanchoredBatchScopes` and
   `DistinctPendingRecordScopes` group by tenant).
3. The chaincode never accepts a tenant argument. `batchID` content cannot
   influence the partition (`TestTenantNotSpoofableViaBatchID`).
4. Anchor **and** verify for a tenant use the **same** channel and identity.
   Otherwise the verify read lands in a different partition and reports
   `UNANCHORED`.
5. Cross-tenant access looks like "not found" (404 / `does not exist`), never
   "forbidden". Existence must not leak.
6. `KeyToTenant`: if the same key appears under two tenants, the **last one
   wins** (tenants override flat keys). Config review must reject duplicates.

## Known gaps

- `auth.enabled=false` silently puts everyone into `default`. Production
  configs must enable it (the ship-check gate should verify this).
- `fabric.tenant_identities` has **no env override**, only yaml.
- The public endpoint only sees default-identity batches
  (`flow-public-verification`).
- Pre-F14 batches (`batch_<id>` keys) are invisible after the tenant-scoped
  chaincode upgrade. Deploy on a clean ledger or re-key (see the doc).
- Legacy chaincode functions (`CreateLog`, `GetAllLogs`, …) are **not**
  tenant-scoped (`flow-chaincode`).
- Rate limiting is per client IP, not per tenant.

## Tests

- API: `api/internal/middleware/middleware_test.go` (`TestAPIKeyAuth`,
  `TestMatchAPIKey`), `hardening_test.go` (`TestMatchAPIKeyHashed`),
  `api/pkg/config/config_test.go` (`TestParseTenants`, `TestChannelForTenant`,
  `TestValidateAuth`), `api/internal/fabric/gateway_pool_test.go`
  (`TestGatewayForSelectsTenantIdentity`).
- Chaincode: `hybrid-architecture/chaincode/logchaincode_isolation_test.go`
  (all five tests, fake stub + fake client identity).
- **Gap:** there is no API-level test proving tenant B's key gets 404 on
  tenant A's record or batch (list, get, delete, batch, verify). The staging
  isolation run (`config.prod.yaml`, `staging-acme-key` vs
  `staging-default-key`) covers it manually. Add an automated handler test
  when you touch this.

## Changing it safely

- Red first: a cross-tenant negative test at the layer you touch (handler,
  query, or chaincode).
- New collection or query: add `tenant` + `domain` to the filter **and** an
  index through a migration.
- New chaincode function: derive the tenant with `clientTenant`, key with
  `batchKey`, and add an isolation test in the same file pattern.
- Always run the `integrity-reviewer` agent and update
  `docs/tenant-isolation-guarantee.md` plus the website page
  `security/tenant-isolation.md`.

## Debug recipes

```bash
# which tenant does a key map to? (auth enabled)
curl -s -H 'X-API-Key: staging-acme-key' localhost:5001/api/v1/audit/records | jq '.records[0].tenant'
# cross-tenant check: expect 404
curl -s -o /dev/null -w '%{http_code}\n' -H 'X-API-Key: staging-default-key' \
  localhost:5001/api/v1/audit/records/<acme-record-id>
```

## Related

`flow-http-middleware` · `flow-chaincode` · `flow-fabric-transport` ·
`flow-fabric-staging-network` · `flow-public-verification` · `flow-config-lifecycle`
