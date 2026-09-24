import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

from aeternislog import cli
from aeternislog.client import VerifyResult
from aeternislog.record import Record, merkle_root

# Shared v2 golden vector (see test_record.TestConformanceV2 and the server's
# crypto_v2_test.go): the CLI must reproduce it from a plain CSV export.
CONFORMANCE_ROOT = "897b88ee0e8ce4bc54391fd8de95d716c139f0ec972409d7024d64a1835eaf2b"
CONFORMANCE_LEAF1 = "d45b3f3276931bfde83931f183e470620d0289f7d6ef3de77b80a8465940c13c"


def _write_csv(header, rows):
    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(",".join(header) + "\n")
        for r in rows:
            f.write(",".join(r) + "\n")
    return path


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.path = _write_csv(
            ["id", "timestamp", "source", "payload"],
            [
                ("rec-1", "2026-01-01T00:00:00Z", "conformance", '"{""k"":""a""}"'),
                ("rec-2", "2026-01-01T00:00:00Z", "conformance", '"{""k"":""b""}"'),
                ("rec-3", "2026-01-01T00:00:00Z", "conformance", '"{""k"":""c""}"'),
            ],
        )
        self.addCleanup(os.remove, self.path)

    def _run(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli.main(argv)
        return rc, out.getvalue()

    def _legacy_root(self):
        return merkle_root([
            Record(id=f"rec-{i}", timestamp="2026-01-01T00:00:00Z", source="conformance",
                   payload={"k": c}, hash_version=1)
            for i, c in [(1, "a"), (2, "b"), (3, "c")]
        ])

    def test_merkle_defaults_to_current_scheme(self):
        rc, out = self._run(["merkle", "--file", self.path])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), CONFORMANCE_ROOT)

    def test_merkle_legacy_flag(self):
        rc, out = self._run(["merkle", "--file", self.path, "--hash-version", "1"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), self._legacy_root())

    def test_hash_version_column_overrides_default(self):
        path = _write_csv(
            ["id", "timestamp", "source", "payload", "hash_version"],
            [
                ("rec-1", "2026-01-01T00:00:00Z", "conformance", '"{""k"":""a""}"', "1"),
                ("rec-2", "2026-01-01T00:00:00Z", "conformance", '"{""k"":""b""}"', "1"),
                ("rec-3", "2026-01-01T00:00:00Z", "conformance", '"{""k"":""c""}"', "1"),
            ],
        )
        self.addCleanup(os.remove, path)
        rc, out = self._run(["merkle", "--file", path])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), self._legacy_root())

    def test_verify_valid_exit_0(self):
        rc, out = self._run(["verify", "--file", self.path, "--expected-root", CONFORMANCE_ROOT])
        self.assertEqual(rc, 0)
        self.assertIn("VALID", out)

    def test_verify_corrupted_exit_2(self):
        rc, out = self._run(["verify", "--file", self.path, "--expected-root", "wrongroot"])
        self.assertEqual(rc, 2)
        self.assertIn("CORRUPTED", out)

    def test_verify_via_api_compares_with_on_chain_root(self):
        # The database root differs from the ledger's: the CLI must trust the ledger.
        result = VerifyResult(original_merkle_root="tampered-db-root",
                              on_chain_merkle_root=CONFORMANCE_ROOT, anchor_status="ANCHORED")
        with mock.patch("aeternislog.cli.Client") as client_cls:
            client_cls.return_value.verify_batch.return_value = result
            rc, out = self._run(["verify", "--file", self.path, "--domain", "audit", "--batch-id", "b1"])
        self.assertEqual(rc, 0)
        self.assertIn(CONFORMANCE_ROOT, out)
        self.assertNotIn("tampered-db-root", out)

    def test_verify_via_api_rejects_unanchored_batch(self):
        result = VerifyResult(original_merkle_root=CONFORMANCE_ROOT, anchor_status="UNKNOWN")
        with mock.patch("aeternislog.cli.Client") as client_cls:
            client_cls.return_value.verify_batch.return_value = result
            with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stdout(io.StringIO()):
                cli.main(["verify", "--file", self.path, "--domain", "audit", "--batch-id", "b1"])
        self.assertIn("UNKNOWN", str(ctx.exception.code))

    def test_hash_defaults_to_current_scheme(self):
        rc, out = self._run([
            "hash", "--id", "rec-1", "--timestamp", "2026-01-01T00:00:00Z",
            "--source", "conformance", "--payload", '{"k":"a"}',
        ])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), CONFORMANCE_LEAF1)

    def test_hash_legacy_flag(self):
        rc, out = self._run([
            "hash", "--id", "rec-1", "--timestamp", "2026-01-01T00:00:00Z",
            "--source", "conformance", "--payload", '{"k":"a"}', "--hash-version", "1",
        ])
        self.assertEqual(rc, 0)
        expected = Record(id="rec-1", timestamp="2026-01-01T00:00:00Z", source="conformance",
                          payload={"k": "a"}, hash_version=1).compute_hash()
        self.assertEqual(out.strip(), expected)


if __name__ == "__main__":
    unittest.main()
