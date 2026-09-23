---
name: flow-local-stack
description: >-
  Deep reference for running AeternisLog locally on this Windows+WSL machine:
  the root Makefile targets (up/down/api/dev/run/smoke/clean…), the shared
  aeternislog_network, what each compose file starts (API, MongoDB, Redis,
  nginx dashboard, Fabric), ports, volumes, the two Docker daemons, native
  runs, and git/WSL hygiene. Use whenever you need to bring the stack up,
  reset it, run the API natively, reach a container, or when something
  "works in tests but not in the stack".
---

# Flow: local development stack

## Environment facts

- Code lives in WSL: `wsl.exe -d Ubuntu-22.04 -- bash -lc 'cd /root/tcc-log-management && …'`.
- **Two Docker daemons**: native WSL Docker (other projects) and **Docker
  Desktop** (the tcc stacks). An environment restart can drop the tcc
  containers. `make up` restores them, and named volumes survive.
- `docker compose` (v2) is intermittent here. Prefer the `make` targets. The
  Fabric dev network uses `docker-compose` (v1).
- Git: **never `git add -A`** (WSL surfaces exec-bit mode noise). Stage
  explicit paths. WSL git has no credentials, so push with **Windows git**
  over the UNC path `\\wsl.localhost\Ubuntu-22.04\root\tcc-log-management`
  (use `git -c safe.directory=*` if it complains about ownership).

## Make targets (repo root `Makefile`)

| Target | Does |
|---|---|
| `make up` | `blockchain` then `api`: the whole stack |
| `make blockchain` | `network` + `fabric-network/start-network.sh` (`flow-fabric-dev-network`) |
| `make api` | `network` + `cd api && docker compose up -d --build` → API + MongoDB + Redis + dashboard |
| `make dashboard` | nginx dashboard only (:8088) |
| `make dev` | MongoDB + Redis only (for native runs and Mongo/Redis-backed tests) |
| `make run` / `make build` / `make test` / `make vet` | native `go run ./cmd/api` / build / test / vet in `api/` |
| `make smoke` | `api/scripts/e2e-test.sh` against the dev API (no auth, `MONGO=aeternislog-mongodb`) |
| `make status` | containers on `aeternislog_network` |
| `make api-logs` / `dashboard-logs` / `blockchain-logs` | follow logs |
| `make down` | stop everything, **keep volumes** |
| `make clean` | stop and **delete volumes** (Mongo, Redis, **WAL**) + network. Destructive |
| `make clean-blockchain` | wipe and recreate the Fabric dev network (ledger lost) |

`api/Makefile` has more (coverage, lint, swagger, load tests) but is
secondary. Some of its targets (`db-migrate`) are placeholders.

## What runs where

| Container | Port(s) | Notes |
|---|---|---|
| `aeternislog-api` | 5001 (API), 9090 (metrics) | `RUN_MIGRATIONS=true`, config `api/config.yaml` (ro), crypto `/fabric-crypto` (ro), volume `wal-data` |
| `aeternislog-mongodb` | 27017 | volume `mongodb-data`, db `logdb` |
| `aeternislog-redis` | 6379 | AOF `appendfsync always`, volume `redis-data` |
| `aeternislog-dashboard` | 8088 | nginx serving `examples/dashboard` (ro). The browser calls the API directly |
| Fabric dev | 7050, 7051/9051/11051, 5984…, 7054 | see `flow-fabric-dev-network` |
| Staging (optional) | API 5002/9091, Mongo 27018, Redis 6380 | `flow-fabric-staging-network` |

All dev containers share the external network **`aeternislog_network`**
(created by `make network`). Its absence used to make compose create nothing.

URLs: API `http://localhost:5001`, Swagger `/swagger/index.html`, health
`/health`, metrics `http://localhost:9090/metrics`, dashboard
`http://localhost:8088`.

## Dashboard (`examples/dashboard/index.html`)

A single static page (vanilla JS, no build, inline SVG). It uses `/health`,
`/api/v1/{domain}/records…`, `/report`, `/verify`, `/public/anchors`. Settings
(API URL, key, domain) are kept in `localStorage`. CORS must allow its origin
(the default `*` does). Changes to API response shapes can break it. Check it
after changing verify, report or record models.

## Native run checklist

```bash
make dev
cd api && go run ./cmd/migrate                       # schema (API asserts at boot)
FABRIC_SYNC_ENABLED=false WAL_DIRECTORY=/tmp/aeternislog-wal go run ./cmd/api
```

Keep Fabric on by pointing the gateway env vars at `localhost:7051` and
`hybrid-architecture/fabric-network/crypto-config/...`
(`flow-config-lifecycle`).

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| API restarts in a loop, `schema version mismatch` | migration not applied (fresh volume without `RUN_MIGRATIONS`) |
| API exits `failed to create Fabric client` | crypto-config missing (network never generated) → `make blockchain` |
| `/health` fabric unhealthy, batches `failed` | Fabric down or cold chaincode → `make blockchain`, warm script |
| Containers vanished after a reboot | Docker Desktop daemon restarted → `make up` |
| Mongo/Redis tests "pass" instantly | they **skipped** (no Mongo/Redis) → `make dev` first |

## Related

`flow-fabric-dev-network` · `flow-config-lifecycle` · `flow-mongo-migrations` ·
`flow-testing-strategy` · `flow-fabric-staging-network`
