from collections.abc import Iterable
from hashlib import sha256


def compute_sha256(content: bytes | bytearray | memoryview | Iterable[bytes]) -> str:
    """Return the SHA-256 digest for uploaded file content.

    Accepts either a contiguous bytes-like object or an iterable of byte
    chunks. The streaming overload lets callers feed the hasher chunk by
    chunk without ever materialising the full payload — for the same input,
    both forms produce byte-identical digests (the hash is the same function
    either way).
    """
    digest = sha256()
    if isinstance(content, (bytes, bytearray, memoryview)):
        digest.update(content)
    else:
        for chunk in content:
            digest.update(chunk)
    return digest.hexdigest()
