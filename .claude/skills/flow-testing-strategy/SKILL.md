---
name: flow-testing-strategy
description: >-
  Map of every test suite in AeternisLog and how to run it for real: pure
  unit tests, Mongo/Redis-backed tests that SKIP silently without
  infrastructure, build-tagged integration tests (Fabric gateway, SDK live),
  chaincode fake-stub tests, SDK conformance vectors, and the black-box
  e2e-test.sh (make smoke). Use when deciding which tests to write or run
  for a change, when interpreting a "green" run, or when adding test seams
  (fakes) to new code.
---

# Flow: testing strategy

TDD is mandatory (red, then green, then refactor). This skill tells you
**where** a test belongs and **how to make sure it actually ran**.

## Suites

| Suite | Command | Needs | Skips silently? |
|---|---|---|---|
| API unit (models, merkle verify table, middleware, config, cursor, logger, metrics, report PDF, webhook, public handler, WAL, fabric construction) | `make test` | nothing | — |
| API Mongo-backed (`database/mongodb_test.go`, `merkle/f03_test.go`, `migrations/runner_test.go`) | `make dev && make test` | Mongo on `localhost:27017` | **yes** (`t.Skipf("MongoDB not available")`) |
| API Redis-backed (`cache/redis_test.go`) | `make dev && make test` | Redis on `localhost:6379` | **yes** |
| Fabric gateway construct (`fabric/gateway_test.go`) | `make test` | generated `crypto-config` | **yes** |
| Fabric gateway E2E (`fabric/gateway_integration_test.go`) | `cd api && go test -tags integration ./internal/fabric/ -v` | dev Fabric up | tagged + skips |
| Chaincode isolation | `cd hybrid-architecture/chaincode && go test ./...` | nothing (fake stub/CID) | — |
| Go SDK unit + conformance | `cd sdk/go && go test ./...` | nothing | — |
| Go SDK live | `cd sdk/go && go test -tags integration -run TestSDKLive -v` | `make up` | tagged |
| Python SDK | `cd sdk/python && python -m pytest -q` | Python ≥ 3.8 | — (`integration_test.py` needs a live API) |
| Black-box e2e | `make smoke` (dev) or `bash api/scripts/e2e-test.sh` (staging defaults) | full stack | exits non-zero on any FAIL |

**Always check for skips** before calling a run green:

```bash
cd api && go test ./... -v 2>&1 | grep -E -- '--- SKIP' || echo 'no skips'
```

A skipped `f03_test.go` means reconciliation and anchor-failure guarantees
were **not** exercised.

## e2e-test.sh sections

Infra/health (and metrics) → Auth (when `KEY` is set) → Records CRUD
(domain `qa`) → soft delete + duplicate id (409, no resurrection) → cursor
pagination → batch/anchor/verify on Fabric → tamper detection (mutates
Mongo, expects `409 CORRUPTED`; skipped when `MONGO=""`) → multi-tenant
isolation (skipped when `KEY2=""`, which `make smoke` sets). Env: `BASE`, `METRICS`, `KEY`, `KEY2`,
`EXPECT_CHANNEL`, `MONGO`. Its defaults target **staging** (5002,
`aeternislog-mongodb-prod`, staging keys). `make smoke` overrides them for dev.

## Seams to reuse (don't reinvent)

| Seam | Where | Fake |
|---|---|---|
| `merkle.Anchorer` | `batch_processor.go:23` | `fakeAnchorer` in `merkle/f03_test.go` |
| `handlers.batchQuerier` | `public.go:16` | `handlers/public_test.go` |
| `wal.RecordLog` | `wal.go:41` | implement Append/Confirm/Stats |
| `middleware.RateStore` | `middleware.go:118` | memory store |
| Chaincode `TransactionContextInterface` | `logchaincode_isolation_test.go` | `fakeStub`, `fakeCID`, `ctxFor`, `ctxForMSP` |
| HTTP | — | `httptest.NewRecorder` + `gin.New()` with just the handler and middleware under test |

## Coverage gaps worth closing (from the flow skills)

- No handler tests for `CreateRecord`, `ListRecords`, `GetRecord`,
  `DeleteRecord`, `ForceRecordBatch`, `VerifyRecordBatch` (status mapping,
  tenant from context).
- No API-level cross-tenant 404 test (only chaincode and e2e cover
  isolation).
- No test that deleting a **batched** record keeps `verify` VALID.
- No chaincode test for same-tenant write-once ("already anchored").
- No aggregation test for audit reports. No keyset test for equal
  `created_at`.

## Rules

- **Fakes must behave like the real component.** A fake server that hashes
  with a different scheme than the API hid a v1/v2 bug in both SDKs until
  `fix(sdk)`. When the real behavior changes, update its fakes in the same PR.
- A guarantee test for every change to the integrity or isolation surface:
  tamper → CORRUPTED, cross-tenant → not found, legacy version still
  verifies, and so on.
- Tests live beside the code and are table-driven where it helps.
- Tests that need infrastructure must skip with a clear message, never fail
  obscurely. The **reporter** (you or CI) must surface skips.
- Integration tests go behind a `//go:build integration` tag.

## Related

`flow-local-stack` · every `flow-*` skill's "Tests" section · `ship-check`
