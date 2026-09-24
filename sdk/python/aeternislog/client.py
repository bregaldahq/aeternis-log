"""HTTP client for the Tamper-Evident Data Anchoring API.

Wraps the REST API with automatic retries (network errors and 5xx, linear
backoff) and, crucially, recomputes hashes locally so callers never have to trust
the server. Standard library only — no third-party dependencies.
"""
from __future__ import annotations

import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from .errors import APIError, HashMismatchError
from .record import CURRENT_HASH_VERSION, Record


@dataclass
class BatchResult:
    batch_id: str = ""
    domain: str = ""
    tenant: str = ""
    merkle_root: str = ""
    num_records: int = 0
    tx_id: str = ""
    anchored: bool = False
    channel: str = ""

    @classmethod
    def from_api(cls, d: Mapping[str, Any]) -> "BatchResult":
        return cls(
            batch_id=d.get("batch_id", ""),
            domain=d.get("domain", ""),
            tenant=d.get("tenant", ""),
            merkle_root=d.get("merkle_root", ""),
            num_records=int(d.get("num_records", 0) or 0),
            tx_id=d.get("tx_id", ""),
            anchored=bool(d.get("anchored", False)),
            channel=d.get("channel", ""),
        )


@dataclass
class VerifyResult:
    """Outcome of a server-side batch verification.

    ``on_chain_merkle_root`` is the root read from the ledger — the value to
    compare a locally recomputed root against. ``original_merkle_root`` is only
    the root stored in the database. ``anchor_status`` says whether the verdict
    was decided against the ledger (``ANCHORED``), only against local state
    (``UNKNOWN``), or the batch is not on the ledger (``UNANCHORED``).
    """

    batch_id: str = ""
    is_valid: bool = False
    num_records: int = 0
    original_merkle_root: str = ""
    recalculated_merkle_root: str = ""
    on_chain_merkle_root: str = ""
    anchor_status: str = ""
    integrity: str = ""
    message: str = ""

    @classmethod
    def from_api(cls, d: Mapping[str, Any]) -> "VerifyResult":
        return cls(
            batch_id=d.get("batch_id", ""),
            is_valid=bool(d.get("is_valid", False)),
            num_records=int(d.get("num_logs", 0) or 0),
            original_merkle_root=d.get("original_merkle_root", ""),
            recalculated_merkle_root=d.get("recalculated_merkle_root", ""),
            on_chain_merkle_root=d.get("on_chain_merkle_root", ""),
            anchor_status=d.get("anchor_status", ""),
            integrity=d.get("integrity", ""),
            message=d.get("message", ""),
        )


def _rfc3339_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Client:
    """Client for the API at ``base_url`` (e.g. ``http://localhost:5001``)."""

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        timeout: float = 10.0,
        max_retries: int = 3,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries

    # -- public API ----------------------------------------------------------

    def create_record(
        self,
        domain: str,
        source: str,
        payload: dict,
        hash_fields: Optional[Sequence[str]] = None,
    ) -> Record:
        """Create a record. The id and timestamp are generated client-side so the
        returned record is fully known and locally verifiable; the server's hash is
        then checked against the independently computed one (raises on mismatch)."""
        rec = Record(
            domain=domain,
            id=secrets.token_hex(16),
            timestamp=_rfc3339_now(),
            source=source,
            payload=payload,
            hash_fields=list(hash_fields) if hash_fields else None,
            hash_version=CURRENT_HASH_VERSION,
        )
        rec.hash = rec.compute_hash()

        body: dict = {"id": rec.id, "timestamp": rec.timestamp, "source": source, "payload": payload}
        if hash_fields:
            body["hash_fields"] = list(hash_fields)

        resp = self._do("POST", f"/api/v1/{domain}/records", body)
        server_hash = (resp.get("data") or {}).get("hash", "")
        if server_hash != rec.hash:
            raise HashMismatchError(server_hash, rec.hash)
        return rec

    def get_record(self, domain: str, record_id: str) -> Record:
        return Record.from_api(self._do("GET", f"/api/v1/{domain}/records/{record_id}"))

    def list_records(self, domain: str, source: str = "", limit: int = 50, cursor: str = "") -> dict:
        query = f"?limit={int(limit)}"
        if source:
            query += f"&source={urllib.parse.quote(source)}"
        if cursor:
            query += f"&cursor={urllib.parse.quote(cursor)}"
        resp = self._do("GET", f"/api/v1/{domain}/records{query}")
        resp["records"] = [Record.from_api(r) for r in resp.get("records", []) or []]
        return resp

    def batch_records(self, domain: str) -> BatchResult:
        """Batch the domain's pending records and anchor the Merkle root on-chain."""
        return BatchResult.from_api(self._do("POST", f"/api/v1/{domain}/records/batch", {}))

    def verify_batch(self, domain: str, batch_id: str) -> VerifyResult:
        """Ask the server to verify a batch. A CORRUPTED batch (HTTP 409) is
        returned as a result with ``is_valid=False``, not raised."""
        try:
            return VerifyResult.from_api(
                self._do("POST", f"/api/v1/{domain}/records/verify/{batch_id}")
            )
        except APIError as e:
            if e.status_code == 409:
                try:
                    return VerifyResult.from_api(json.loads(e.body))
                except (ValueError, TypeError):
                    pass
            raise

    # -- transport -----------------------------------------------------------

    def _do(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        last_err: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            if attempt > 0:
                time.sleep(attempt * 0.2)  # linear backoff

            req = urllib.request.Request(self.base_url + path, data=data, method=method)
            req.add_header("Content-Type", "application/json")
            if self.api_key:
                req.add_header("X-API-Key", self.api_key)

            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:  # non-2xx
                body_txt = e.read().decode("utf-8", errors="replace")
                if e.code >= 500:
                    last_err = APIError(e.code, body_txt)
                    continue  # server error: retry
                raise APIError(e.code, body_txt)  # 4xx: do not retry
            except urllib.error.URLError as e:  # network error
                last_err = e
                continue

        if last_err is not None:
            raise last_err
        raise APIError(0, "request failed without a response")
