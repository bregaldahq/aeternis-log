---
name: flow-sdk-go
description: >-
  Deep reference for the Go SDK (sdk/go, package aeternislog, stdlib only,
  Go 1.21): client options, request/retry semantics, the trustless
  create-record hash check, batch and verify calls (409 as a result, not an
  error), and local verification (ComputeHash, MerkleRoot,
  VerifyRecordsLocally) that must stay byte-identical with the server. Use
  when changing the SDK, its public API, or anything in the server's hash /
  Merkle / response shapes that the SDK mirrors.
---

# Flow: Go SDK (`sdk/go`)

Module `github.com/RicardoMBregalda/aeternis-log/sdk/go`, package
`aeternislog`, **standard library only**. Two halves:
1. **HTTP client** (`client.go`), a thin typed wrapper with retries.
2. **Local verifier** (`record.go`), which recomputes leaves and roots
   independently, so integrity can be checked without trusting the API.

## Public surface

| API | Behavior |
|---|---|
| `New(baseURL, ...Option)` | 10 s HTTP timeout, 3 retries. Options: `WithAPIKey` (sent as `X-API-Key`), `WithHTTPClient`, `WithMaxRetries` |
| `CreateRecord(ctx, domain, source, payload, hashFields)` | Generates the **id** (128-bit hex) and **timestamp** (RFC3339 UTC) client-side, sets `HashVersion = CurrentHashVersion`, computes the hash locally, POSTs, then **fails if the server hash ≠ local hash** |
| `CurrentHashVersion` (`record.go`) | The server's current scheme (2). Must track `models.CurrentHashVersion` in the API |
| `GetRecord(ctx, domain, id)` | → `*Record` (includes `hash_version`, `batch_id`, `merkle_root`) |
| `BatchRecords(ctx, domain)` | POST `/records/batch` with `{}` (server default size 100) → `BatchResult` |
| `VerifyBatch(ctx, domain, batchID)` | 200 → result. **409 is decoded into a result** (`IsValid=false`), not returned as an error. `VerifyResult` carries `OnChainMerkleRoot` (the root to trust for local verification), `AnchorStatus` (`ANCHORED` / `UNANCHORED` / `UNKNOWN`), `NumRecords`, `Message`, and the database/recomputed roots |
| `Record.ComputeHash()` / `MerkleRoot([]*Record)` / `VerifyRecordsLocally(recs, root)` | Local mirror of `models` (v1 + v2 dispatch by `HashVersion`) |
| `APIError{StatusCode, Body}` | Non-retried 4xx, or the last 5xx |

Retry policy (`do`): network errors and 5xx are retried up to `maxRetries`
with **linear** backoff `attempt × 200 ms` (context-aware). 4xx is returned
immediately. `CreateRecord` retries are **safe** because the id is fixed
client-side: a retried create either succeeds or gets `409` (already there).

## Known gaps

- A retried `CreateRecord` that hits `409` (the first attempt landed but its
  response was lost) is returned as an error. Callers cannot tell "already
  created with my id" from a real conflict. Consider treating 409 plus a
  GET-and-compare as success.
- `CurrentHashVersion` is a copy of the server constant. When the server
  moves to v3, bump it here in the same PR (the conformance vector test
  catches drift only for the pinned version).

Test fakes must behave like the real server: `TestCreateRecord`'s fake hashes
under v2. A fake that uses a different scheme than the server is what hid the
v1/v2 bug fixed in `fix(sdk)`.

## Tests

```bash
cd sdk/go && go test ./... -v                          # unit + conformance
cd sdk/go && go test -tags integration -run TestSDKLive -v   # needs `make up`; AETERNISLOG_BASE_URL / AETERNISLOG_API_KEY
```

`conformance_test.go` (`TestConformanceV2`) pins the **shared v2 golden
vector** (leaf and root for `rec-1..3`) that the server
(`api/internal/models/crypto_v2_test.go`) and the Python SDK
(`tests/test_record.py`) also assert. That vector is the cross-implementation
contract.

## Invariants

- Stdlib only (no third-party deps). Keep it that way.
- Local hashing is byte-identical to the server for every supported version.
  Any server hash change lands in the SDK and the conformance vectors in the
  same PR.
- Never trust server-provided hashes or roots in the local-verify path.

## Changing it safely

- Public API changes are semver-relevant (the module is consumed by
  customers). Keep them additive, and update `sdk/go/README.md` and the
  website page `sdks/go.md`.
- Mirror server response-model changes (`VerifyBatchResponse`,
  `RecordBatchResult`) here.

## Related

`flow-hash-scheme` · `flow-merkle-tree` · `flow-sdk-python` ·
`flow-batch-verification` · `flow-testing-strategy`
