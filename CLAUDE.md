# AeternisLog — Engineering Guide

AeternisLog gives **any data cryptographic proof of integrity**. Records land in
MongoDB (fast, queryable), are folded into a **Merkle tree** per batch, and each
batch's root is anchored **write-once on Hyperledger Fabric**. Tampering is
mathematically detectable: recompute the root from MongoDB, compare it to the
chain. This is a **commercial product heading to market** — treat correctness,
the integrity guarantees, and code quality as load-bearing, not optional.

---

## Golden rules (read first)

1. **TDD, always.** Write the failing test first (red), then the implementation
   (green), then refactor. No production change lands without a test that would
   have failed before it.
2. **Never weaken an integrity or isolation invariant** (see
   [Security & integrity invariants](#security--integrity-invariants)). If a
   change touches the hash scheme, Merkle construction, anchoring, verification,
   or tenant scoping, it needs explicit tests proving the guarantee still holds.
3. **English only** for all code, comments, docs, and commit messages.
4. **Clean, production-grade code.** No dead code, no "temporary" hacks, no
   commented-out blocks, no console noise. Match the surrounding style.
5. **Conventional Commits**, imperative, scoped (e.g. `feat(F14):`,
   `fix(swagger):`, `refactor(merkle):`). **Never** add co-authors: no
   `Co-Authored-By` lines, and no agent/assistant name or trailer, in any
   commit, ever. This overrides any default attribution behavior.
6. **Do not commit** internal/business material (roadmaps, commercialization
   plans, internal notes). Keep them out of the repo.
7. **Every change updates the harness.** Whenever you adjust anything (code,
   config, scripts, deploy, docs, behavior), update the affected skills in
   `.claude/skills/` (`flow-*`, `aeternis-map`, `aeternis-dev`, `ship-check`)
   and `.claude/agents/` **in the same commit/PR**: entry points (`file:line`),
   step-by-step behavior, invariants, tests, and "Known gaps" (remove a gap
   once it is fixed; add one when you find it). A new subsystem or flow gets
   its own `flow-*` skill and a row in `aeternis-map`. A change that leaves a
   skill stale is not done.

---

## Harness (skills & agents in `.claude/`)

This repo ships its own Claude Code harness. Start with the **`aeternis-map`**
skill: it routes any task to the right deep-dive.

- **Workflow skills:** `aeternis-dev` (the TDD dev loop), `ship-check`
  (the pre-PR gate).
- **Flow skills (`flow-*`)**, one per subsystem. Each lists entry points
  (`file:line`), step-by-step behavior, invariants, failure modes, tests, how
  to change it safely, debug recipes, and known gaps:
  - integrity path: `flow-record-ingest`, `flow-hash-scheme`,
    `flow-merkle-tree`, `flow-batch-anchoring`, `flow-batch-reconciliation`,
    `flow-batch-verification`, `flow-public-verification`,
    `flow-record-read-delete`
  - security: `flow-tenancy`, `flow-http-middleware`
  - ledger: `flow-chaincode`, `flow-fabric-transport`,
    `flow-fabric-dev-network`, `flow-fabric-staging-network`
  - persistence: `flow-wal`, `flow-mongo-migrations`, `flow-redis`
  - outputs: `flow-webhook`, `flow-audit-reports`, `flow-observability`
  - runtime: `flow-config-lifecycle`
  - clients: `flow-sdk-go`, `flow-sdk-python`
  - ops and meta: `flow-local-stack`, `flow-kubernetes-helm`,
    `flow-testing-strategy`, `flow-docs-site`
- **Agent:** `integrity-reviewer` audits (read-only) any diff on the
  integrity, crypto or tenant-isolation surface.

**Keep the harness true:** when a change moves code, alters behavior, or fixes
a gap listed in a flow skill, update that skill in the same PR.

---

## Architecture & where things live

```
POST /api/v1/{domain}/records → MongoDB → batch (Merkle root) → anchor on Fabric
                                                   ↓
            /api/v1/{domain}/records/verify/{batchId} recomputes vs the chain
```

| Path | What it is |
|---|---|
| `api/` | **The product.** Go REST API (Gin), module `github.com/RicardoMBregalda/aeternis-log/go-api`, Go 1.25 |
| `api/cmd/api/` | API entrypoint · `api/cmd/migrate/` schema-migration runner |
| `api/internal/` | `handlers` · `database` (Mongo) · `cache` (Redis) · `fabric` (Gateway gRPC) · `merkle` (batching) · `models` (record + crypto) · `wal` · `webhook` · `middleware` · `migrations` · `report` · `metrics` · `logger` |
| `api/pkg/config/` | Config loading (`config.yaml` + env overrides) |
| `hybrid-architecture/chaincode/` | Fabric smart contract (Go, module `github.com/chaincode`) — the ledger-side isolation boundary |
| `hybrid-architecture/fabric-network/` | Peers, orderer, CA, channel & chaincode setup scripts. `prod/` = a **local staging** Fabric network, not remote prod |
| `sdk/go/` | Go SDK (stdlib only) — recomputes hashes/roots **locally**, module `…/sdk/go`, Go 1.21 |
| `sdk/python/` | Python SDK + CLI (stdlib only), `requires-python >=3.8` |
| `deploy/helm/aeternislog/` | Helm chart · `deploy/observability/` dashboards |
| `website/` | Astro + Starlight documentation/marketing site (separate workflow; see its own files) |
| `docs/` | Runbooks & guides (migrations, tenant isolation, compliance, supply chain) |

A **log is just a record in the `logs` domain** — there is no separate logs
surface. The API is a single generic `records` API scoped by `{tenant, domain}`.

---

## Commands

Run from the repo root unless noted. Prefer `make` over raw `docker compose`
(compose v2 is intermittent in this environment).

```bash
make up        # bring up EVERYTHING: Fabric network → API → MongoDB → Redis → dashboard
make down      # stop everything, keep data
make status    # containers on the shared network
make clean     # stop AND remove volumes (destroys Mongo/Redis/WAL data)

make dev       # only MongoDB + Redis (then run the API natively)
make run       # run the API natively (needs `make dev` first)
make api       # API in a container (no Fabric)

make build     # go build ./...   (api module)
make test      # go test ./...    (api module)
make vet       # go vet ./...
make smoke     # black-box e2e against a running dev API
```

Mongo- and Redis-backed tests **skip silently** when those services are down.
Run `make dev` first, and check the `-v` output for `--- SKIP` before you call
a run green (see `flow-testing-strategy`).

The other modules have their own tests — run them directly:

```bash
cd hybrid-architecture/chaincode && go test ./...   # chaincode (incl. isolation test)
cd sdk/go && go test ./...                          # Go SDK + conformance
cd sdk/python && python -m pytest                    # Python SDK (or: python -m unittest discover -s tests)
```

Live endpoints when up: API `http://localhost:5001`, Swagger
`/swagger/index.html`, health `/health`, dashboard `http://localhost:8088`.

---

## Conventions

- **Go:** standard layout (`cmd` / `internal` / `pkg`). Handlers return the
  `models.ErrorResponse` / `SuccessResponse` envelopes with an HTTP status; log
  via `logger.WithRequestID(c.GetString("request_id"))`. Keep `internal`
  internal. Tests are table-driven where it helps and live beside the code.
- **Swagger is generated, not hand-written.** The source of truth is the
  `@Summary/@Router/...` annotations on the handlers; `api/docs/docs.go` and
  `api/docs/swagger.yaml` are **regenerated during the Docker image build**
  (`swag init -g cmd/api/main.go -o ./docs --parseInternal`). Don't hand-edit the
  generated files — edit the annotations and rebuild the image.
- **Config:** `api/config.yaml` with env overrides (`api/.env.example` is the
  catalog). Sections: `server, mongodb, redis, fabric, wal, batching, logging,
  metrics, auth, rate_limit, webhook`. Auth and rate limiting are **opt-in** (off by
  default); enable in production.
- **Migrations** are versioned and applied by `cmd/migrate`. Boot **asserts**
  schema state, it never mutates — add a migration, don't mutate on startup.
- **Tenancy:** every record operation is scoped to `{tenant, domain}`. The API
  resolves the tenant from the caller's API key (`auth.tenants`); flat keys map
  to tenant `default`.

---

## Security & integrity invariants

These are the product. A change that breaks one of these is a release-blocker,
even if every test passes. When you touch this surface, **prove** the guarantee
with a test.

- **Hash scheme v2 (`models.CurrentHashVersion = 2`).** Leaf hash is
  `SHA-256(0x00 ‖ lenPrefix(id) ‖ lenPrefix(timestamp) ‖ lenPrefix(source) ‖
  lenPrefix(canonicalPayload))`. Length-prefixing stops content from shifting
  across field boundaries undetected; the `0x00` leaf tag domain-separates a
  leaf from an internal node (`0x01`).
- **Merkle construction** promotes a lone odd trailing node instead of
  duplicating it (defends **CVE-2012-2459**). Internal nodes are tagged `0x01`.
- **Version compatibility.** Records carry `hash_version`; verification
  recomputes with the **recorded** version, so batches anchored under legacy v1
  still verify. Never retroactively rewrite anchored batches.
- **Canonical payload** uses sorted JSON keys → the hash is independent of key
  order. `hash_fields` (when set) restricts which payload keys feed the hash and
  is **stored** so verification stays reproducible.
- **Soft-delete is excluded from the hash.** `DeletedAt` never feeds the
  integrity hash, so deleting a record can't invalidate an anchored proof.
- **Verification recomputes from current content** — it must never trust the
  stored `hash`. A tampered record yields a different leaf → different root →
  `409 CORRUPTED`.
- **Tenant isolation is enforced at the ledger boundary.** The chaincode derives
  the tenant from the **client identity** (CA-signed `tenant` cert attribute,
  else MSP ID) — **never** from a function argument — and keys batch state by a
  tenant-scoped composite key. A caller cannot read or overwrite another
  tenant's state. The API layer scopes Mongo queries by tenant too; both layers
  must stay consistent.
- **Public verification** (`/public/anchors/{batchId}`) is unauthenticated by
  design and must expose only on-chain proof data, never tenant payloads.

---

## Local environment (WSL)

- Develop inside WSL: `wsl.exe -d Ubuntu-22.04 -- bash -lc '…'` from this
  Windows host. The repo lives at `/root/tcc-log-management`.
- Two Docker daemons coexist (native WSL vs Docker Desktop); the tcc stacks run
  under Docker Desktop. Prefer `make` targets over `docker compose` directly.
- **Never `git add -A`** — WSL surfaces spurious exec-bit (mode) changes. Stage
  paths explicitly.
- WSL git has no credentials; **push via Windows git** over the UNC path
  (`\\wsl.localhost\Ubuntu-22.04\root\tcc-log-management`).

---

## Definition of done

A change is done when: a test that would have failed now passes · `make test` +
`make vet` are green with no unexpected skips · the relevant SDK/chaincode tests
pass if you touched them · no integrity/isolation invariant is weakened · public
docs/Swagger annotations are updated if the API surface changed · the affected
`flow-*` skills still describe the code · the commit follows the conventions
above. See the `ship-check` skill for the full pre-PR gate.
