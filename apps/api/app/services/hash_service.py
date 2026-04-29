from hashlib import sha256


def compute_sha256(content: bytes) -> str:
    """Return the SHA-256 digest for immutable uploaded file bytes."""
    return sha256(content).hexdigest()
