---
name: flow-record-read-delete
description: >-
  Deep reference for the record read and soft-delete paths: GET
  /api/v1/{domain}/records (filters, offset vs keyset cursor pagination, total
  count), GET /records/{id}, and DELETE /records/{id} (soft-delete via
  deleted_at, idempotent, never breaks an anchored proof). Use when changing
  listing/pagination/filtering, cursor encoding, soft-delete semantics, or the
  indexes that back these queries.
---

# Flow: reading and soft-deleting records

## Entry points

| What | Where |
|---|---|
| List | `api/internal/handlers/records.go:165` (`ListRecords`) |
| Get | `api/internal/handlers/records.go:252` (`GetRecord`) |
| Delete | `api/internal/handlers/records.go:282` (`DeleteRecord`) |
| Cursor codec | `api/internal/handlers/cursor.go` (`encodeCursor`, `decodeCursor`, `cloneFilter`) |
| Queries | `api/internal/database/collections.go:53` (`FindRecords`), `:68` (`FindRecordByID`), `:83` (`CountRecords`), `:94` (`SoftDeleteRecord`) |
| Backing index | `api/internal/migrations/0001_records_indexes.go` (`tenant, domain, created_at ↓, id ↓`) |

## List — `GET /api/v1/{domain}/records`

Query: `source`, `limit` (default 50; ≤0, >1000 or non-numeric → 50),
`offset` (default 0), `cursor`.

1. `baseFilter = {tenant, domain, deleted_at: {$exists: false}}`, plus
   `source` if given. Soft-deleted records are hidden from listings.
2. **Cursor mode** (`cursor` set, overrides `offset`): decode base64url JSON
   `{t: created_at_ms, id}` (bad → `400 invalid_cursor`). Add keyset predicate
   `created_at < t OR (created_at == t AND id < id)` on a **clone** of the
   filter.
3. Sort `created_at ↓, id ↓`, `limit`. `skip(offset)` only in offset mode.
4. `total = CountDocuments(baseFilter)`, without the cursor predicate. It is
   the total of the whole filtered set. On a count error it falls back to the
   page length.
5. `next_cursor` is set **only when the page is full** (`len == limit`), from
   the last record's `(created_at, id)`. The last page may still return a
   cursor that yields an empty page.

The response is `ListRecordsResponse{records, total, limit, offset, next_cursor}`.
Records serialize the full stored document, including `hash`,
`hash_version`, `batch_id`, `merkle_root`, `anchor_status` and `tx_id`.
SDKs use this for local verification.

**Precision detail:** cursors carry milliseconds because Mongo stores
`created_at` as a BSON date (ms). A record created with sub-ms precision in Go
is truncated on write, so keyset boundaries stay consistent.

## Get — `GET /api/v1/{domain}/records/{id}`

`FindRecordByID(tenant, domain, id)`. Not found **or soft-deleted** →
`404 not_found`. Another tenant's id is indistinguishable from a missing one.

## Delete — `DELETE /api/v1/{domain}/records/{id}`

1. Look up by `(tenant, domain, id)`. Missing → `404`.
2. If not already deleted: `UpdateOne` with filter `deleted_at` absent and
   `$currentDate: {deleted_at: true}`. The document, hash, batch membership
   and anchor are untouched.
3. Always `200 {soft_deleted: true}`. Deleting twice is idempotent (the
   second call is a no-op, and a concurrent-delete `ErrNoDocuments` is
   swallowed).

Effects on the integrity pipeline:
- **Not yet batched** → never claimed (the claim filter excludes
  `deleted_at`). The record is simply never anchored.
- **Already batched** → stays a leaf. Verification reads it (no `deleted_at`
  filter) and `deleted_at` is not in the hash, so the proof stays valid.
- The id stays reserved. Re-creating it returns `409` (unique index), because
  audit-trail ids are never resurrected.

## Invariants

- Every query filters by `tenant` **and** `domain`.
- Listings and get hide soft-deleted records. **Verification and batch reads
  must not.**
- `deleted_at` never feeds the hash.
- No hard delete exists in the API. Adding one would break anchored proofs
  and needs a product decision.

## Tests

- `api/internal/handlers/cursor_test.go`: `TestCursorRoundTrip`,
  `TestDecodeCursorInvalid`.
- `api/internal/database/mongodb_test.go`: `TestRecordsCRUD` (needs Mongo).
- e2e (`make smoke`): duplicate id → 409, re-create soft-deleted id → 409.
- **Gaps:** there is no unit test for the keyset predicate across equal
  `created_at` values, and no test proving that deleting a batched record
  keeps `verify` VALID. Add both when you touch this area. The second is an
  integrity guarantee.

## Changing it safely

- New filters: add them to `baseFilter`, keep the tenant and domain keys, and
  consider a new index through a migration (`flow-mongo-migrations`). Never
  create indexes at boot.
- Changing sort or cursor fields breaks outstanding cursors. Version the
  cursor payload if needed.
- Update Swagger annotations and `website/src/content/docs/api/endpoints.md`.

## Debug recipes

```bash
curl -s "localhost:5001/api/v1/contracts/records?limit=2" | jq '.next_cursor,.total'
curl -s "localhost:5001/api/v1/contracts/records?limit=2&cursor=<c>" | jq '.records[].id'
curl -s -XDELETE localhost:5001/api/v1/contracts/records/<id> | jq
```

## Related

`flow-record-ingest` · `flow-batch-verification` · `flow-tenancy` ·
`flow-mongo-migrations`
