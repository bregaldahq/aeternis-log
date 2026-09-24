---
name: flow-observability
description: >-
  Deep reference for AeternisLog observability: zerolog structured logging
  (config, request-id correlation, global logger), per-request access logs,
  Prometheus metrics on the separate metrics port (HTTP + product counters),
  /health semantics, and the shipped Prometheus alerts and Grafana dashboard
  in deploy/observability. Use when adding logs/metrics, changing /health,
  editing alert rules or dashboards, or investigating an incident.
---

# Flow: logging, metrics, health, alerts

## Logging (`api/internal/logger/logger.go`)

- `logger.Init(cfg)` builds the global zerolog: level
  (`debug|info|warn|error`, validated by config), format `json` (default) or
  `console`, output `stdout|stderr|<file>`, optional caller, a constant field
  `service=aeternislog-api`, RFC3339Nano timestamps. It also sets
  `zlog.Logger`, so packages can use `github.com/rs/zerolog/log` directly
  (the batch processor and Fabric code do).
- Before `Init`, `main.go` uses a stderr bootstrap logger, so configuration
  errors are never silent.
- Handlers: `log := logger.WithRequestID(c.GetString("request_id"))`. Every
  log inside a request should carry `request_id`.
- Access log: `middleware.RequestLogger` writes one event per request,
  leveled by status (`flow-http-middleware`).
- Env: `LOG_LEVEL`, `LOG_FORMAT`.

Useful messages to grep: `record batch created`, `reconcile: …`,
`on-chain anchor lookup failed`, `failed to write WAL`,
`WAL recovery: records replayed`, `webhook delivery failed`,
`database schema verified`, `schema version mismatch`.

Rule: never log secrets, API keys, private keys or record payloads. Log ids,
roots and counts.

## Metrics (`api/internal/metrics/metrics.go`)

Private registry, served on a **separate port**: `metrics.port` (9090) at
`metrics.path` (`/metrics`), started in `api/cmd/api/main.go:257-274`. It is
not exposed on the API port.

| Metric | Labels | Source |
|---|---|---|
| `http_requests_total` | method, route (template), status | middleware |
| `http_request_duration_seconds` | method, route | middleware |
| `batches_anchored_total` | tenant, domain | `RecordAnchoredBatch` on anchor + reconcile |
| `records_anchored_total` | tenant, domain | same |
| `integrity_verifications_total` | domain, result (`VALID`/`CORRUPTED`) | `RecordVerification` on every verify |
| Go runtime + process collectors | — | registry |

Label cardinality: `route` is the Gin template (bounded). `tenant` and
`domain` are unbounded in principle (client-chosen domains), so watch
cardinality in large multi-tenant deployments.

## Health (`api/internal/handlers/health.go:53`)

`GET /health` **always returns 200**, with body
`{status: healthy|degraded, version, build_time, timestamp, services:{mongodb, redis, fabric, batch_processor}}`.
Mongo or Redis unhealthy → `degraded`. Fabric unhealthy does **not** degrade
(it is treated as optional). Kubernetes probes and compose healthchecks hit
`/health`, so they only detect a dead process, not a dead dependency.

## Alerts and dashboard (`deploy/observability/`)

`prometheus-alerts.yml`: `AnchorApiDown`, `IntegrityViolationDetected`
(critical), `AnchoringStalled`, `HighErrorRate`, `HighWriteLatencyP99`
(SLA 500 ms on `POST /api/v1/:domain/records`). `grafana-dashboard.json`
has 7 panels. Validate with `promtool check rules prometheus-alerts.yml`.

## Known gaps (important)

- **`IntegrityViolationDetected` false positives**: `RecordVerification`
  labels any `IsValid=false` as `CORRUPTED`, including **UNANCHORED** batches
  (and verify calls during an outage in UNKNOWN mode with a stale local
  root). The critical page can fire without tampering. Fix: label by
  `resp.Integrity` (add `UNANCHORED`) and alert only on `CORRUPTED` with
  `anchor_status=ANCHORED`.
- `AnchoringStalled` uses `sum(increase(records_anchored_total[15m])) == 0`.
  If the series has never been created (fresh process, nothing anchored yet),
  `sum` returns empty and the alert cannot fire.
- No metrics for WAL depth, pending/failed batch counts, reconciler retries,
  webhook failures or Fabric latency. Those are the signals an operator needs
  most.
- `/stats` exists in code but is not routed.
- `/health` returns 200 even when degraded.

## Tests

`api/internal/metrics/metrics_test.go`, `api/internal/logger/logger_test.go`,
`api/internal/handlers/health_test.go`.

## Changing it safely

- New metric: register it in `init()`, keep labels bounded, add a test that
  scrapes `Handler()` (pattern: `TestHandlerExposesProductCounters`), and
  update the dashboard, alerts and README, plus the website page
  `operations/observability.md`.
- Changing metric names or labels breaks dashboards and alerts. Change all
  three together.

## Debug recipes

```bash
curl -s localhost:9090/metrics | grep -E '^(batches|records)_anchored_total|integrity_verifications_total'
curl -s localhost:5001/health | jq
make api-logs | jq -c 'select(.level=="error")'   # json format
```

## Related

`flow-http-middleware` · `flow-batch-verification` · `flow-kubernetes-helm` ·
`flow-config-lifecycle`
