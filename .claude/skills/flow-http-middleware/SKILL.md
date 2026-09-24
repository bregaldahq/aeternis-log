---
name: flow-http-middleware
description: >-
  Deep reference for the Gin request pipeline in AeternisLog: global
  middleware order (recovery, request id, structured request logging, CORS
  allowlist, body cap, security headers, Prometheus, rate limiting with
  memory/Redis store) and route-group middleware (domain validation, API-key
  auth), plus which routes are public. Use when adding/reordering middleware,
  adding a route or route group, changing CORS/rate-limit/auth behavior, or
  debugging 400/401/413/429 responses.
---

# Flow: HTTP request pipeline

## Order (as registered in `api/cmd/api/main.go:190-236, 299-354`)

```
gin.Recovery                     panic → 500
RequestID                        X-Request-ID in/out; c.Set("request_id")
RequestLogger                    one zerolog event per request (after c.Next)
CORS(server.cors_allowed_origins)
MaxBodyBytes(server.max_body_bytes)      default 1 MiB
SecurityHeaders
metrics.Middleware               only if metrics.enabled
RateLimiter(store, max, window)  only if rate_limit.enabled
── route groups ────────────────────────────────────────────────
/, /health, /public/anchors/:batchId, /api/v1/ping, /swagger/*   → no auth
/api/v1/:domain/records[...]     → ValidateDomain → APIKeyAuth (if auth.enabled)
/api/v1/:domain/report[.pdf]     → ValidateDomain → APIKeyAuth (if auth.enabled)
```

Rate limiting is global, so it also protects the unauthenticated routes. It
runs **before** auth, so unauthenticated floods are throttled too.

## Components

| Middleware | Where | Behavior |
|---|---|---|
| `RequestID` | `api/internal/middleware/middleware.go:99` | Reuses the incoming `X-Request-ID`, else a timestamp id (`20060102150405.000000`), which is **not unique** under concurrency. Echoed in the response header |
| `RequestLogger` | `:309` | `info` <400, `warn` 4xx, `error` 5xx. Fields: method, path+query, status, latency, client_ip, bytes, request_id |
| `CORS` | `:29` | `*` → ACAO `*` **without** credentials. Explicit origin match → reflect origin + `Allow-Credentials: true` + `Vary: Origin`. Unknown origin → no ACAO. `OPTIONS` → `204` |
| `MaxBodyBytes` | `:67` | `http.MaxBytesReader`. An oversize body fails in `ShouldBindJSON` → handler returns `400 invalid_request` (not 413). `0` disables |
| `SecurityHeaders` | `:277` | nosniff, `X-Frame-Options: DENY`, legacy XSS header, HSTS 1y |
| `metrics.Middleware` | `api/internal/metrics/metrics.go:82` | Labels by **route template** (`c.FullPath()`) for bounded cardinality; `unmatched` otherwise |
| `RateLimiter` | `middleware.go:123` | Fixed window per `ratelimit:<ClientIP>`. `>max` → `429 rate_limit_exceeded`. **Fails open** on store error |
| memory store | `middleware.go:143` | Per instance; sweeps expired keys on every call (O(n) under lock) |
| Redis store | `api/internal/cache/ratelimit.go` | Lua `INCR` + `PEXPIRE` on first hit (atomic TTL), shared across replicas. Chosen automatically when Redis is enabled |
| `ValidateDomain` | `middleware.go:83` | `^[a-z0-9][a-z0-9-]{0,62}$` else `400 invalid_domain` |
| `APIKeyAuth` | `middleware.go:187` | See `flow-tenancy`. `401 unauthorized` |

Defined but **not wired**: `Timeout` (`:239`, which runs `c.Next()` in a
goroutine and is unsafe with Gin) and `ValidateContentType` (`:288`). Do not
wire `Timeout` as is. Handlers use per-request `context.WithTimeout` instead
(5 s CRUD, 10 s verify/public, 40 s forced batch).

## Invariants

- `RequestID` before `RequestLogger` (the logger reads `request_id`).
- `ValidateDomain` before any handler that puts `:domain` into Mongo filters
  or batch ids.
- `APIKeyAuth` on every tenant-data route. Public routes are explicit
  and few: `/`, `/health`, `/public/anchors/:batchId`, `/api/v1/ping`,
  `/swagger/*`.
- Never emit `ACAO: *` together with `Allow-Credentials: true`.
- `ClientIP()` depends on Gin's trusted-proxy settings. Behind a load
  balancer, configure trusted proxies, or every client shares the LB's IP
  (one bucket for everyone) or can spoof `X-Forwarded-For`.

## Known gaps

- `GET /stats` has a handler and Swagger annotation but **no route**.
- `RequestID` is not collision-free. Prefer a UUID if request ids become
  correlation keys across services.
- No trusted-proxy configuration exists (`router.SetTrustedProxies` is never
  called), so Gin trusts all proxies by default.
- Swagger declares the security scheme as header `Authorization`, while the
  default auth header is `X-API-Key`. Both work, but the Swagger "Authorize"
  box expects the Bearer form.

## Tests

`api/internal/middleware/`: `middleware_test.go` (auth),
`hardening_test.go` (`TestCORS`, `TestValidateDomain`, `TestMaxBodyBytes`,
`TestMatchAPIKeyHashed`), `rate_test.go` (window, reset, idle eviction).
`api/internal/metrics/metrics_test.go` (`TestMiddlewareRecordsRequests`).
Redis store: `api/internal/cache/redis_test.go` (needs Redis).

## Changing it safely

- New route: decide public vs tenant-scoped **explicitly**. Tenant routes go
  under a group with `ValidateDomain` + `authMW`. Add Swagger annotations.
- New middleware: add a focused `httptest` test, and think about its position
  relative to auth and rate limiting.
- Behavior change on the security surface (CORS, auth, limits): update the
  website pages `security/hardening.md` and `operations/hardening-checklist.md`.

## Debug recipes

```bash
curl -si localhost:5001/health | grep -i -E 'x-request-id|strict-transport'
curl -si -H 'Origin: https://evil.example' localhost:5001/health | grep -i access-control
curl -s -o /dev/null -w '%{http_code}\n' localhost:5001/api/v1/BAD_DOMAIN/records   # 400
for i in $(seq 1 120); do curl -s -o /dev/null -w '%{http_code} ' localhost:5001/health; done  # 429s when enabled
```

## Related

`flow-tenancy` · `flow-observability` · `flow-redis` · `flow-config-lifecycle`
