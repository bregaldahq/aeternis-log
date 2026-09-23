---
name: flow-mongo-migrations
description: >-
  Deep reference for AeternisLog's MongoDB layer and schema lifecycle: the
  records collection and its document shape, connection/retry/pooling, the
  versioned migration registry (api/internal/migrations), the cmd/migrate
  binary, RUN_MIGRATIONS in the entrypoint, the Helm pre-upgrade Job, and the
  boot-time AssertVersion that refuses to start on a mismatch (F18). Use when
  adding an index/collection/field, when the API won't boot with "schema
  version mismatch", or when changing database access code.
---

# Flow: MongoDB and schema migrations

## Data model

One collection holds everything: `records` (`mongodb.records_collection`) in
database `logdb` (`mongodb.database`). Document = `models.Record`
(`api/internal/models/record.go:26`). Fields by lifecycle:

| Set at | Fields |
|---|---|
| create | `tenant, domain, id, timestamp, source, payload, hash_fields?, hash, hash_version, created_at (BSON date)` |
| claim | `batch_id, anchor_status=pending, batched_at (RFC3339 **string**)` |
| anchor | `merkle_root, tx_id, anchor_status=anchored` (or `failed`) |
| delete | `deleted_at (BSON date via $currentDate)` |

`schema_migrations` holds one document per applied version
`{version (unique), name, applied_at}`.

Config keys `mongodb.collection` (`logs`) and `sync_control_collection` are
**legacy and unused** by the code, but `Validate` still requires
`collection`.

## Access layer

- `api/internal/database/mongodb.go`: `NewMongoClient` (pool min/max, idle,
  server-selection, socket and connect timeouts, primary ping),
  `ConnectWithRetry(cfg, 5)` (backoff 1, 2, 4, 8, 16 s, about 31 s before a
  fatal error).
- `api/internal/database/collections.go`: every query the product runs (see
  `flow-batch-anchoring`, `flow-record-read-delete`). Duplicate key →
  `ErrDuplicateRecord`.
- `FlexTime` (`api/internal/models/flextime.go`) decodes BSON dates and
  several string formats and writes BSON dates. That is why `batched_at` (a
  string) and `created_at` (a date) both decode.

## Migration lifecycle (F18)

```
registry (migrations.go) ──► Runner.Apply (cmd/migrate)  ──► schema_migrations
                                   ▲                              │
            RUN_MIGRATIONS=true (entrypoint.sh, compose dev)      │
            Helm Job pre-install/pre-upgrade (weight -5)          ▼
                                             API boot: AssertVersion(TargetVersion) — read-only, fatal on mismatch
```

- Registry: `api/internal/migrations/migrations.go:31`, append-only. Never
  edit, reorder or remove a released migration.
- `0001_records_indexes.go`: unique `(tenant, domain, id)`, listing index
  `(tenant, domain, created_at↓, id↓)`, batch index `(tenant, domain, batch_id)`.
- Runner (`runner.go:70`): unique index on `schema_migrations.version` first,
  then apply every `Version > current` in order, recording each one. A
  concurrent runner hits a duplicate key and aborts loudly.
- `AssertVersion` (`runner.go:113`): the database must be **exactly** at
  target. Behind **or ahead** means fatal, with an actionable message.
- `cmd/migrate/main.go`: reads `CONFIG_PATH` (default `config.yaml`) plus env,
  120 s timeout, logs from/to/target/applied.

## How each environment migrates

| Env | Mechanism |
|---|---|
| dev compose (`api/docker-compose.yml`) | `RUN_MIGRATIONS=true` → `entrypoint.sh` runs `/app/aeternislog-migrate` first |
| native `make run` | **nothing**. Run `cd api && go run ./cmd/migrate` once against the `make dev` Mongo, or boot fails |
| staging (`api/docker-compose.prod.yml`) | **not set**, so run `docker exec aeternislog-api-prod /app/aeternislog-migrate` once on a fresh DB |
| Helm | `templates/migrate-job.yaml` pre-install/pre-upgrade hook |

## Adding a migration (TDD)

1. Red: add a test in `runner_test.go` asserting the new index or shape
   (pattern: `TestMigration0001CreatesUniqueRecordsIndex`).
2. `000N_<name>.go` with `Version: N` and an **idempotent** `Up`. Append it
   to `registry`.
3. `TestSchemaVersionIsUnique` guards against duplicate versions.
4. Update `docs/runbook-migrations-and-upgrades.md` for anything
   destructive (two-release add/backfill → remove).
5. Never create indexes or mutate schema at API boot or in `NewMongoClient`.

## Known gaps / risks

- **Helm first install**: the migrate Job is a `pre-install` hook, so it runs
  **before** the chart's demo MongoDB exists. With `mongodb.enabled=true` it
  retries for about 31 s × `backoffLimit`, and fresh installs may fail. Verify
  on a clean cluster, and consider `post-install` for the first install or an
  init container.
- Record-level data migrations (backfills) have no framework yet. `Up` gets
  the raw `*mongo.Database`, so write them carefully, batched and resumable.
- Mongo-dependent tests **skip** when Mongo is absent. `make dev` first.

## Tests

`api/internal/migrations/runner_test.go` (`TestApplyThenAssert`,
`TestApplyIsIdempotent`, `TestMigration0001CreatesUniqueRecordsIndex`,
`TestSchemaVersionIsUnique`), `api/internal/database/mongodb_test.go`.

## Debug recipes

```bash
docker exec aeternislog-mongodb mongosh logdb --quiet --eval 'db.schema_migrations.find().toArray()'
docker exec aeternislog-mongodb mongosh logdb --quiet --eval 'db.records.getIndexes()'
docker exec aeternislog-api /app/aeternislog-migrate      # idempotent
make api-logs | grep -E 'schema (version|check)'
```

## Related

`flow-record-ingest` · `flow-record-read-delete` · `flow-batch-anchoring` ·
`flow-kubernetes-helm` · `flow-config-lifecycle`
