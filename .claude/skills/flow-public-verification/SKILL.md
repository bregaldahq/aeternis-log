---
name: flow-public-verification
description: >-
  Deep reference for the unauthenticated external-auditor endpoint GET
  /public/anchors/{batchId}[?root=…] (F16): server-side channel resolution,
  the identity it queries under, exactly which fields may be returned, and the
  404/502 mapping. Use when changing PublicHandler, adding any other public or
  unauthenticated route, or when an auditor gets 404 for a batch that verifies
  fine through the authenticated API.
---

# Flow: public anchor verification (`GET /public/anchors/{batchId}`)

An external auditor who holds a batch id, and optionally a root they computed
from data they were given (for example with an SDK), can confirm it against
the ledger **without an API key**. The endpoint shows that a root is anchored.
It must never reveal anything about tenants, records or batch contents.

## Entry points

| What | Where |
|---|---|
| Route (no auth, no domain validation) | `api/cmd/api/main.go:317-318` |
| Handler | `api/internal/handlers/public.go:48` (`GetAnchor`) |
| Seam | `public.go:16` (`batchQuerier` interface, satisfied by `*fabric.FabricClient`) |
| Construction | `main.go:233` (`NewPublicHandler(fabricClient, cfg.Fabric.Channel)`) |

## Step by step

1. `channel = defaultChannel` (`fabric.channel`), resolved **server-side**. A
   `?channel=` parameter is ignored on purpose, so the endpoint cannot be used
   to probe arbitrary channels.
2. `VerifyMerkleBatch(ctx, tenant="", channel, batchID)`. The empty tenant
   means the API's **default gateway identity**.
3. Errors: message contains `does not exist` → `404 not_found`. Anything else
   (Fabric down or disabled) → `502 fabric_error`.
4. The response is an allowlist:
   `{anchored: true, batch_id, merkle_root, anchored_at?}`, plus
   `root_matches: bool` when `?root=` is given.
   `log_ids`, `num_logs` and `tenant` from the chaincode are **dropped**.

## Who can actually be looked up (important)

The chaincode scopes every read by the **caller's** tenant, which it derives
from the signing identity (`flow-chaincode`). This endpoint signs with the
default identity, so it can only see batches anchored under the default
identity's tenant (its `tenant` cert attribute, else its MSP ID) **on the
default channel**. Consequences:

- Tenants anchored under a **per-tenant identity**
  (`fabric.tenant_identities`) or on a **per-tenant channel**
  (`fabric.tenant_channels`) get **404** here, even though their batches are
  anchored. This follows from ledger isolation, but it contradicts the
  "external auditors can verify any batch" framing in the docs.
- A future fix needs a deliberate design, such as a public read-only
  chaincode function keyed by a non-enumerable proof id, or per-tenant public
  routes. Treat it as an isolation-sensitive change.

## Invariants

1. Unauthenticated by design. It must stay **read-only** and return only
   on-chain proof data (root and timestamp).
2. The channel is never taken from the caller.
3. No tenant metadata (`log_ids`, `num_logs`, `tenant`) in the response.
4. The difference between 404 and "not yours" must not leak cross-tenant
   existence. Today both look like `does not exist` from the chaincode.
5. Rate limiting applies (global middleware) when enabled. This is the main
   abuse surface because it has no auth.

## Tests

`api/internal/handlers/public_test.go`:
`TestGetAnchorFoundUsesDefaultChannel`, `TestGetAnchorIgnoresChannelOverride`,
`TestGetAnchorDoesNotLeakMetadata`, `TestGetAnchorRootMismatch`,
`TestGetAnchorNotFound`, `TestGetAnchorFabricError`. They use a fake
`batchQuerier`.

## Changing it safely

- Any new field in the response needs an explicit reason and a
  `DoesNotLeak`-style test asserting what is *absent*.
- Adding any new unauthenticated route: register it outside the auth groups
  deliberately, document it in `CLAUDE.md` invariants, and run the
  `integrity-reviewer` agent.
- Update the website pages `security/threat-model.md` and
  `guides/verify-integrity.md` when behavior changes.

## Debug recipes

```bash
curl -s localhost:5001/public/anchors/<batchId> | jq
curl -s "localhost:5001/public/anchors/<batchId>?root=<hex>" | jq .root_matches
```

A 404 on a batch that verifies through `/api/v1/.../verify` means it was
anchored under a tenant identity or channel the default identity cannot see
(see above).

## Related

`flow-batch-verification` · `flow-chaincode` · `flow-tenancy` ·
`flow-fabric-transport` · `flow-http-middleware`
