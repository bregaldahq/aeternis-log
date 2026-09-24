---
name: flow-wal
description: >-
  Deep reference for the AeternisLog write-ahead log (api/internal/wal): the
  append+fsync-before-insert durability contract on the record-create path,
  in-flight counting and truncation, idempotent crash recovery at boot, the
  NoopWAL, and the single-replica/PVC constraints it imposes on deployment.
  Use when touching the WAL, the create path's ordering, boot recovery,
  wal.* config, or when diagnosing lost/duplicated records after a crash.
---

# Flow: write-ahead log

**Contract:** a record acknowledged with `201` survives a process crash. The
handler writes the record to a local append-only file and `fsync`s it
**before** inserting it into MongoDB. If the process dies between the two
steps, the next boot replays the file into Mongo.

## Entry points

| What | Where |
|---|---|
| Package doc (design rationale) | `api/internal/wal/wal.go:1-14` |
| Interface used by handlers | `wal.go:41` (`RecordLog{Append, Confirm, Stats}`) |
| File impl | `wal.go:48` (`FileWAL`), `:59` (`New`), `:101` (`Append`), `:124` (`Confirm`), `:158` (`Recover`) |
| Disabled impl | `wal.go:205` (`NoopWAL`) |
| Boot wiring + recovery | `api/cmd/api/main.go:110-138` |
| Use on create | `api/internal/handlers/records.go:107-122` |
| Config | `wal.enabled` (default true), `wal.directory` (default `/var/log/aeternislog-wal`); env `WAL_ENABLED`, `WAL_DIRECTORY` |

## Mechanics

- File `<dir>/records.wal`, one JSON-encoded `models.Record` per line,
  opened `O_APPEND`.
- **Append**: marshal → write → `fsync` under one mutex. `pending++`,
  `count++`.
- **Confirm**: `pending--`. When `pending == 0`, **truncate** the file. The
  file is emptied only when nothing is in flight, so a concurrent append is
  never lost. Under constant load the file can grow until a quiet moment.
- **New**: counts existing lines and sets `pending = count`, so leftovers from
  a crash block truncation until `Recover` drains them.
- **Recover (boot)**: replay every line in order through the `insert`
  callback. `main.go` treats `ErrDuplicateRecord` as success, which makes the
  replay idempotent (no poison loop). A line that fails to parse (partial
  last write) is skipped. A real insert error returns an error and **leaves
  the file intact** for the next boot (the API still starts and logs the
  error). On success it truncates and sets `pending = 0`.

## Invariants

1. `Append` (with fsync) strictly before `InsertRecord`, and `Confirm` after
   it on **every** outcome.
2. Recovery must stay idempotent: the insert callback maps duplicates to
   success.
3. Truncate only when `pending == 0`.
4. One WAL file per process. The WAL is **not shared** across replicas.

## Deployment constraints

- The Helm chart puts the WAL on a PVC (`persistence.enabled`) and **refuses
  `replicaCount > 1`** unless `allowMultiReplica=true`. A ReadWriteOnce PVC
  cannot be shared, so a record written by one pod is not recoverable by
  another (`deploy/helm/aeternislog/templates/api-deployment.yaml:1-2`).
- docker-compose: named volume `wal-data` (`wal-data-prod` for staging).
- `make clean` deletes the volume, and with it any unrecovered records.

## Known gaps

- `Stats()` (pending entries, total appended) is **not exposed** anywhere
  (not in `/health`, not in metrics).
- A replayed record's `created_at` is JSON-serialized as RFC3339 (seconds)
  through `FlexTime.MarshalJSON`, so sub-second precision is lost on recovery.
  This does not affect the hash (not hashed), but it can reorder the record
  relative to same-second peers in the claim order.
- The Helm configmap renders many WAL keys (`backend`, `stream_key`,
  `consumer_group`, `rotation_enabled`, …) that the Go config **ignores**
  (leftovers of a removed Redis-stream WAL). `values.yaml` mentions
  `wal.backend`, and the Redis compose comment refers to a "redis WAL
  backend". Neither exists.
- If `Recover` fails at boot, the API starts anyway with records still
  pending in the file. They are retried on the next boot only.

## Tests

`api/internal/wal/wal_test.go`: `TestAppendDurableAndRecovers`,
`TestRecoverIsIdempotent`, `TestConfirmTruncatesWhenDrained`,
`TestStatsCountsEntriesNotBytes`, `TestConcurrentAppendsNoLoss`.

## Changing it safely

- Red test first in `wal_test.go`. For ordering changes in the handler, add
  a handler test with a fake `RecordLog` that records call order.
- A new WAL backend must implement `RecordLog`, preserve the replay
  idempotency contract, and get a config key that `Validate` checks.
- Never move the insert before the append "for latency". Measure instead
  (the `HighWriteLatencyP99` alert and SLA exist for this).

## Debug recipes

```bash
docker exec aeternislog-api sh -c 'wc -l /var/log/aeternislog-wal/records.wal; tail -1 /var/log/aeternislog-wal/records.wal'
make api-logs | grep -E 'WAL recovery|failed to write WAL'
```

## Related

`flow-record-ingest` · `flow-config-lifecycle` · `flow-kubernetes-helm` ·
`flow-local-stack`
