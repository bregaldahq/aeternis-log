# anchor — Go SDK for Tamper-Evident Data Anchoring

A small, dependency-free Go client for the API. It wraps the HTTP endpoints (with
automatic retries) and — crucially — recomputes record hashes and Merkle roots
**locally and independently of the server**, so integrity can be verified without
trusting the API.

```go
import "github.com/RicardoMBregalda/aeternis-log/sdk/go"
```

## Usage

```go
c := aeternislog.New("http://localhost:5001", aeternislog.WithAPIKey("my-key"))
ctx := context.Background()

// Create a record. The SDK generates the id/timestamp client-side and verifies
// the server returned the same hash it computed locally.
rec, err := c.CreateRecord(ctx, "contracts", "crm",
    map[string]any{"party": "acme", "amount": 100},
    []string{"party", "amount"}, // optional: only these fields feed the hash
)

// Batch the domain's pending records and anchor the Merkle root on-chain.
batch, err := c.BatchRecords(ctx, "contracts")

// Ask the server to verify a batch. AnchorStatus "ANCHORED" means the verdict was
// decided against the ledger; OnChainMerkleRoot is the root read from it.
res, err := c.VerifyBatch(ctx, "contracts", batch.BatchID)

// ...or verify locally, trusting only the on-chain root:
ok := aeternislog.VerifyRecordsLocally(records, res.OnChainMerkleRoot)
```

Records created through the client are hashed under the server's current scheme
(`aeternislog.CurrentHashVersion`, v2), so the trustless hash check compares like
with like.

## Local (trustless) verification

- `(*Record).ComputeHash()` — the v2 scheme: SHA-256 over `0x00` plus each of
  `id, timestamp, source, canonical(payload)` length-prefixed, restricted to
  `HashFields` when set. Canonical JSON has sorted keys, so the hash is independent
  of payload key order. `HashVersion` selects the scheme (absent = legacy v1).
- `MerkleRoot(records)` — Merkle root of the (ordered) records.
- `VerifyRecordsLocally(records, expectedRoot)` — recompute and compare. Returns
  `false` if any record's content no longer hashes to `expectedRoot`.

## Behavior

- Retries transient failures (network errors, HTTP 5xx) up to `WithMaxRetries`
  (default 3) with a linear backoff; 4xx are returned immediately as `*APIError`.
- No external dependencies (standard library only).
