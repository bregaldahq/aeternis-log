---
name: flow-config-lifecycle
description: >-
  Deep reference for AeternisLog runtime configuration and process lifecycle:
  defaults → config.yaml → env overrides → Validate (api/pkg/config), which
  keys are real vs legacy, the exact boot sequence in cmd/api/main.go
  (logger, Mongo, schema assert, WAL recovery, Redis, Fabric, batch processor,
  router, servers) and graceful shutdown order. Use when adding/renaming a
  config key or env var, changing startup/shutdown, or when the API fails to
  boot or ignores a setting.
---

# Flow: configuration and process lifecycle

## Config resolution (`api/pkg/config/config.go`)

1. **Defaults** hardcoded in `LoadConfig` (`:209-299`).
2. **YAML** `config.yaml` from the **current working directory** (`:302`).
   The file is required: a missing file is an error. Unknown keys are
   **silently ignored** (yaml.v3 without `KnownFields`), so a typo never
   errors.
3. **Env overrides** (`overrideFromEnv`, `:336`). Only the variables listed
   there exist. Booleans are `== "true"`, and unparseable numbers or
   durations are ignored silently.
4. **Validate** (`:584`): port ranges, required Mongo/Fabric names,
   transport `gateway`, gateway identity fields when `fabric.sync_enabled`,
   WAL dir, batch size, log level, auth keys/header/tenant shape, rate-limit
   positivity, webhook URL.

`api/.env.example` is **documentation only**. Nothing loads `.env` files, and
the compose files set env inline.

## Key catalog (real, used)

`server.{host, port, debug(→gin release mode when false), read/write/shutdown_timeout, max_body_bytes, cors_allowed_origins}` ·
`mongodb.{url, database, records_collection, pools/timeouts}` ·
`redis.{host, port, password, db, pool/timeouts, cache_enabled}` ·
`fabric.{sync_enabled, transport, channel, chaincode, msp_id, gateway_*, identity_*, invoke/query_timeout, tenant_channels, tenant_identities}` ·
`wal.{enabled, directory}` · `batching.{enabled, auto_batch_size, auto_batch_interval}` ·
`logging.{level, format, output, enable_caller}` · `metrics.{enabled, port, path}` ·
`auth.{enabled, header_name, api_keys, tenants}` · `rate_limit.{enabled, max_requests, window}` ·
`webhook.{enabled, url, secret, timeout, max_retries}`.

## Legacy or dead keys (accepted, no effect)

`mongodb.collection` (still **required** by Validate),
`mongodb.sync_control_collection`, `redis.cache_ttl` (only dead cache
helpers), `fabric.api_url`, `fabric.tls_enabled` (not in the struct),
`batching.batch_executor_workers` (logged only),
`batching.verification_enabled`. `.env.example` also lists ghost vars
(`WAL_BACKEND`, `WAL_CHECK_INTERVAL`, `WAL_STREAM_KEY`,
`WAL_CONSUMER_GROUP`) and **misses two real ones**: `AUTH_TENANTS` and
`MONGO_RECORDS_COLLECTION`. Process-level env outside the config package:
`CONFIG_PATH` (read by `cmd/migrate` only; the API always reads
`./config.yaml`) and `RUN_MIGRATIONS` (`api/entrypoint.sh`). The Helm
configmap renders many WAL keys that do not exist. `fabric.tenant_identities` and the Fabric timeouts have **no env
override**.

## Boot sequence (`api/cmd/api/main.go:61-295`)

1. Banner → bootstrap stderr logger.
2. `LoadConfig("config.yaml")`, **fatal** on error.
3. `logger.Init`.
4. `database.ConnectWithRetry(5)` (about 31 s), **fatal**.
5. `migrations.AssertVersion(TargetVersion)`, **fatal** on mismatch
   (`flow-mongo-migrations`).
6. WAL: open plus `Recover` (errors logged, boot continues), or `NoopWAL`
   (warning).
7. Redis: connect or degrade (never fatal).
8. `fabric.NewFabricClient`, **fatal** on error (for example a missing crypto
   path when `sync_enabled=true`).
9. `BatchProcessor` + optional webhook notifier → `Start` (ticker).
10. Router + middleware (`flow-http-middleware`) + auth (warns when
    disabled) + handlers + routes.
11. `ListenAndServe` on `server.host:port` (goroutine), plus the metrics
    server on `:metrics.port`.
12. Wait for SIGINT or SIGTERM.

## Shutdown order

`srv.Shutdown(ctx)` → `metricsSrv.Shutdown(ctx)` (bounded by
`server.shutdown_timeout`), then the deferred calls in reverse: batch
processor `Stop` (10 s; waits for the ticker goroutine, so an in-flight batch
finishes or times out) → Fabric `Close` → Redis `Close` → WAL `Close` → Mongo
`Close` → log file close. In-flight **webhook goroutines are not awaited**.

## Native-run gotchas (`make dev` + `make run`)

`make run` runs from `api/`, so it reads `api/config.yaml`
(Mongo/Redis `localhost`, fine). But:
- Fabric paths point to `/fabric-crypto/...`, which does not exist natively.
  Either `FABRIC_SYNC_ENABLED=false` or override the endpoint to
  `localhost:7051` plus the three path env vars
  (`hybrid-architecture/fabric-network/crypto-config/...`).
- WAL dir `/var/log/aeternislog-wal` needs write access. Use
  `WAL_DIRECTORY=/tmp/aeternislog-wal`.
- Schema must be migrated first: `cd api && go run ./cmd/migrate`.

## Invariants

- A new config key needs: struct field + yaml tag, default, env override if
  operators need it, `Validate` rule if misconfiguration is dangerous,
  `config_test.go` coverage, `api/config.yaml` + `.env.example` + Helm
  values/configmap + the website page `operations/configuration.md`.
- Security-relevant defaults stay **safe**: auth and rate limiting are
  opt-in today, so production configs must enable them explicitly (the
  ship-check gate checks this).
- Secrets (API keys, webhook secret, identity keys) never go in ConfigMaps
  or committed yaml. Use env from a Secret, or mounted files.

## Tests

`api/pkg/config/config_test.go`: `TestValidateAuth`,
`TestValidateRateLimit`, `TestValidateFabric`, `TestChannelForTenant`,
`TestSplitAndTrim`, `TestParseTenants`.

## Related

`flow-local-stack` · `flow-kubernetes-helm` · `flow-mongo-migrations` ·
`flow-fabric-transport` · `flow-http-middleware` · `flow-wal`
