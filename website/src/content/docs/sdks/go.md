---
title: Go SDK
description: A dependency-free Go client that wraps the API and recomputes hashes and Merkle roots locally for trustless verification.
sidebar:
  order: 1
---

A small, dependency-free Go client. It wraps the HTTP endpoints (with automatic
retries) and — crucially — recomputes record hashes and Merkle roots **locally and
independently of the server**, so integrity can be verified without trusting the
API.

```go
import "github.com/RicardoMBregalda/aeternis-log/sdk/go"
```

## Usage

```go
c := aeternislog.New("http://localhost:5001", aeternislog.WithAPIKey("my-key"))
ctx := context.Background()

// Create a record. The SDK generates the id/timestamp client-side and checks the
// server returned the same hash it computed locally.
rec, err := c.CreateRecord(ctx, "contracts", "crm",
    map[string]any{"party": "acme", "amount": 100},
    []string{"party", "amount"}, // optional: only these fields feed the hash
)

// Batch the domain's pending records and anchor the Merkle root on-chain.
batch, err := c.BatchRecords(ctx, "contracts")

// Ask the server to verify a batch. AnchorStatus "ANCHORED" means the verdict was
// decided against the ledger; OnChainMerkleRoot is the root read from it…
res, err := c.VerifyBatch(ctx, "contracts", batch.BatchID)

// …or verify locally, trusting only the on-chain root:
ok := aeternislog.VerifyRecordsLocally(records, res.OnChainMerkleRoot)
```

Records created through the client are hashed under the server's current scheme
(`aeternislog.CurrentHashVersion`, v2), so the trustless hash check compares like
with like.

## Trustless verification

- `(*Record).ComputeHash()` — the v2 scheme: SHA-256 over `0x00` plus each of
  `id, timestamp, source, canonical(payload)` length-prefixed, restricted to
  `HashFields` when set. Canonical JSON has sorted keys, so the hash is
  independent of payload key order.
- `MerkleRoot(records)` — the Merkle root of the ordered records.
- `VerifyRecordsLocally(records, expectedRoot)` — recompute and compare; returns
  `false` if any record no longer hashes into `expectedRoot`.

## Behavior

- Retries transient failures (network errors, HTTP 5xx) up to `WithMaxRetries`
  (default 3) with linear backoff; 4xx are returned immediately as `*APIError`.
- No external dependencies — standard library only.

See [local verification](/aeternis-log/sdks/verification/) for the shared scheme
and cross-language parity.
