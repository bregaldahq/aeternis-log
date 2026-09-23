---
name: aeternis-dev
description: >-
  The canonical development loop for the AeternisLog platform (Go API +
  Hyperledger Fabric chaincode + Go/Python SDKs). Use when implementing or
  changing backend behavior — a new endpoint, batching/anchoring logic, the hash
  or Merkle scheme, chaincode, migrations, config, webhooks, or SDK code — and
  you need to do it test-first, run the right suite, verify integrity end to
  end, and follow the repo's conventions. Not for the website/ docs site (see
  flow-docs-site).
---

# AeternisLog development loop

This skill encodes how to make a change to the AeternisLog backend correctly.
The product is tamper-evident data anchoring going to market: TDD is mandatory
and the integrity/isolation invariants in `CLAUDE.md` are non-negotiable. Work in
WSL: `wsl.exe -d Ubuntu-22.04 -- bash -lc '…'`, repo at `/root/tcc-log-management`.

## 1. Locate the change

Map the work to a module before writing anything:

| Change | Lives in | Load first | Test command (from repo root unless noted) |
|---|---|---|---|
| HTTP surface / handler | `api/internal/handlers/` | `flow-record-ingest`, `flow-record-read-delete`, `flow-http-middleware` | `make test` |
| Hash / record model | `api/internal/models/` | `flow-hash-scheme`, `flow-merkle-tree` | `cd api && go test ./internal/models/...` |
| Batching / Merkle / anchoring | `api/internal/merkle/` | `flow-batch-anchoring`, `flow-batch-reconciliation`, `flow-batch-verification` | `make dev && cd api && go test ./internal/merkle/...` |
| Fabric transport | `api/internal/fabric/` | `flow-fabric-transport` | `cd api && go test ./internal/fabric/...` |
| Schema change | `api/internal/migrations/` (+ `cmd/migrate`) | `flow-mongo-migrations` | `make dev && cd api && go test ./internal/migrations/...` |
| Tenancy / auth | `middleware`, `handlers`, `config`, chaincode | `flow-tenancy` | API + chaincode suites |
| Ledger logic / isolation | `hybrid-architecture/chaincode/` | `flow-chaincode` | `cd hybrid-architecture/chaincode && go test ./...` |
| Go SDK | `sdk/go/` | `flow-sdk-go` | `cd sdk/go && go test ./...` |
| Python SDK / CLI | `sdk/python/` | `flow-sdk-python` | `cd sdk/python && python -m pytest` |
| Config / boot | `api/pkg/config/`, `cmd/api/main.go` | `flow-config-lifecycle` | `cd api && go test ./pkg/config/...` |
| Deploy | `deploy/helm/`, compose files | `flow-kubernetes-helm`, `flow-local-stack` | `helm lint`, `make up` |

Not sure? Load `aeternis-map`. Read the flow skill(s) **before** editing:
they list the invariants, the existing tests and fakes to reuse, and the
known gaps in that area.

Read the neighboring code first and match its style, error envelopes
(`models.ErrorResponse`/`SuccessResponse`), logging
(`logger.WithRequestID(...)`), and tenant scoping (`tenantFrom(c)`).

## 2. Red — write the failing test first

Always. Add the test that captures the new behavior or the bug, beside the code,
and run it to confirm it **fails for the right reason**. On the integrity surface
this means an explicit guarantee test, e.g.:

- tamper a record's payload → `verify` returns `409 CORRUPTED`;
- a batch anchored under v1 still verifies after a v2 change (version compat);
- soft-deleting a record does **not** change its leaf hash;
- a key for tenant A cannot read/anchor tenant B's batch (chaincode isolation
  test pattern lives in `logchaincode_isolation_test.go`).

Do not proceed until you've seen red.

## 3. Green — implement the minimum to pass

Write production-grade code that makes the test pass without weakening any
invariant. Re-run the focused suite, then widen:

```bash
make dev                                     # Mongo + Redis, or those suites silently SKIP
make build && make vet && make test          # api module
# plus the focused suite(s) for any other module you touched
```

Check `go test -v` output for `--- SKIP` on the suites that matter to your
change (`flow-testing-strategy`).

If you changed the API surface, update the handler's `@Summary/@Param/@Router`
Swagger annotations — they are the source of truth (the generated
`api/docs/*` are rebuilt during the image build; don't hand-edit them).

## 4. Verify integrity end to end (when behavior changed at runtime)

For changes that affect the live create→anchor→verify path, bring the stack up
and exercise it rather than trusting unit tests alone:

```bash
make up                 # Fabric → API → Mongo → Redis (idempotent; data preserved)
make smoke              # black-box e2e against the running dev API
# or drive it directly:
curl -s -XPOST localhost:5001/api/v1/contracts/records \
  -H 'Content-Type: application/json' \
  -d '{"source":"crm","payload":{"party":"acme","amount":100}}'
# …batch, then POST /verify/{batchId} and confirm the root matches the chain
```

`make up` is the reliable path (compose v2 is intermittent here). If the stack
was dropped by an environment restart, `make up` restores it — data in the
volumes is safe unless you ran `make clean`.

## 5. Conventions to honor

- **English** everywhere; **clean** code (no dead code/TODO dumps/commented
  blocks).
- Commit with **Conventional Commits** (`feat(...)`, `fix(...)`, `refactor(...)`),
  imperative; **no `Co-Authored-By` / agent trailer**.
- **Never `git add -A`** (WSL exec-bit noise) — stage explicit paths. Push via
  Windows git over the UNC path.
- **Never commit** internal/business docs (roadmaps, commercialization plans).
- **Keep the harness true:** if you moved code, changed behavior, or fixed a
  known gap, update the affected `flow-*` skill(s) in the same change.

When you've touched the integrity, crypto, anchoring, or tenant-isolation
surface, hand the diff to the `integrity-reviewer` agent before opening a PR, and
run the `ship-check` skill as the final gate.
