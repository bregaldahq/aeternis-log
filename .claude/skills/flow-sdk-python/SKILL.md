---
name: flow-sdk-python
description: >-
  Deep reference for the Python SDK and auditor CLI (sdk/python, package
  aeternislog, stdlib only, Python >=3.8): Client (create/get/list/batch/
  verify with retries), the Go-compatible canonical JSON encoder, local
  hashing/Merkle verification, and the `aeternislog` CLI (merkle / verify /
  hash over CSV exports). Use when changing the Python SDK or CLI, debugging
  a Python-vs-server hash mismatch, or preparing an auditor workflow.
---

# Flow: Python SDK and CLI (`sdk/python`)

Package `aeternislog` (version 0.1.0 in `pyproject.toml`), **no
dependencies**, console script `aeternislog = aeternislog.cli:main`.

## Modules

| Module | Contents |
|---|---|
| `record.py` | `canonical()` (Go `encoding/json`-compatible), `Record` (`compute_hash` v1/v2 by `hash_version`, `verify`, `from_api`), `merkle_root`, `verify_records_locally` |
| `client.py` | `Client(base_url, api_key="", timeout=10, max_retries=3)`: `create_record`, `get_record`, `list_records` (limit/source/cursor), `batch_records`, `verify_batch` (409 → result). `_do` uses `urllib`, `X-API-Key`, linear backoff `attempt × 0.2 s`, retries 5xx and network errors, raises on 4xx |
| `errors.py` | `AeternisLogError` → `APIError(status_code, body)`, `HashMismatchError(server_hash, local_hash)` |
| `cli.py` | `merkle --file`, `verify --file (--expected-root R \| --api --domain --batch-id [--key])` (exit 0 VALID / 2 CORRUPTED), `hash --id --timestamp --source --payload [--hash-field]` |

CSV format: columns `id, timestamp, source, payload` (JSON object) plus an
optional `hash_fields` (a JSON array or comma list). **Row order must equal
the batch order** (`created_at ↑, id ↑`).

## Canonical JSON parity (see `flow-hash-scheme`)

`canonical()` sorts keys, uses compact separators, keeps UTF-8, escapes
`< > & U+2028 U+2029` like Go, and prints integer-valued floats without
`.0`. **Parity gap:** other floats use `repr()`, which differs from Go for
exponent forms (`1e-05` vs `0.00001`). Bools and `None` are handled, and any
other type raises `TypeError`.

## Known bugs (verify, then fix test-first)

1. **v1/v2 mismatch in `create_record`.** The local `Record` defaults to
   `hash_version=0` (v1), while the server writes v2. `create_record` should
   raise `HashMismatchError` against a real API. `test_client.py`'s fake
   server also computes v1, so the bug passes unit tests. The integration
   test (`tests/integration_test.py`) would catch it. Fix: construct with
   `hash_version=CURRENT_HASH_VERSION`, and make the fake compute v2.
2. **CLI `verify` / `merkle` / `hash` hash with v1.** `_load_csv` and
   `_cmd_hash` never set `hash_version`, and the CSV format has no column for
   it. Every v2 batch therefore reports **CORRUPTED**. Fix: default to the
   current version, and add an optional `hash_version` column / `--hash-version`
   flag for legacy batches.
3. **CLI trusts the server.** With `--api/--batch-id`, the "anchored root"
   is `verify_batch(...).original_merkle_root`, which is **MongoDB's stored
   root**, not the on-chain one. A trustless auditor flow should use
   `on_chain_merkle_root` or the public endpoint `GET /public/anchors/{id}`.
   The PDF report footer tells auditors to use this CLI.
4. `VerifyResult` lacks `on_chain_merkle_root` and `anchor_status`.
5. License metadata says MIT, but the repository `LICENSE` is Apache-2.0
   (the Swagger header also says MIT). Align them.

## Tests

```bash
cd sdk/python && python -m pytest -q          # or: python -m unittest discover -s tests
AETERNISLOG_BASE_URL=http://localhost:5001 python -m pytest tests/integration_test.py -q   # live stack
```

`test_record.py` (canonical encoding, hashing, v1 Merkle, the **shared v2
conformance vector** `test_leaf_and_root_match_server`), `test_client.py`
(fake `urlopen`: retries, 4xx, 409 handling, hash mismatch), `test_cli.py`.

## Invariants

- Stdlib only, Python ≥ 3.8.
- Byte-identical hashing with the server and the Go SDK (the conformance
  vector).
- The CLI's verify must be usable **offline** (`--expected-root`) with a
  root the auditor obtained independently.

## Changing it safely

Mirror every server hash or response change here and in `sdk/go` in the same
PR. Update `sdk/python/README.md` and the website pages `sdks/python.md` and
`sdks/verification.md`.

## Related

`flow-sdk-go` · `flow-hash-scheme` · `flow-merkle-tree` ·
`flow-public-verification` · `flow-audit-reports`
