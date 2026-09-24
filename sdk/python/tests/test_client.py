import io
import json
import unittest
import urllib.error
from unittest import mock

from aeternislog.client import Client
from aeternislog.errors import APIError, HashMismatchError
from aeternislog.record import Record


class _Resp:
    """Minimal stand-in for the urlopen context-manager response."""

    def __init__(self, body):
        self._b = body.encode() if isinstance(body, str) else body

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code, body):
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(body.encode()))


def _good_server(req, timeout=None):
    """Simulate the real server: it hashes new records under the v2 scheme."""
    body = json.loads(req.data.decode())
    rec = Record(
        id=body["id"], timestamp=body["timestamp"], source=body["source"],
        payload=body["payload"], hash_fields=body.get("hash_fields"), hash_version=2,
    )
    return _Resp(json.dumps({"data": {"id": body["id"], "hash": rec.compute_hash()}}))


class TestClient(unittest.TestCase):
    def setUp(self):
        # No real backoff sleeps in tests.
        patcher = mock.patch("aeternislog.client.time.sleep")
        self.addCleanup(patcher.stop)
        patcher.start()
        self.client = Client("http://localhost:5001", max_retries=2)

    def test_create_record_trustless_ok(self):
        with mock.patch("aeternislog.client.urllib.request.urlopen", side_effect=_good_server):
            rec = self.client.create_record("audit", "app", {"event": "login", "n": 3})
        self.assertEqual(len(rec.id), 32)  # 16 random bytes hex
        self.assertEqual(rec.hash_version, 2)
        self.assertEqual(rec.hash, rec.compute_hash())

    def test_create_record_hash_mismatch_raises(self):
        def bad_server(req, timeout=None):
            return _Resp(json.dumps({"data": {"id": "x", "hash": "deadbeef"}}))

        with mock.patch("aeternislog.client.urllib.request.urlopen", side_effect=bad_server):
            with self.assertRaises(HashMismatchError):
                self.client.create_record("audit", "app", {"k": "v"})

    def test_4xx_not_retried(self):
        m = mock.Mock(side_effect=_http_error(400, '{"error":"bad"}'))
        with mock.patch("aeternislog.client.urllib.request.urlopen", m):
            with self.assertRaises(APIError) as ctx:
                self.client.get_record("audit", "x")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(m.call_count, 1)

    def test_5xx_retried_then_succeeds(self):
        m = mock.Mock(side_effect=[
            _http_error(500, "boom"),
            _http_error(503, "still"),
            _Resp(json.dumps({"id": "r1", "hash": "abc"})),
        ])
        with mock.patch("aeternislog.client.urllib.request.urlopen", m):
            rec = self.client.get_record("audit", "r1")
        self.assertEqual(rec.id, "r1")
        self.assertEqual(m.call_count, 3)

    def test_network_error_retried(self):
        m = mock.Mock(side_effect=[
            urllib.error.URLError("conn refused"),
            _Resp(json.dumps({"id": "r2", "hash": "x"})),
        ])
        with mock.patch("aeternislog.client.urllib.request.urlopen", m):
            rec = self.client.get_record("audit", "r2")
        self.assertEqual(rec.id, "r2")
        self.assertEqual(m.call_count, 2)

    def test_verify_409_returned_as_result(self):
        payload = json.dumps({
            "batch_id": "b1", "is_valid": False,
            "original_merkle_root": "aaa", "recalculated_merkle_root": "bbb",
            "integrity": "CORRUPTED",
        })
        with mock.patch("aeternislog.client.urllib.request.urlopen", side_effect=_http_error(409, payload)):
            res = self.client.verify_batch("audit", "b1")
        self.assertFalse(res.is_valid)
        self.assertEqual(res.integrity, "CORRUPTED")

    def test_verify_exposes_anchor(self):
        body = json.dumps({
            "batch_id": "b1", "is_valid": True, "num_logs": 3,
            "original_merkle_root": "db", "recalculated_merkle_root": "chain",
            "on_chain_merkle_root": "chain", "anchor_status": "ANCHORED",
            "integrity": "VALID", "message": "ok",
        })
        with mock.patch("aeternislog.client.urllib.request.urlopen", side_effect=lambda *a, **k: _Resp(body)):
            res = self.client.verify_batch("audit", "b1")
        self.assertEqual(res.on_chain_merkle_root, "chain")
        self.assertEqual(res.anchor_status, "ANCHORED")
        self.assertEqual(res.num_records, 3)

    def test_batch_result_parsed(self):
        body = json.dumps({
            "batch_id": "audit-1", "tenant": "default", "domain": "audit",
            "merkle_root": "root", "num_records": 5, "tx_id": "tx", "anchored": True,
            "channel": "logchannel",
        })
        with mock.patch("aeternislog.client.urllib.request.urlopen", side_effect=lambda *a, **k: _Resp(body)):
            res = self.client.batch_records("audit")
        self.assertTrue(res.anchored)
        self.assertEqual(res.num_records, 5)
        self.assertEqual(res.channel, "logchannel")


if __name__ == "__main__":
    unittest.main()
