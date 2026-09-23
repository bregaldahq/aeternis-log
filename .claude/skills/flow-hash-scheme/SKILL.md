---
name: flow-hash-scheme
description: >-
  Deep reference for AeternisLog's record integrity hash (the Merkle leaf):
  scheme v1 vs v2, length-prefixing, the 0x00 leaf tag, canonical payload with
  sorted keys, hash_fields, hash_version dispatch, and parity with the Go and
  Python SDKs. Use whenever you touch models.Record hashing, add a field that
  might need hashing, introduce a new hash version, debug a hash mismatch
  between API and SDK, or review anything on the leaf-hash surface. For how
  leaves are combined into a root, see flow-merkle-tree.
---

# Flow: record integrity hash (Merkle leaf)

Every record carries a SHA-256 leaf hash computed from its **content**. The
hash is what makes tampering detectable: verification recomputes it from what
is stored now, folds the leaves into a Merkle root, and compares against the
root anchored on Fabric. The scheme is versioned per record (`hash_version`).

## Entry points

| What | Where |
|---|---|
| Current version constant | `api/internal/models/record.go:21` (`CurrentHashVersion = 2`) |
| Canonical payload | `api/internal/models/record.go:62` (`canonicalPayload`) |
| v2 leaf | `api/internal/models/record.go:96` (`calculateHashV2`) |
| v1 leaf (legacy) | `api/internal/models/record.go:87` (`calculateHashV1`) |
| Version dispatch | `api/internal/models/record.go:107` (`hashForVersion`), `:167` (`batchHashVersion`) |
| Length prefix + tags | `api/internal/models/merkle.go:122-134` (`merkleLeafPrefix=0x00`, `writeLenPrefixed`) |
| Go SDK mirror | `sdk/go/record.go` (`ComputeHash`, `computeHashV2`, `canonicalPayload`, `writeLenPrefixed`) |
| Python SDK mirror | `sdk/python/aeternislog/record.py` |

## The schemes

**v2 (current, every new record):**

```
leaf = hex( SHA-256( 0x00
                     ‖ u64be(len(id))        ‖ id
                     ‖ u64be(len(timestamp)) ‖ timestamp
                     ‖ u64be(len(source))    ‖ source
                     ‖ u64be(len(canon))     ‖ canon ) )
canon = json.Marshal(payload  OR  {k: payload[k] for k in hash_fields if k in payload})
```

- `u64be` = 8-byte big-endian length. Without it, `id="ab", ts="c"` and
  `id="a", ts="bc"` would collide (field-boundary shifting).
- `0x00` domain-separates a leaf from an internal node (`0x01`), so a
  precomputed internal node can never be passed off as a leaf.
- Output is **lowercase hex** (64 chars).

**v1 (legacy, only for verifying old batches):**
`hex(SHA-256(id + timestamp + source + canon))` — plain concatenation, no tag.

`hash_version` absent or `0` means v1. A batch's version is taken from its
**first** record (`batchHashVersion`); a batch never mixes versions because all
records created by one binary share `CurrentHashVersion`.

## Canonical payload — the subtle part

- Go's `encoding/json` sorts map keys **recursively**, so nested objects are
  canonical too. Key order in the request never affects the hash.
- Numbers round-trip through `interface{}` → `float64`. `100` hashes as `100`,
  but a very large integer or `1.0` is re-serialized by Go's float formatting
  (`1.0` → `1`). The SDKs must reproduce **Go's** output, not their language's
  default. The Python SDK's `_encode_float` handles integer-valued floats but
  falls back to `repr()` otherwise. That diverges from Go for small or
  exponent-form values (Go writes `0.00001`, Python writes `1e-05`). Treat
  non-integer floats in hashed payloads as a **known parity gap** and add a
  conformance case before you rely on them.
- Go escapes `<`, `>`, `&`, U+2028 and U+2029 in `json.Marshal` (HTML-safe).
  The Python SDK mirrors this (`_GO_STRING_ESCAPE`). Any new SDK must too.
- `hash_fields` keys missing from the payload are **silently skipped**. An
  empty sub-map hashes as `{}`.
- `json.Marshal` failure returns `""` (the hash still computes over an empty
  canon). Payloads come from JSON decoding, so this path is practically
  unreachable, but do not rely on it for new types.

## What is NOT in the pre-image (by design)

`tenant`, `domain`, `hash_fields` (the list itself), `hash`, `hash_version`,
`created_at`, `batch_id`, `merkle_root`, `anchor_status`, `tx_id`,
`batched_at`, `deleted_at`.

Consequences:
- **Soft-delete never changes a leaf** (`deleted_at` is excluded) — anchored
  proofs survive deletion.
- Tenant/domain binding comes from the batch scope and the chaincode's
  tenant-scoped key, not the leaf.
- Changing `hash_fields` after creation would change the canon and break
  verification. It is set once at create time and never updated.

## Invariants

1. New records are written with `CurrentHashVersion`; old records are
   **never** rehashed or migrated to a new version.
2. Verification recomputes with the **recorded** version (`hashForVersion`).
3. Every hashed field is length-prefixed and in a fixed order.
4. The leaf tag (`0x00`) differs from the node tag (`0x01`).
5. The API and **both** SDKs produce byte-identical hashes for the same input
   (conformance vector).

## Tests

- `api/internal/models/crypto_v2_test.go`: `TestLeafHashV2NoBoundaryCollision`,
  `TestRecordMerkleRootVersionDispatch`, `TestConformanceVectorV2`
  (shared vector).
- `api/internal/models/record_test.go`: key-order independence, content
  sensitivity, `hash_fields`.
- `sdk/go/conformance_test.go` (`TestConformanceV2`) and
  `sdk/go/record_test.go`: same vector on the SDK side.
- `sdk/python/tests/test_record.py`: Python parity.

## Changing it safely (a new scheme = v3)

1. **Red first:** add a failing test in `crypto_v2_test.go` (or a new
   `crypto_v3_test.go`) that pins the new vector, plus a test proving a
   v2-anchored batch still verifies after the change.
2. Add `calculateHashV3`, extend `hashForVersion` (`v >= 3`) and
   `CalculateRecordMerkleRoot` if the tree changes too. Bump
   `CurrentHashVersion` **last**.
3. Mirror it in `sdk/go/record.go` and `sdk/python/aeternislog/record.py`,
   updating their conformance tests to the same vector.
4. Never alter `calculateHashV1`/`V2` — existing anchors depend on them
   byte-for-byte.
5. Update `README.md`, `CLAUDE.md` (invariants), and the website pages
   `security/cryptography.md` and `sdks/verification.md`.
6. Run the `integrity-reviewer` agent on the diff.

Adding a *non-hashed* field needs none of this, just a test proving the hash
is unchanged when that field changes.

## Debug recipes

Hash mismatch between SDK and API: dump both canon strings. The usual culprits
are float formatting, HTML escaping, or a missing `hash_fields`.

```bash
cd api && go test ./internal/models/ -run 'Conformance|LeafHash' -v
cd sdk/go && go test -run Conformance -v
cd sdk/python && python -m pytest tests/test_record.py -q
```

## Related

`flow-merkle-tree` · `flow-batch-verification` · `flow-record-ingest` ·
`flow-sdk-go` · `flow-sdk-python`
