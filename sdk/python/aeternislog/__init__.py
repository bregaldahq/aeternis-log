"""AeternisLog — Python client for the Tamper-Evident Data Anchoring API.

Besides wrapping the HTTP API (with automatic retries), it recomputes record
hashes and Merkle roots locally and independently of the server, so an auditor can
verify integrity without trusting the API.

    from aeternislog import Client

    client = Client("http://localhost:5001", api_key="...")
    rec = client.create_record("audit", source="app", payload={"event": "login"})
    batch = client.batch_records("audit")          # anchors on the blockchain
    result = client.verify_batch("audit", batch.batch_id)
    assert result.is_valid
"""
from .client import BatchResult, Client, VerifyResult
from .errors import AeternisLogError, APIError, HashMismatchError
from .record import CURRENT_HASH_VERSION, Record, canonical, merkle_root, verify_records_locally

__version__ = "0.1.0"

__all__ = [
    "CURRENT_HASH_VERSION",
    "Client",
    "BatchResult",
    "VerifyResult",
    "Record",
    "canonical",
    "merkle_root",
    "verify_records_locally",
    "AeternisLogError",
    "APIError",
    "HashMismatchError",
    "__version__",
]
