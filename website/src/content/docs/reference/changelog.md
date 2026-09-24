---
title: Changelog
description: Notable changes to AeternisLog.
sidebar:
  order: 2
---

A high-level log of notable changes. For the full commit history, see
[GitHub](https://github.com/RicardoMBregalda/aeternis-log).

## SDK & CLI verification fixes

- **SDKs hash new records under v2.** The Go and Python clients now create
  records with the server's current hash scheme, so the trustless
  create-record check passes against a live API. Previously they computed the
  legacy v1 hash locally and rejected every server response.
- **CLI verifies v2 batches.** `aeternislog merkle|verify|hash` default to the
  current scheme. A `hash_version` CSV column and a `--hash-version` flag cover
  batches anchored under v1.
- **CLI trusts the ledger, not the database.** `verify --api/--batch-id`
  compares against the on-chain root and refuses batches that are not anchored.
- **Verify results expose the anchor.** `VerifyResult` in both SDKs now carries
  `on_chain_merkle_root`, `anchor_status`, the record count and the message.

## Production hardening

A structured hardening pass across the stack:

### Security & cryptography
- Hardened **v2 hashing** — length-prefixed fields, leaf/node domain separation,
  versioned per record.
- **CVE-2012-2459** closed — odd Merkle nodes are promoted, not duplicated.
- **Ledger-enforced tenant isolation** — chaincode keys batch state by the
  caller's signed identity; composite `(tenant, batchID)` keys.
- **Per-tenant gateway identities** in the API (identity pool, selected by tenant).
- **Locked-down public endpoint** — server-side channel resolution; no metadata
  leaks.

### Durability & correctness
- **Record-aware WAL** with idempotent crash recovery.
- **Race-free batch claiming** + anchor **reconciler**.
- **Write-once anchors** enforced on-chain.

### Operations
- **Versioned schema migrations** — assert-not-mutate boot, unique version index,
  Helm pre-upgrade Job.
- **Gateway-only Fabric transport** — the Docker-socket transport removed.
- **Scripted chaincode upgrades** at incremented sequence.
- **Honest Helm chart** — WAL PVC, replica gating, least-privilege identity,
  datastore guidance.
- **Swagger** regenerated in the image build; dead endpoints/config pruned.

### Tooling
- Reworked **web integrity dashboard** for the records API, served by nginx.
- This **documentation site**.

:::note
Versioned releases and tags will be listed here as they are published.
:::
