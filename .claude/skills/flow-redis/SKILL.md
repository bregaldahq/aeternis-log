---
name: flow-redis
description: >-
  Deep reference for Redis in AeternisLog (api/internal/cache): what it is
  actually used for today (the shared rate-limit store and health/stats),
  graceful degradation when it is absent, the Lua fixed-window counter, and
  the unused legacy cache helpers. Use when touching redis.* config, the rate
  limiter backend, adding caching, or when Redis shows unhealthy.
---

# Flow: Redis

## What Redis does today

| Use | Where | Notes |
|---|---|---|
| Shared **rate-limit** counter | `api/internal/cache/ratelimit.go` (`RedisRateStore`), wired in `api/cmd/api/main.go:200-215` | Chosen automatically when `rate_limit.enabled` **and** Redis is connected, else the in-memory per-instance store |
| Health / stats | `api/internal/handlers/health.go:72-84` | `/health` → `services.redis` (`healthy`/`disabled`/`unhealthy: …`). An unhealthy Redis marks overall `degraded` |

That is all. **No request path reads or writes cached data.** The helpers in
`api/internal/cache/redis.go` (`Get/Set/GetJSON/SetJSON/DeletePattern`,
`BuildLogListKey`, `BuildLogKey`, `BuildBatchKey`, `InvalidateLogCache`, …)
are leftovers from the removed `logs` API. Nothing calls them outside tests.

## Connection and degradation

`NewRedisClient` (`redis.go:22`):
- `redis.cache_enabled=false` → disabled client, no connection attempt.
  (The flag really means "Redis enabled".)
- ping fails (5 s) → **warn and continue disabled** (never fatal).
- ok → `Enabled=true`, closed on shutdown.

Consequence: with Redis down at boot, the rate limiter silently falls back to
in-memory. Each replica then enforces its own limit (effective limit =
N × max). Redis failing **after** boot makes `Incr` error, and the limiter
**fails open**.

## Rate-limit algorithm

A Lua script runs atomically: `INCR key`, and on the first hit
`PEXPIRE key window`. It returns the count, and the middleware blocks when
`count > max`. Key: `ratelimit:<ClientIP>`. The TTL on first hit guarantees
eviction (no INCR-then-crash key that never expires).

## Config

`redis.{host, port, password, db, pool_size, min_idle_conns, max_retries,
dial/read/write_timeout, cache_ttl, cache_enabled}`. Env: `REDIS_HOST`,
`REDIS_PORT`, `REDIS_PASSWORD`, `REDIS_DB`, `REDIS_CACHE_ENABLED`,
`REDIS_CACHE_TTL`. The compose Redis runs with `--appendonly yes
--appendfsync always`. The comment there about a "redis WAL backend" is
stale.

## Known gaps

- Dead cache code, and `cache_ttl`, are unused. Either delete them or give
  them a real consumer (for example verify results), with tests.
- Redis-backed limiting is still **per IP**, not per tenant or key.
- Helm `values.yaml` says rate limiting is "in-memory, per pod", but the
  chart deploys Redis, so it is actually shared.

## Tests

`api/internal/cache/redis_test.go` (needs Redis on localhost:6379, **skips**
otherwise), `api/internal/middleware/rate_test.go` (in-memory store).

## Changing it safely

Introducing real caching into the integrity path is dangerous: a cached
verify or anchor result must never replace a ledger read. Cache only derived,
non-authoritative data, key it by tenant, and invalidate on anchor.

## Debug recipes

```bash
curl -s localhost:5001/health | jq .services.redis
docker exec aeternislog-redis redis-cli --scan --pattern 'ratelimit:*' | head
docker exec aeternislog-redis redis-cli ttl 'ratelimit:<ip>'
```

## Related

`flow-http-middleware` · `flow-observability` · `flow-config-lifecycle`
