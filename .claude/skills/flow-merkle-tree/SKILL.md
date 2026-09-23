---
name: flow-merkle-tree
description: >-
  Deep reference for how AeternisLog folds record leaf hashes into a batch
  Merkle root: v1 (duplicate-last, legacy) vs v2 (0x01 node tag, odd node
  promoted — CVE-2012-2459 safe), leaf ordering, version selection, and the
  three independent implementations (API, Go SDK, Python SDK) plus the legacy
  copy in the chaincode. Use when touching BuildMerkleTree*/CombineHashes*,
  batch ordering, or when a recomputed root disagrees with the anchored one.
  For the leaf hash itself see flow-hash-scheme.
---

# Flow: Merkle root construction

A batch is an **ordered** list of records. Each record gives one leaf
(`flow-hash-scheme`). The leaves are folded pairwise, bottom-up, into one root.
That root is the only thing anchored on Fabric, so the whole integrity
guarantee rests on this function being deterministic and structure-sensitive.

## Entry points

| What | Where |
|---|---|
| Batch root (version-dispatching) | `api/internal/models/record.go:153` (`CalculateRecordMerkleRoot`) |
| v2 tree | `api/internal/models/merkle.go:149` (`BuildMerkleTreeV2`), `:138` (`CombineHashesV2`) |
| v1 tree (legacy) | `api/internal/models/merkle.go:85` (`BuildMerkleTree`), `:78` (`CombineHashes`) |
| Leaf order (claim + verify) | `api/internal/database/collections.go:110-131` (sort `created_at ↑, id ↑`) |
| Go SDK | `sdk/go/record.go:110` (`MerkleRoot`), `:154` (`buildMerkleTreeV2`) |
| Python SDK | `sdk/python/aeternislog/record.py:178` (`merkle_root`) |
| Chaincode copy (legacy v1 only) | `hybrid-architecture/chaincode/logchaincode.go:245` (`BuildMerkleTree`) |

## Algorithm

**v2 (current):**
```
level = leaves (hex strings)
while len(level) > 1:
    next = []
    for i in 0,2,4,…:
        if i+1 < len(level): next.append( hex(SHA-256(0x01 ‖ level[i] ‖ level[i+1])) )
        else:                next.append( level[i] )        # promote, never duplicate
    level = next
root = level[0]            # 0 leaves → "" ; 1 leaf → the leaf itself
```

**v1 (legacy):** the same pairing, but an odd level **duplicates** its last
node, and nodes are `hex(SHA-256(left ‖ right))` with no tag. Duplicating the
last node lets `[a,b,c]` and `[a,b,c,c]` share a root, which is the
CVE-2012-2459 weakness. v1 is kept **only** so pre-v2 anchors still verify.

Detail that trips people up: children are concatenated as **hex text**
(ASCII), not raw 32-byte digests. All three implementations agree on this.
Changing it is a new scheme version.

## Ordering is part of the root

Leaves are ordered by `(created_at ASC, id ASC)`. `ClaimRecordsForBatch` reads
them in that order when building the root, and `FindRecordsByBatchID` reads
them back in the same order for verification. If you change either sort, every
existing batch fails verification. Both must use the same order, and it must
be stable and total (the `id` tie-breaker makes it total).

`created_at` has millisecond precision in Mongo (BSON date). Two records in
the same millisecond fall back to `id` ordering, which stays deterministic.

## Version selection

`CalculateRecordMerkleRoot` picks the tree from `batchHashVersion(records)`,
which is the **first** record's `hash_version` (`>=2` → v2, else v1). Leaves
and tree always use the same version.

## Invariants

1. v2 node tag `0x01` ≠ leaf tag `0x00`.
2. Odd trailing nodes are promoted, never duplicated.
3. Deterministic: same ordered leaves give the same root (tested). Different
   order or count gives a different root.
4. API, Go SDK and Python SDK produce identical roots for the same input.
5. v1 code is frozen. Never "fix" it, or legacy anchors stop verifying.

## Tests

- `api/internal/models/crypto_v2_test.go`: `TestMerkleV2DomainSeparation`,
  `TestMerkleV2OddCountNotDuplicated`, `TestRecordMerkleRootVersionDispatch`,
  `TestConformanceVectorV2`.
- `api/internal/models/record_test.go`: `TestCalculateRecordMerkleRoot`.
- `api/internal/merkle/batch_processor_test.go`: `TestMerkleTreeDeterminism`,
  `BenchmarkMerkleTree`.
- SDKs: `sdk/go/record_test.go` (`TestMerkleAndLocalVerify`),
  `sdk/python/tests/test_record.py`.

## Known gaps

- The chaincode's `VerifyBatchIntegrity` / `BuildMerkleTree`
  (`logchaincode.go:245-397`) implement **v1 only**. Calling it on a v2 batch
  returns `false` even when the data is intact. The API never calls it
  (verification happens in the API and SDKs), but it is a misleading public
  chaincode function. See `flow-chaincode`.
- There are no Merkle **inclusion proofs** (audit paths). Verifying one record
  today means recomputing the whole batch. Adding proofs would be a new
  feature with its own conformance vectors.

## Changing it safely

Any change to pairing, tags, encoding, or odd-node handling is a new hash
version. Follow the v3 procedure in `flow-hash-scheme`: red test with a pinned
vector, keep v1/v2 intact, mirror it in both SDKs, and run the
`integrity-reviewer` agent.

## Debug recipes

```bash
cd api && go test ./internal/models/ -run 'Merkle|Conformance' -v
# compare API vs SDK root for a real batch
curl -s -XPOST localhost:5001/api/v1/<domain>/records/verify/<batchId> | jq
# then feed the same ordered records to sdk/go VerifyRecordsLocally
```

If the recomputed root differs from the stored `merkle_root` but no content
changed, check (a) the sort order of the read, (b) mixed `hash_version` inside
the batch, and (c) a record missing from the read (a query filter that
excludes soft-deleted records would break verification; the verification
read must include them).

## Related

`flow-hash-scheme` · `flow-batch-anchoring` · `flow-batch-verification` ·
`flow-chaincode` · `flow-sdk-go` · `flow-sdk-python`
