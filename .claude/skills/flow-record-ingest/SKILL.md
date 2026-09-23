---
name: flow-record-ingest
description: >-
  Deep reference for the record-create path in AeternisLog — POST
  /api/v1/{domain}/records from request binding through tenant resolution,
  integrity-hash computation, WAL append/fsync, MongoDB insert and the response.
  Use when changing, debugging or reviewing how a record is accepted and
  persisted: new request fields, ID/timestamp defaults, validation, duplicate
  (409) handling, durability, or anything that alters what gets hashed at
  creation time. Not for batching/anchoring (see flow-batch-anchoring) or the
  hash math itself (see flow-hash-scheme).
---

# Flow: record ingest (`POST /api/v1/{domain}/records`)

A client sends `{source, payload, [id], [timestamp], [hash_fields]}`. The API
builds a `models.Record`, computes its v2 integrity hash, makes the write
crash-durable through the WAL, inserts it into MongoDB and returns
`201 {domain, id, hash}`. The record is **not** batched or anchored here — it
sits in the pending pool (no `batch_id`) until the batch processor claims it.

## Entry points

| What | Where |
|---|---|
| Route registration + middleware order | `api/cmd/api/main.go:327-340` (`registerRoutes`) |
| Handler | `api/internal/handlers/records.go:61` (`CreateRecord`) |
| Tenant resolution | `api/internal/handlers/records.go:42` (`tenantFrom`) |
| Request model | `api/internal/models/record.go:132` (`CreateRecordRequest`) |
| Record model | `api/internal/models/record.go:26` (`Record`) |
| Hash | `api/internal/models/record.go:81` (`CalculateHash` → v2) |
| Validation | `api/internal/models/record.go:115` (`Validate`) |
| WAL | `api/internal/wal/wal.go:101` (`Append`), `:124` (`Confirm`) |
| Insert + duplicate mapping | `api/internal/database/collections.go:42` (`InsertRecord`, `ErrDuplicateRecord`) |
| Uniqueness guarantee | `api/internal/migrations/0001_records_indexes.go` (unique `(tenant, domain, id)` index) |

## Step by step

1. **Global middleware** (in order): `gin.Recovery` → `RequestID` →
   `RequestLogger` → `CORS` → `MaxBodyBytes` (default 1 MiB) → `SecurityHeaders`
   → `metrics.Middleware` (if enabled) → `RateLimiter` (if enabled). See
   `flow-http-middleware`.
2. **Group middleware**: `ValidateDomain` (`^[a-z0-9][a-z0-9-]{0,62}$`, else
   `400 invalid_domain`) then `APIKeyAuth` when `auth.enabled` (sets
   `c.Set("tenant", …)`, else `401`).
3. **Bind** `CreateRecordRequest` with `ShouldBindJSON`. `source` and `payload`
   are `binding:"required"`; a body over `MaxBodyBytes` also fails here →
   `400 invalid_request`.
4. **Defaults**: empty `id` → `uuid.New()`; empty `timestamp` →
   `time.Now().UTC().Format(RFC3339)` (second precision). The client-supplied
   `timestamp` is an **opaque string** — it is hashed exactly as sent, never
   parsed or normalized.
5. **Build the record**: `Tenant = tenantFrom(c)` (`"default"` when auth is
   off), `Domain` from the path, `HashVersion = models.CurrentHashVersion` (2),
   `CreatedAt = now` (server time, *not* hashed; drives ordering and cursors).
6. **Hash**: `record.Hash = record.CalculateHash()` — v2 leaf hash over
   `id, timestamp, source, canonical(payload | hash_fields)`. Tenant, domain,
   `created_at` and `hash_fields` themselves are **not** in the pre-image.
7. **Validate** (`domain`, `id`, `source` non-empty, `payload` non-nil) →
   `400 validation_failed`. Note it runs *after* hashing; harmless because a
   failed record is never persisted.
8. **WAL append** (5 s context starts before this): JSON line + `fsync`. On
   error → `500 wal_error` and nothing is inserted. With `wal.enabled=false`
   the handler uses `NoopWAL`.
9. **Insert** into the `records` collection. Then **always** `wal.Confirm()` —
   any outcome (success, duplicate, error) closes the crash window.
10. **Map result**: duplicate key → `409 conflict` (IDs are immutable,
    including soft-deleted ones — they are never resurrected); other error →
    `500 database_error`; success → `201` with `{domain, id, hash}`.

## State after success

```
{tenant, domain, id, timestamp, source, payload, [hash_fields], hash,
 hash_version: 2, created_at: <BSON date>}      # no batch_id / merkle_root yet
```

The record is now visible to `GET` and to `DistinctPendingRecordScopes`, so
the next auto-batch tick (default every 30 s) will claim it.

## Invariants

- The hash is computed **server-side** from the exact fields stored; the
  stored `hash` is informational — verification always recomputes
  (`flow-batch-verification`).
- `hash_version` must be set to `CurrentHashVersion` on every new record, and
  `hash_fields` must be **persisted** alongside it, or later verification
  cannot reproduce the leaf.
- Tenant comes **only** from the auth middleware context — never from the body,
  a header the client controls, or the path.
- WAL `Append` happens **before** the insert; `Confirm` happens after it on
  every path. Reordering either reopens the crash-loss window.
- `(tenant, domain, id)` is unique (migration 0001). The same `id` in another
  tenant or domain is a different record.

## Failure modes

| Symptom | Cause |
|---|---|
| `400 invalid_domain` | Domain has uppercase, `_`, `.`, or > 63 chars |
| `400 invalid_request` | Missing `source`/`payload`, bad JSON, or body > `server.max_body_bytes` |
| `401 unauthorized` | Auth enabled and key missing/unknown |
| `409 conflict` | `(tenant, domain, id)` already exists (even soft-deleted) |
| `500 wal_error` | WAL dir not writable / disk full (`wal.directory`, default `/var/log/aeternislog-wal`) |
| `500 database_error` | Mongo down or timed out (5 s) |
| API refuses to boot | Schema version mismatch — run migrations (`flow-mongo-migrations`) |

## Tests

- Hash properties: `api/internal/models/record_test.go`
  (`TestRecordHashKeyOrderIndependent`, `TestRecordHashChangesWithContent`,
  `TestRecordHashFields`, `TestRecordValidate`).
- Duplicate → `ErrDuplicateRecord`: `api/internal/database/mongodb_test.go`
  (`TestInsertRecordDuplicate`, needs Mongo).
- WAL durability: `api/internal/wal/wal_test.go`.
- **Gap:** there is no handler-level test for `CreateRecord` (status mapping,
  defaults, tenant from context). Add one with `httptest` + a fake
  `RecordLog` when you touch this handler.

## Changing it safely

- **New request field that should be hashed** → it is a hash-scheme change.
  Stop and follow `flow-hash-scheme` (new version, keep old verifiable).
- **New field that should not be hashed**: add it to `Record` with bson/json
  tags and make sure `calculateHashV2` does not read it. Add a test asserting
  the hash is unchanged when only that field changes.
- **Changing defaults** (ID format, timestamp format) changes future hashes
  only. Existing records keep verifying because the stored strings are used.
- Update the Swagger annotations on `CreateRecord`
  (`records.go:49-60`), since they are the API docs source of truth.

## Debug recipes

```bash
# create (auth off)
curl -s -XPOST localhost:5001/api/v1/contracts/records \
  -H 'Content-Type: application/json' \
  -d '{"source":"crm","payload":{"party":"acme","amount":100},"hash_fields":["party","amount"]}'
# read it back (hash, hash_version, no batch_id yet)
curl -s localhost:5001/api/v1/contracts/records/<id>
# WAL state: /health does NOT report it; inspect the file (empty when drained)
docker exec aeternislog-api sh -c 'wc -l /var/log/aeternislog-wal/records.wal'
```

Logs carry `request_id`; grep API logs for `failed to write WAL` or
`failed to insert record`.

## Related

`flow-hash-scheme` · `flow-wal` · `flow-tenancy` · `flow-http-middleware` ·
`flow-batch-anchoring` · `flow-mongo-migrations`
