from app.services.hash_service import compute_sha256


def test_compute_sha256_is_stable_for_same_bytes():
    content = b"policy text"

    assert compute_sha256(content) == compute_sha256(content)


def test_compute_sha256_changes_when_bytes_change():
    assert compute_sha256(b"policy text") != compute_sha256(b"policy text ")
