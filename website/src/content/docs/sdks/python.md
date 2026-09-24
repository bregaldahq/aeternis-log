---
title: Python SDK
description: A dependency-free Python client and offline verifier for the AeternisLog API, with a CLI for auditors.
sidebar:
  order: 2
---

Python client and **offline verifier**. Like the [Go SDK](/aeternis-log/sdks/go/),
it recomputes record hashes and Merkle roots **locally and independently of the
server**. Standard library only.

## Install

```bash
pip install aeternislog          # once published
# or, from this repo:
pip install ./sdk/python
```

## Library

```python
from aeternislog import Client

client = Client("http://localhost:5001", api_key="my-key")  # api_key optional

# Create a record. The server's returned hash is checked against the independently
# computed one (raises on mismatch).
rec = client.create_record("audit", source="payments",
                           payload={"event": "charge", "amount": 1200})

# Batch the domain's pending records and anchor the Merkle root.
batch = client.batch_records("audit")
print(batch.tx_id, batch.merkle_root, batch.channel)

# Verify server-side (a CORRUPTED batch returns is_valid=False; it does not raise).
assert client.verify_batch("audit", batch.batch_id).is_valid
```

## Trustless local verification

```python
from aeternislog import Record, merkle_root, verify_records_locally

records = [Record.from_api(r) for r in client.list_records("audit")["records"]]
assert verify_records_locally(records, expected_root="<root from the blockchain>")
```

## CLI — offline audit

The package installs an `aeternislog` command for auditors who hold a CSV export:

```bash
# Fully offline: recompute the root and compare with the on-chain root.
aeternislog verify --file records.csv --expected-root <root>

# Or let the tool fetch the on-chain root from the API by batch id.
aeternislog verify --file records.csv --api http://host:5001 --domain audit --batch-id audit-…

aeternislog merkle --file records.csv     # print a CSV's Merkle root

# Batches anchored under the legacy v1 scheme
aeternislog verify --file legacy.csv --expected-root <root> --hash-version 1
```

`verify` exits `0` for **VALID** and `2` for **CORRUPTED**, so it drops into
CI/cron. With `--api/--batch-id` it compares against the root read from the
**ledger** (`on_chain_merkle_root`), never the database's copy, and refuses
batches that are not anchored on-chain.

CSV columns: `id,timestamp,source,payload`, plus optional `hash_fields` and
`hash_version`. Rows without `hash_version` use `--hash-version` (default: the
current scheme, v2). Row order must match the anchored batch.
