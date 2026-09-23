---
name: flow-batch-reconciliation
description: >-
  Deep reference for AeternisLog's batch reconciler (F03): how batches left in
  anchor_status pending/failed are re-driven to Fabric, why the ORIGINAL stored
  root is re-submitted, and how the chaincode's write-once "already anchored"
  error makes retries idempotent. Use when batches are stuck pending/failed,
  when changing ReconcileBatches, the anchor error strings, or the write-once
  guard, or when reasoning about crash/timeout windows between Fabric commit
  and the Mongo status update.
---

# Flow: batch reconciliation (F03)

Anchoring can fail, or partly succeed: the commit lands on-chain but the Mongo
status write fails or times out. The reconciler closes those gaps. On every
auto-batch tick, **before** new batching, it re-submits every
`pending`/`failed` batch with its original root. The chaincode is write-once,
so re-submitting is always safe. An already-committed batch comes back as
"already anchored", and the reconciler treats that as success.

## Entry points

| What | Where |
|---|---|
| Reconciler | `api/internal/merkle/batch_processor.go:346` (`ReconcileBatches`) |
| Called from | `api/internal/merkle/batch_processor.go:153` (ticker, before auto-batch) |
| Work list | `api/internal/database/collections.go:223` (`FindUnanchoredBatchScopes`) |
| Idempotency detection | `api/internal/merkle/batch_processor.go:392` (`isAlreadyAnchored`) |
| Write-once guard | `hybrid-architecture/chaincode/logchaincode.go:329-341` |
| Commit-status handling | `api/internal/fabric/gateway.go:209-218` |

## Step by step

1. Return immediately when Fabric is disabled (`anchorer.Enabled()==false`).
2. Aggregate distinct `(tenant, domain, batch_id)` with
   `anchor_status ∈ {pending, failed}`.
3. For each: load its records (`FindRecordsByBatchID`, verification order).
   Skip on error or empty.
4. `root = records[0].MerkleRoot`, the root **stored at creation**, not a
   recompute. If a record was tampered with while the batch was unanchored,
   anchoring the recompute would launder the tampering into the ledger.
   Re-submitting the original root keeps the tampering detectable.
5. `StoreMerkleBatch(tenant, ChannelForTenant(tenant), batchID, root, n, ids)`.
   - **Success** → `SetRecordBatchAnchored(root, txID)`, webhook, metrics, log
     `reconcile: batch anchored`.
   - **Error containing "already anchored"** → the batch is already on-chain
     → `SetRecordBatchAnchored(root, records[0].TxID)` and continue.
   - **Other error** → warn `reconcile: re-anchor failed, will retry` and
     leave it for the next tick.

## Windows this closes

| Window | Outcome without reconciler | With reconciler |
|---|---|---|
| Fabric unreachable at batch time | batch stuck `failed` forever | re-anchored on a later tick |
| Commit status timed out (outcome unknown) | treated as failure → `failed` | re-submit → either anchors or "already anchored" |
| Committed, but Mongo `SetRecordBatchAnchored` failed | on-chain but `pending` locally | "already anchored" → status synced |
| Fabric disabled at batch time | `pending` with root | anchored once Fabric is enabled |

## Invariants

- Re-submit the **original** root, never a recompute.
- The write-once guard in the chaincode is what makes retry safe. Weakening it
  (allowing overwrite) turns the reconciler into a tampering vector.
- Idempotency hinges on the **error string** `already anchored`
  (chaincode: `batch %s is already anchored and cannot be overwritten`). If
  you rename that message you must update `isAlreadyAnchored`, or the
  reconciler will retry forever.
- The reconciler runs under the batch's tenant identity and channel, so the
  chaincode sees the same composite key as the original attempt.

## Known gaps

- On the "already anchored" path the tx id written is `records[0].TxID`, which
  is empty when the original success was never persisted. The record is
  marked anchored with no `tx_id`. It still verifies (verification reads the
  root from the chain), but audit reports and the webhook lack the tx id.
  A proper fix would query the ledger for the anchoring tx.
- The **"already anchored" path does not fire the webhook**, so a batch
  anchored during a Mongo blip never produces `batch.anchored`.
- The reconciler **only runs inside the auto-batch ticker**. With
  `batching.enabled=false` (as in `api/config.prod.yaml`) failed batches are
  never reconciled automatically.
- There is no backoff or attempt cap. A permanently failing batch is retried
  every tick, with one warning each time.
- The error-string matching (`"already anchored"`, `"does not exist"`) is
  brittle across chaincode versions. Typed errors or status codes would be
  more robust.

## Tests

`api/internal/merkle/f03_test.go` (Mongo required, skips otherwise):
`TestReconcileRetriesFailedAndAnchors`,
`TestReconcileAlreadyAnchoredTreatedAsSuccess`,
`TestProcessRecordBatchAnchorFailureNotLost`. Chaincode side:
`TestCrossTenantCannotOverwrite` (write-once plus tenant scoping).

Run with Mongo up (`make dev`), otherwise these are silent skips and prove
nothing:

```bash
make dev && cd api && go test ./internal/merkle/ -run 'Reconcile|AnchorFailure' -v
```

## Debug recipes

```bash
docker exec aeternislog-mongodb mongosh logdb --quiet --eval '
  db.records.aggregate([{$match:{anchor_status:{$in:["pending","failed"]}}},
    {$group:{_id:{t:"$tenant",d:"$domain",b:"$batch_id"},n:{$sum:1}}}])'
make api-logs | grep reconcile
```

To check whether a stuck batch is actually on-chain, query the ledger
directly (`flow-fabric-dev-network` → `peer chaincode query … QueryMerkleBatch`)
**with the same tenant identity**. Under another identity it reads as
"does not exist".

## Related

`flow-batch-anchoring` · `flow-chaincode` · `flow-fabric-transport` ·
`flow-webhook`
