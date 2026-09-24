"""``aeternislog`` command-line tool.

Offline integrity verification for auditors: given a CSV export of records,
recompute the Merkle root locally and compare it with the root anchored on the
blockchain — without trusting the API to do the recomputation.

    aeternislog merkle  --file records.csv
    aeternislog verify  --file records.csv --expected-root <on-chain root>
    aeternislog verify  --file records.csv --api http://host:5001 --domain audit \\
                        --batch-id audit-... [--key <api-key>]
    aeternislog hash    --id ID --timestamp TS --source SRC --payload '{"k":"v"}'

CSV columns: id, timestamp, source, payload (a JSON object string); optional
hash_fields (JSON array or comma-separated) and hash_version (the record's
integrity-hash scheme). Records without a hash_version use --hash-version, which
defaults to the current scheme; pass --hash-version 1 for batches anchored under
the legacy scheme. Row order must match the anchored batch (the API returns
records in batch order).

With --api/--batch-id, the root is compared against the one read from the
ledger (``on_chain_merkle_root``), never the root stored in the database.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from typing import List, Optional, Sequence

from .client import Client
from .errors import AeternisLogError
from .record import CURRENT_HASH_VERSION, Record, merkle_root

_ANCHORED = "ANCHORED"


def _parse_hash_version(raw: str, where: str) -> int:
    try:
        return int(raw)
    except ValueError:
        raise SystemExit(f"error: {where}: hash_version must be an integer, got {raw!r}")


def _load_csv(path: str, default_hash_version: int) -> List[Record]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {"id", "timestamp", "source", "payload"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"error: CSV is missing required columns: {sorted(missing)}")

        records: List[Record] = []
        for line, row in enumerate(reader, start=2):
            raw_payload = (row.get("payload") or "").strip()
            try:
                payload = json.loads(raw_payload) if raw_payload else {}
            except json.JSONDecodeError as e:
                raise SystemExit(f"error: row {line}: invalid payload JSON: {e}")

            hash_fields: Optional[List[str]] = None
            raw_hf = (row.get("hash_fields") or "").strip()
            if raw_hf:
                try:
                    hash_fields = json.loads(raw_hf)
                except json.JSONDecodeError:
                    hash_fields = [s.strip() for s in raw_hf.split(",") if s.strip()]

            raw_hv = (row.get("hash_version") or "").strip()
            hash_version = (
                _parse_hash_version(raw_hv, f"row {line}") if raw_hv else default_hash_version
            )

            records.append(
                Record(
                    id=row["id"],
                    timestamp=row["timestamp"],
                    source=row["source"],
                    payload=payload,
                    hash_fields=hash_fields,
                    hash_version=hash_version,
                )
            )
    return records


def _cmd_merkle(args: argparse.Namespace) -> int:
    print(merkle_root(_load_csv(args.file, args.hash_version)))
    return 0


def _cmd_hash(args: argparse.Namespace) -> int:
    payload = json.loads(args.payload) if args.payload else {}
    rec = Record(
        id=args.id,
        timestamp=args.timestamp,
        source=args.source,
        payload=payload,
        hash_fields=args.hash_field or None,
        hash_version=args.hash_version,
    )
    print(rec.compute_hash())
    return 0


def _fetch_on_chain_root(args: argparse.Namespace) -> str:
    """Read the batch's root from the ledger through the API. The database root
    is never used: an auditor must compare against the on-chain anchor."""
    if not args.domain:
        raise SystemExit("error: --batch-id requires --domain")
    client = Client(args.api, api_key=args.key)
    try:
        result = client.verify_batch(args.domain, args.batch_id)
    except AeternisLogError as e:
        raise SystemExit(f"error: could not fetch anchored root: {e}")
    if result.anchor_status != _ANCHORED or not result.on_chain_merkle_root:
        raise SystemExit(
            f"error: batch {args.batch_id} has no on-chain root to compare against "
            f"(anchor_status={result.anchor_status or 'missing'})"
        )
    return result.on_chain_merkle_root


def _cmd_verify(args: argparse.Namespace) -> int:
    records = _load_csv(args.file, args.hash_version)
    local_root = merkle_root(records)

    expected = args.expected_root
    if not expected and args.batch_id:
        expected = _fetch_on_chain_root(args)
    if not expected:
        raise SystemExit("error: provide --expected-root, or --api/--domain/--batch-id")

    ok = local_root == expected
    print(f"records:       {len(records)}")
    print(f"local root:    {local_root}")
    print(f"anchored root: {expected}")
    print(f"integrity:     {'VALID' if ok else 'CORRUPTED'}")
    return 0 if ok else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aeternislog", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    version_help = (
        f"hash scheme for records without a hash_version column (default {CURRENT_HASH_VERSION}; "
        "use 1 for legacy batches)"
    )

    p_merkle = sub.add_parser("merkle", help="compute the Merkle root of a CSV (offline)")
    p_merkle.add_argument("--file", required=True, help="path to the records CSV")
    p_merkle.add_argument("--hash-version", type=int, default=CURRENT_HASH_VERSION, help=version_help)
    p_merkle.set_defaults(func=_cmd_merkle)

    p_verify = sub.add_parser("verify", help="recompute a CSV's root and compare with the anchored root")
    p_verify.add_argument("--file", required=True, help="path to the records CSV")
    p_verify.add_argument("--expected-root", default="", help="anchored Merkle root (fully offline mode)")
    p_verify.add_argument("--api", default="http://localhost:5001", help="API base URL (to fetch the anchored root)")
    p_verify.add_argument("--domain", default="", help="record domain (with --batch-id)")
    p_verify.add_argument("--batch-id", default="", help="anchored batch id (fetch its root via the API)")
    p_verify.add_argument("--key", default="", help="API key")
    p_verify.add_argument("--hash-version", type=int, default=CURRENT_HASH_VERSION, help=version_help)
    p_verify.set_defaults(func=_cmd_verify)

    p_hash = sub.add_parser("hash", help="compute the integrity hash of a single record")
    p_hash.add_argument("--id", required=True)
    p_hash.add_argument("--timestamp", required=True)
    p_hash.add_argument("--source", required=True)
    p_hash.add_argument("--payload", default="{}", help="payload as a JSON object")
    p_hash.add_argument("--hash-field", action="append", help="restrict the hash to this payload key (repeatable)")
    p_hash.add_argument(
        "--hash-version", type=int, default=CURRENT_HASH_VERSION,
        help=f"hash scheme (default {CURRENT_HASH_VERSION}; use 1 for legacy records)",
    )
    p_hash.set_defaults(func=_cmd_hash)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
