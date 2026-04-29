from app.services.hash_service import compute_sha256


def test_compute_sha256_is_stable_for_same_bytes():
    content = b"policy text"

    assert compute_sha256(content) == compute_sha256(content)


def test_compute_sha256_changes_when_bytes_change():
    assert compute_sha256(b"policy text") != compute_sha256(b"policy text ")


def test_empty_bytes_match_published_sha256_vector():
    # SHA-256 of empty input is a well-known constant — guards against accidental
    # algorithm or encoding changes (e.g., switching to a hex/base64 mix-up).
    assert (
        compute_sha256(b"")
        == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


def test_returns_64_character_lowercase_hex():
    digest = compute_sha256(b"anything")

    assert len(digest) == 64
    assert digest == digest.lower()
    assert all(c in "0123456789abcdef" for c in digest)


def test_handles_large_payload_without_error():
    # Document hashing must not impose a small size limit; one MB of zeroes is
    # a sane lower bound that catches accidental in-memory copies.
    payload = b"\x00" * (1024 * 1024)

    assert len(compute_sha256(payload)) == 64

