---
name: flow-batch-anchoring
description: >-
  Deep reference for how pending records become an anchored Merkle batch on
  Hyperledger Fabric: the auto-batch ticker, the forced POST
  /api/v1/{domain}/records/batch endpoint, the atomic claim in MongoDB, batch-id
  format, stamp-after-anchor, the Fabric-disabled path, failure marking, and the
  webhook/metrics side effects. Use when changing BatchProcessor, the claim
  queries, batch sizing/intervals, anchor error handling, or when records are
  stuck unbatched / pending / failed. Reconciliation of failed batches is in
  flow-batch-reconciliation.
---

# Flow: batching and anchoring

Records land unbatched (no `batch_id`). The batch processor periodically, or
on demand, claims up to N of them per `(tenant, domain)` into a fresh batch,
computes the Merkle root and writes it **write-once** to Fabric under the
tenant's identity and channel. Then it stamps the records with the root and
the tx id.

## Entry points

| What | Where |
|---|---|
| Wiring + start/stop | `api/cmd/api/main.go:167-183` |
| Processor type + `Anchorer` seam | `api/internal/merkle/batch_processor.go:23-67` |
| Ticker loop | `api/internal/merkle/batch_processor.go:139` (`autoBatchTicker`) |
| One batch | `api/internal/merkle/batch_processor.go:237` (`ProcessRecordBatch`) |
| Forced endpoint | `api/internal/handlers/records.go:329` (`ForceRecordBatch`) |
| Pending scopes | `api/internal/database/collections.go:315` (`DistinctPendingRecordScopes`) |
| Atomic claim | `api/internal/database/collections.go:137` (`ClaimRecordsForBatch`) |
| State stamps | `collections.go:176` (`SetRecordBatchMerkleRoot`), `:186` (`SetRecordBatchAnchored`), `:202` (`MarkRecordBatchFailed`) |
| Ledger call | `api/internal/fabric/client.go:67` (`StoreMerkleBatch` → chaincode `StoreMerkleRoot`) |
| Config | `batching.{enabled, auto_batch_size, auto_batch_interval}` in `api/pkg/config/config.go:185` |

## Triggers

1. **Auto ticker** (when `batching.enabled` and `auto_batch_interval > 0`,
   default 30 s). Each tick first runs `ReconcileBatches`, then, for every
   `(tenant, domain)` with pending records, calls
   `ProcessRecordBatch(…, auto_batch_size)` (default 100). One batch per scope
   per tick, sequentially. A backlog larger than `auto_batch_size` drains over
   several ticks.
2. **Forced**: `POST /api/v1/{domain}/records/batch` with optional
   `{"batch_size": n}`. Values ≤0, >1000 or missing mean 100. The request
   context gets 40 s. It is tenant-scoped via `tenantFrom(c)` and only batches
   the caller's own records.

`batching.enabled=false` only disables the ticker. The forced endpoint still
works.

## `ProcessRecordBatch` step by step

1. `batchID = "<tenant>-<domain>-<uuid[:8]>"`. The tenant prefix is only a
   naming convention. Isolation on the ledger comes from the chaincode's
   composite key, not the id (`flow-chaincode`).
2. **Claim** (`ClaimRecordsForBatch`):
   a. read up to `limit` pending ids (`batch_id` absent, not deleted) sorted
      `created_at ↑, id ↑`;
   b. `UpdateMany` with `id ∈ ids AND batch_id absent` →
      `$set {batch_id, anchor_status: "pending", batched_at: RFC3339 string}`.
      The `batch_id absent` guard makes concurrent claimers race-safe: each
      document is won by at most one batch;
   c. read back by `batch_id` in the same order. That set is exactly what this
      call won.
   Nothing claimed returns `(nil, nil)`, which the endpoint reports as "No
   pending records to batch".
3. **Root**: `CalculateRecordMerkleRoot(records)` recomputes the leaves from
   content (not from the stored `hash`).
4. **Fabric disabled** (`fabric.sync_enabled=false`): only `merkle_root` is
   stamped. `anchor_status` stays `pending`, no tx id, `Anchored=false`. The
   reconciler skips everything while Fabric is disabled, so these batches wait
   until it is enabled.
5. **Anchor** (30 s sub-context): `StoreMerkleBatch(tenant,
   ChannelForTenant(tenant), batchID, root, n, ids)` submits `StoreMerkleRoot`
   and **waits for the commit status**. An unknown or failed commit counts as
   a failure (`api/internal/fabric/gateway.go:209-218`).
6. **On failure**: `MarkRecordBatchFailed` sets `merkle_root` and
   `anchor_status: "failed"`. `batch_id` is kept, so the records never return
   to the pending pool and are never excluded forever. `FailedBatches++`, and
   the error propagates (the endpoint returns `500 batch_error`).
7. **On success**: `SetRecordBatchAnchored` sets `merkle_root`, `tx_id`,
   `anchor_status: "anchored"`. If that Mongo write fails, the batch is
   on-chain but local status lags. It is logged as a warning and fixed later
   by the reconciler (the write-once "already anchored" path). Then the
   webhook fires (async), `metrics.RecordAnchoredBatch` runs, stats update,
   and a `record batch created` log line is written.

## Record lifecycle (`anchor_status`)

```
(unbatched) --claim--> pending --anchor ok--> anchored
                          |  \--anchor err--> failed --reconcile ok--> anchored
                          \--(fabric disabled: stays pending with merkle_root)
```

## Invariants

- **Stamp after anchor**: `anchored` is written only after a confirmed commit.
- A claimed record is never released back to the pool. A failed batch keeps
  its membership and **original root** (the reconciler re-submits that root,
  never a recompute).
- Each batch is scoped to exactly one `(tenant, domain)`, and every query and
  update filters on both.
- The claim order is the verification order (`flow-merkle-tree`).
- Soft-deleted records are never claimed. Once claimed, deletion does not
  remove them from the batch.

## Tests

- `api/internal/merkle/f03_test.go`: `TestProcessRecordBatchAnchorFailureNotLost`
  (needs Mongo on `localhost:27017`, **skips silently otherwise**).
- `api/internal/database/mongodb_test.go`: `TestClaimRecordsForBatchAtomic`
  (concurrent claimers, needs Mongo).
- `api/internal/merkle/batch_processor_test.go`: lifecycle and stats.
- To inject ledger behavior, use the `fakeAnchorer` pattern in `f03_test.go`.

## Known gaps / drift

- `batching.batch_executor_workers` is only logged at boot. Batching is
  single-goroutine.
- `ProcessorStats` is exposed by `HealthHandler.GetStats`, but `/stats` is
  **not registered** in `registerRoutes`, so it is unreachable.
- Auto-batch processes scopes sequentially within one tick. A slow Fabric
  commit (up to 30 s) delays every other tenant.
- Multiple API replicas each run a ticker. The claim is race-safe, so this is
  correct, but the replicas compete.

## Changing it safely

- Write the failing test first with `fakeAnchorer` (anchor fails, anchor
  "already anchored", disabled).
- Never persist `anchored` before the commit status, and never clear
  `batch_id` on failure.
- If you change the claim sort, you must change `FindRecordsByBatchID`
  identically, and both must stay backward compatible with existing batches.
- Changing the batch-id format is safe for new batches only. Existing ids are
  keys on the ledger.

## Debug recipes

```bash
# force a batch for the caller's tenant
curl -s -XPOST localhost:5001/api/v1/contracts/records/batch \
  -H 'Content-Type: application/json' -d '{"batch_size":50}' | jq
# stuck states in Mongo
docker exec aeternislog-mongodb mongosh logdb --quiet --eval '
  db.records.aggregate([{$group:{_id:"$anchor_status",n:{$sum:1}}}])'
# API side
make api-logs | grep -E 'record batch created|anchor|reconcile'
```

`pending` for a long time means Fabric is disabled or unreachable (check
`/health` → `services.fabric`). `failed` means the reconciler will retry on
every tick. Look for `reconcile: re-anchor failed`.

## Related

`flow-batch-reconciliation` · `flow-merkle-tree` · `flow-fabric-transport` ·
`flow-chaincode` · `flow-webhook` · `flow-observability` · `flow-tenancy`
