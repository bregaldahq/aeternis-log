---
name: ship-check
description: >-
  Pre-PR / release-readiness gate for AeternisLog. Use right before opening a
  pull request, finishing a feature, or cutting a release — it runs the full
  test/vet matrix across every module, re-checks the integrity and tenant-
  isolation invariants, confirms Swagger/docs and migrations are in order, and
  verifies commit & hygiene rules so what ships is commercial-grade. Run this as
  the final step after the work is implemented and locally green.
---

# AeternisLog ship-check

A commercial, security-critical product. Before code leaves your branch, walk
this gate. Work in WSL (`wsl.exe -d Ubuntu-22.04 -- bash -lc '…'`), repo at
`/root/tcc-log-management`. Run the checks, then report a PASS/FAIL table with
the evidence — do not claim green you didn't observe.

## 1. Build, vet, test — every module you touched (and the API always)

```bash
# API (always); Mongo/Redis first or those suites SKIP silently
make dev
make build && make vet && make test
cd api && go test ./... -v 2>&1 | grep -E -- '--- SKIP' ; cd -   # list skips

# Other modules — run if touched (cheap; run them anyway when unsure)
cd hybrid-architecture/chaincode && go test ./... ; cd -
cd sdk/go && go test ./... ; cd -
cd sdk/python && python -m pytest ; cd -      # or: python -m unittest discover -s tests
```

Every suite must be green. A skipped or failing test is a FAIL — report it with
the output, never paper over it.

## 2. Runtime smoke (if behavior at runtime changed)

```bash
make up && make smoke
```

Confirm the create → batch → anchor → verify path works against the live stack
and a verify returns a matching root (and `409 CORRUPTED` on tampered content).

## 3. Integrity & isolation invariants

For any change to `api/internal/{models,merkle,fabric,handlers,migrations}`,
`hybrid-architecture/chaincode`, or SDK local-verification, confirm each invariant
still holds **with a test that proves it** (see `CLAUDE.md` →
"Security & integrity invariants"):

- [ ] v2 leaf hash: `0x00` leaf tag, all fields length-prefixed, sorted-key
      canonical payload, `hash_fields` honored + persisted.
- [ ] Merkle: `0x01` internal tag; odd node **promoted, not duplicated**
      (CVE-2012-2459).
- [ ] `hash_version` recorded; legacy v1 batches still verify; anchored batches
      never rewritten.
- [ ] `verify` recomputes from current content (trusts nothing stored); tamper →
      `409 CORRUPTED`.
- [ ] soft-delete excludes `DeletedAt` from the hash.
- [ ] tenant derived from client identity in chaincode (never an argument); every
      API Mongo query/anchor scoped by `{tenant, domain}`; cross-tenant denied.
- [ ] public anchor endpoint exposes only on-chain proof, no tenant payloads.

When in doubt on this surface, dispatch the **`integrity-reviewer`** agent on the
diff and resolve its findings before proceeding.

## 4. API surface & docs

- [ ] If the HTTP surface changed: handler `@Summary/@Param/@Router` annotations
      updated (the generated `api/docs/*` rebuild at image build — don't hand-edit).
- [ ] `README.md` / relevant `docs/` updated if behavior, config, or guarantees
      changed.
- [ ] If config changed: `api/.env.example` and `config.yaml` reflect new keys.
- [ ] If schema changed: a **versioned migration** was added (boot asserts, never
      mutates).
- [ ] If config changed: Helm `values.yaml`/`configmap.yaml` (or the Secret, if
      sensitive) carry it (`flow-kubernetes-helm`).
- [ ] Production-facing configs keep `auth.enabled: true` (tenancy depends on it).
- [ ] Website pages for the touched area updated (mapping in `flow-docs-site`).
- [ ] The affected `flow-*` skills still describe the code (entry points, behavior,
      known gaps). Update them in this PR if not.

## 5. Hygiene & commit/PR rules

```bash
git status                         # no stray/unintended files
git diff --stat origin/main...HEAD # scope is what you expect
```

- [ ] **No internal/business docs** committed (roadmaps, commercialization plans).
- [ ] No secrets, crypto material, `.env`, or generated Fabric artifacts staged
      (they're gitignored — keep it that way).
- [ ] English throughout; clean code (no dead code, debug prints, commented
      blocks, leftover TODOs).
- [ ] Commits are **Conventional Commits**, imperative, scoped; **no
      `Co-Authored-By` / agent trailer**.
- [ ] Staged with explicit paths — **never `git add -A`** (WSL exec-bit noise).
- [ ] Branch pushed via Windows git over the UNC path (WSL git has no creds).

## Report

Output a compact table: each gate → PASS / FAIL / N/A, with the command output or
file evidence for anything non-trivial. End with a single readiness verdict:
**READY TO OPEN PR** or **NOT READY** + the blocking items.
