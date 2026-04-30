from app.services.hash_service import compute_sha256


def test_compute_sha256_is_stable_for_same_bytes():
    content = b"policy text"

    assert compute_sha256(content) == compute_sha256(content)


def test_compute_sha256_changes_when_bytes_change():
    assert compute_sha256(b"policy text") != compute_sha256(b"policy text ")


def test_empty_bytes_match_published_sha256_vector():
    # SHA-256 of empty input is a well-known constant — guards against accidental
    # algorithm or encoding changes (e.g., switching to a hex/base64 mix-up).
    assert compute_sha256(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


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


class TestStreamingHash:
    """Streaming overload accepts an iterable of byte chunks and must yield
    a digest byte-identical to the contiguous form."""

    def test_streaming_matches_bytes_for_single_byte_chunks(self):
        payload = b"streaming hash equivalence"
        chunks = [bytes([b]) for b in payload]

        assert compute_sha256(iter(chunks)) == compute_sha256(payload)

    def test_streaming_matches_bytes_for_one_mib_chunks(self):
        payload = b"x" * (3 * 1024 * 1024 + 7)  # uneven tail
        one_mib = 1024 * 1024
        chunks = [payload[i : i + one_mib] for i in range(0, len(payload), one_mib)]

        assert compute_sha256(iter(chunks)) == compute_sha256(payload)

    def test_streaming_matches_bytes_for_full_payload_chunk(self):
        payload = b"single chunk"

        assert compute_sha256(iter([payload])) == compute_sha256(payload)

    def test_streaming_empty_iterable_matches_published_vector(self):
        # The streaming path must also produce the published empty-input
        # SHA-256 vector — guards against accidental "feed at least one
        # chunk" assumptions in the implementation.
        assert (
            compute_sha256(iter([]))
            == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )

    def test_streaming_accepts_generator(self):
        payload = b"generator source"

        def gen():
            yield payload[:5]
            yield payload[5:]

        assert compute_sha256(gen()) == compute_sha256(payload)

    def test_streaming_accepts_bytearray_and_memoryview_contiguous(self):
        # bytes-like contiguous inputs route through the fast path; this
        # exercises both isinstance branches.
        payload = b"abc"
        assert compute_sha256(bytearray(payload)) == compute_sha256(payload)
        assert compute_sha256(memoryview(payload)) == compute_sha256(payload)
