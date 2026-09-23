---
name: flow-batch-verification
description: >-
  Deep reference for POST /api/v1/{domain}/records/verify/{batchId}: how
  AeternisLog recomputes a batch's Merkle root from current MongoDB content,
  reads the authoritative root from Fabric, classifies the result
  (VALID / CORRUPTED / UNANCHORED, anchor status ANCHORED / UNANCHORED /
  UNKNOWN) and maps it to 200/409/404. Use when touching VerifyRecordBatch,
  buildVerifyResponse, anchoredRoot, the verify handler or its response model,
  or when a verify result looks wrong (false CORRUPTED, UNKNOWN in prod).
---

# Flow: batch verification

This is the product's core promise. Given a batch id, recompute the root from
what is in MongoDB **now**, and compare it with the root committed on the
ledger when the batch was anchored. If any record's content changed, its leaf
changes, the root changes, and the answer is `409 CORRUPTED`.

## Entry points

| What | Where |
|---|---|
| Handler (status mapping) | `api/internal/handlers/records.go:375` (`VerifyRecordBatch`) |
| Orchestration | `api/internal/merkle/batch_processor.go:318` (`VerifyRecordBatch`) |
| Ledger read + classification | `api/internal/merkle/batch_processor.go:173` (`anchoredRoot`) |
| Decision table | `api/internal/merkle/batch_processor.go:198` (`buildVerifyResponse`) |
| Response + enums | `api/internal/models/merkle.go:44-75` |
| Record read (order!) | `api/internal/database/collections.go:126` (`FindRecordsByBatchID`) |
| Metric | `metrics.RecordVerification(domain, valid)` |

## Step by step

1. Handler: 10 s context, `tenantFrom(c)`, domain and batch id from the path.
2. `FindRecordsByBatchID(tenant, domain, batchID)`, ordered
   `created_at ↑, id ↑`, **including soft-deleted records** (they are still
   leaves of the anchored tree). Zero records → error → handler returns
   `404 not_found`. A batch owned by another tenant also gives 404, with no
   existence leak.
3. `storedRoot = records[0].MerkleRoot` (informational only when the batch is
   anchored).
4. `recalculated = CalculateRecordMerkleRoot(records)` recomputes **every
   leaf from content** with the batch's recorded `hash_version`. The stored
   `hash` field is never used.
5. `anchoredRoot(tenant, ChannelForTenant(tenant), batchID)` calls chaincode
   `QueryMerkleBatch` under the tenant's identity:
   - ok with `merkle_root` → `(root, ANCHORED)`
   - error containing `does not exist` → `("", UNANCHORED)`
   - Fabric disabled, other error, or empty root → `("", UNKNOWN)` (warning
     logged)
6. `buildVerifyResponse` decides:

| AnchorStatus | IsValid | Integrity | Meaning |
|---|---|---|---|
| `ANCHORED` | `recalculated == onChain` | `VALID` / `CORRUPTED` | The ledger is the source of truth. Tampering with content **and** the stored root is still caught |
| `UNANCHORED` | `false` | `UNANCHORED` | Batch not on the ledger, so integrity cannot be proven |
| `UNKNOWN` | `stored == recalculated` | `VALID` / `CORRUPTED` | Ledger not consulted. **Local consistency only** (the message says so) |

7. Handler: `IsValid` → `200`, otherwise **`409`** (covers `CORRUPTED` **and**
   `UNANCHORED`). The body is always the full `VerifyBatchResponse`.

## Response shape

```json
{ "batch_id": "...", "is_valid": true, "num_logs": 3,
  "original_merkle_root": "...", "recalculated_merkle_root": "...",
  "on_chain_merkle_root": "...", "anchor_status": "ANCHORED",
  "integrity": "VALID", "message": "Batch integrity verified against the on-chain anchor" }
```

Metric caveat: `integrity_verifications_total{result}` records `VALID` or
`CORRUPTED` from `IsValid` only, so an `UNANCHORED` batch is counted as
**CORRUPTED**. Alerts on that counter fire for unanchored batches too. Keep
this in mind (or fix the labeling) before alerting on it.

`num_logs` is a legacy field name (it counts records). Renaming it is a
breaking API change for the SDKs and dashboard.

## Invariants

1. Recompute from **current content**. Never compare the stored `hash` or
   stored `merkle_root` with the chain.
2. When `ANCHORED`, decide **only** against the on-chain root.
3. Read records in the **same order** used at claim time.
4. Read soft-deleted records too. Filtering them out would make every batch
   with a deletion look corrupted.
5. The ledger read runs under the **tenant's** identity and channel.
6. `UNKNOWN` must never be presented as proof. It is disclosed in
   `anchor_status` and `message`.

## Tests

- `api/internal/merkle/verify_test.go` (`TestBuildVerifyResponse`): the full
  decision table, including "content and stored root both tampered, ANCHORED →
  CORRUPTED".
- `api/internal/models/*_test.go`: root recomputation.
- `api/internal/metrics/metrics_test.go` (`TestRecordVerification`).
- Live tamper test: `api/scripts/e2e-test.sh` ("Tamper detection" section,
  run by `make smoke`) mutates a batched document through `mongosh` and
  expects `409` + `CORRUPTED`. It needs the full stack (`make up`) and a
  `MONGO` container name. With `MONGO=""` the tamper step is skipped.
- **Gap:** there is no unit-level HTTP test of the verify handler (status
  mapping 200/409/404). Add one with a fake `Anchorer` when you touch it.

## Changing it safely

- Add rows to the `TestBuildVerifyResponse` table **first**.
- Any new status must keep `IsValid=false` unless the on-chain root was
  actually compared.
- If you rename the chaincode's `does not exist` message, update both
  `anchoredRoot` and `PublicHandler.GetAnchor`.
- Changes to the response model affect Swagger, both SDKs, the dashboard
  (`examples/dashboard`) and the website docs (`guides/verify-integrity.md`,
  `api/errors.md`).
- Run the `integrity-reviewer` agent.

## Debug recipes

```bash
curl -s -XPOST localhost:5001/api/v1/<domain>/records/verify/<batchId> | jq
# simulate tampering (dev only!) then verify again → expect 409 CORRUPTED
docker exec aeternislog-mongodb mongosh logdb --quiet --eval '
  db.records.updateOne({batch_id:"<batchId>"},{$set:{"payload.amount":999}})'
```

`UNKNOWN` in an environment with Fabric means the gateway query failed. Check
`/health` → `services.fabric` and the log line
`on-chain anchor lookup failed`. A false `CORRUPTED` with no tampering usually
means ordering, a mixed `hash_version`, or a record missing from the read.

## Related

`flow-merkle-tree` · `flow-hash-scheme` · `flow-public-verification` ·
`flow-fabric-transport` · `flow-chaincode` · `flow-sdk-go`
