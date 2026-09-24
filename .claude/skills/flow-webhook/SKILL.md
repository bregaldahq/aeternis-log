---
name: flow-webhook
description: >-
  Deep reference for the batch.anchored webhook (api/internal/webhook): when
  it fires, the JSON payload, HMAC-SHA256 signing (X-Webhook-Signature),
  async delivery with linear-backoff retries, and its delivery guarantees
  (at-most-once-ish, best effort). Use when changing the event payload,
  signing, retries, adding event types, or when a consumer reports missing or
  unverifiable callbacks.
---

# Flow: `batch.anchored` webhook

## When it fires

`BatchProcessor.notifyAnchored` (`api/internal/merkle/batch_processor.go:75`)
runs after a **successful** anchor:
- `ProcessRecordBatch` after `StoreMerkleBatch` succeeds (`:299`);
- `ReconcileBatches` after a successful re-anchor (`:383`).

It does **not** fire when the reconciler finds the batch "already anchored"
(`flow-batch-reconciliation`), when Fabric is disabled (nothing is anchored),
or for verification results.

Enabled when `webhook.enabled && webhook.url != ""` (`notifier.go:48`). It is
wired at boot (`api/cmd/api/main.go:169-172`), and `Validate` rejects
`enabled` without a `url`.

## Delivery (`api/internal/webhook/notifier.go`)

1. `NotifyBatchAnchored` sets `event="batch.anchored"`, marshals it, and
   starts **`go deliver(...)`**. Batching never waits and never fails because
   of the webhook.
2. `deliver`: up to `1 + max_retries` attempts (default 3 retries), sleeping
   `attempt × 500 ms` between them (linear). Success = 2xx. After the final
   failure it logs a warning `webhook delivery failed` (batch id, attempts).
3. `post`: `POST url`, headers `Content-Type: application/json`,
   `X-Webhook-Event: batch.anchored`, and when `webhook.secret` is set,
   `X-Webhook-Signature: sha256=<hex HMAC-SHA256(secret, raw body)>`. The
   client timeout is `webhook.timeout` (default 5 s).

## Payload

```json
{ "event": "batch.anchored", "tenant": "acme", "domain": "contracts",
  "batch_id": "acme-contracts-1a2b3c4d", "merkle_root": "<hex>",
  "num_records": 42, "tx_id": "<fabric tx>", "anchored_at": "2026-09-23T12:00:00Z" }
```

`anchored_at` is the API's clock when notifying, not the ledger timestamp.

## Consumer verification

Compute `HMAC-SHA256(secret, raw_request_body)` and compare it in constant
time with the hex after `sha256=`. Use the raw bytes, not re-serialized JSON.

## Guarantees and gaps

- **Best effort.** In-flight deliveries are lost on shutdown (goroutines are
  not tracked or drained). There is no persistent outbox and no redelivery
  after the retries are exhausted.
- **No replay protection.** The signature does not cover a timestamp or
  nonce. Consumers should dedupe on `batch_id` (a batch is anchored exactly
  once).
- One global URL and secret for **all tenants**. The payload includes
  `tenant`, so a shared receiver sees every tenant's anchors. Per-tenant
  endpoints are a product gap.
- The README's payload list omits `tenant` and `event`. Keep the website page
  `guides/webhooks.md` aligned with this struct.

## Config

`webhook.{enabled, url, secret, timeout, max_retries}`, env `WEBHOOK_*`. In
Helm, `secret` goes into the app Secret (`WEBHOOK_SECRET`), never the
ConfigMap.

## Tests

`api/internal/webhook/notifier_test.go`: `TestNotifierDelivers` (payload +
signature), `TestNotifierRetries`, `TestNotifierDisabled`.

## Changing it safely

- Payload changes are a **public contract**: additive only. Update
  `README.md`, `website/src/content/docs/guides/webhooks.md` and the test.
- If you add a timestamp to the signature, version the header (for example
  `X-Webhook-Signature-V2`) so existing consumers keep working.
- Never put record payloads into the event (tenant data leaves the
  platform).

## Debug recipes

```bash
# 1) a throwaway receiver that prints headers + body
nc -lk 9999
# 2) point the API at it: set webhook.enabled/url/secret in api/config.yaml
#    (the compose file does not forward WEBHOOK_* env vars), then rebuild:
make api
# 3) force a batch and watch the receiver and the logs
make api-logs | grep 'webhook'
```

A native run (`make dev` + `make run`) honors
`WEBHOOK_ENABLED=true WEBHOOK_URL=http://localhost:9999 WEBHOOK_SECRET=s`
directly.

## Related

`flow-batch-anchoring` · `flow-batch-reconciliation` · `flow-config-lifecycle`
