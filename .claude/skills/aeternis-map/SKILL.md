---
name: aeternis-map
description: >-
  Router/index for the AeternisLog harness. Use FIRST when starting any task
  in this repo and you are not sure which part of the system it touches — it
  maps a task or symptom to the flow-* skill(s) that explain that part in
  depth (ingest, hash, Merkle, batching, reconciliation, verification, public
  proof, tenancy, middleware, chaincode, Fabric transport/networks, WAL,
  Mongo/migrations, Redis, webhook, reports, observability, config, SDKs,
  local stack, Helm, tests, docs site) and to the workflow skills
  (aeternis-dev, ship-check) and the integrity-reviewer agent.
---

# AeternisLog harness map

## How the harness is organized

- **Workflow skills** say *how to work*: `aeternis-dev` (the TDD dev loop)
  and `ship-check` (the pre-PR gate).
- **Flow skills (`flow-*`)** say *how the system works*: entry points
  (`file:line`), step-by-step behavior, invariants, failure modes, tests,
  how to change it safely, debug recipes, and known gaps.
- **Agent** `integrity-reviewer`: a read-only audit of any diff touching
  hashing, Merkle, anchoring, verification, tenancy, chaincode, migrations or
  auth.
- `CLAUDE.md`: the golden rules and invariants. It always applies.

Load the flow skill(s) for the area **before** editing. Each one names its
neighbors under "Related".

## The core path at a glance

```
client ─POST /api/v1/{domain}/records─► [middleware: reqid, log, CORS, body cap, headers, metrics, rate limit]
   ─► [ValidateDomain, APIKeyAuth → tenant] ─► CreateRecord: v2 leaf hash ─► WAL append+fsync ─► Mongo insert
ticker (30s) / POST …/records/batch ─► reconcile pending|failed ─► claim (tenant,domain) ─► Merkle v2 root
   ─► Fabric Gateway (tenant identity + channel) ─► chaincode StoreMerkleRoot (write-once, key=(tenant,batch))
   ─► stamp anchored + tx_id ─► webhook batch.anchored + metrics
POST …/records/verify/{batch} ─► recompute leaves from Mongo ─► QueryMerkleBatch on-chain ─► VALID | 409 CORRUPTED | UNANCHORED
GET /public/anchors/{batch} ─► default identity/channel ─► root + timestamp only
```

## Task → skill

| If the task / symptom is about… | Load |
|---|---|
| Creating records, request fields, 201/400/409 on create | `flow-record-ingest` |
| What is hashed, `hash_version`, `hash_fields`, canonical JSON, SDK hash mismatch | `flow-hash-scheme` |
| Merkle root construction, odd nodes, ordering, CVE-2012-2459 | `flow-merkle-tree` |
| Batching, auto-batch interval/size, forced batch, stuck unbatched records | `flow-batch-anchoring` |
| Batches stuck `pending`/`failed`, retries, "already anchored" | `flow-batch-reconciliation` |
| Verify endpoint, VALID/CORRUPTED/UNANCHORED/UNKNOWN, 409 | `flow-batch-verification` |
| Unauthenticated auditor endpoint, public 404s | `flow-public-verification` |
| Listing, pagination/cursors, get-by-id, soft delete | `flow-record-read-delete` |
| API keys, tenants, cross-tenant isolation, per-tenant channel/identity | `flow-tenancy` |
| Middleware order, CORS, body limit, rate limit, new routes | `flow-http-middleware` |
| Smart contract functions, write-once, `clientTenant`, chaincode tests | `flow-chaincode` |
| Gateway gRPC, identities, endorsement/commit errors, Fabric timeouts | `flow-fabric-transport` |
| Dev Fabric network up/reset, chaincode not updating, cold peers | `flow-fabric-dev-network` |
| 3-org Raft staging network, CA enroll, chaincode upgrade, tenant channels, DR drills | `flow-fabric-staging-network` |
| Crash durability, WAL recovery, single-replica constraint | `flow-wal` |
| Mongo schema, indexes, migrations, "schema version mismatch" | `flow-mongo-migrations` |
| Redis usage, shared rate-limit store, degraded Redis | `flow-redis` |
| `batch.anchored` callback, HMAC signature, retries | `flow-webhook` |
| Audit report JSON/PDF, period filters | `flow-audit-reports` |
| Logs, metrics, /health, alerts, Grafana | `flow-observability` |
| Config keys, env overrides, boot/shutdown order, native run | `flow-config-lifecycle` |
| Go SDK | `flow-sdk-go` |
| Python SDK, `aeternislog` CLI | `flow-sdk-python` |
| make targets, compose, ports, dashboard, WSL/Docker quirks | `flow-local-stack` |
| Helm chart, K8s secrets, migration Job, replicas | `flow-kubernetes-helm` |
| Which tests to write/run, skipped suites, e2e smoke | `flow-testing-strategy` |
| Website content, Starlight, Pages deploy, which docs to update | `flow-docs-site` |

Cross-cutting changes load several. For example, "add a per-tenant webhook
URL" needs `flow-webhook` + `flow-tenancy` + `flow-config-lifecycle` +
`flow-kubernetes-helm`.

## Standard loop

1. `aeternis-map` → pick the flow skills → read them.
2. `aeternis-dev`: red test → green → widen the suites
   (`flow-testing-strategy`).
3. Integrity or isolation surface touched → the `integrity-reviewer` agent.
4. Docs to update → the flow skill's "Changing it safely" section plus
   `flow-docs-site`.
5. `ship-check` before the PR.

## Keeping the harness true

The flow skills cite `file:line` and describe current behavior, including
**known gaps**. When your change moves code, fixes a gap, or alters behavior,
**update the affected flow skill in the same PR**. A stale skill is worse
than none. When a gap listed in a skill is fixed, delete it from the list.
