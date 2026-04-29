import pytest

from app.services.storage_service import FileSystemStorageService, InMemoryStorageService


class TestInMemoryStorage:
    def test_put_returns_memory_uri(self):
        storage = InMemoryStorageService()

        uri = storage.put("documents/abc/policy.txt", b"content")

        assert uri == "memory://documents/abc/policy.txt"

    def test_get_returns_bytes_from_put(self):
        storage = InMemoryStorageService()
        uri = storage.put("k", b"hello world")

        assert storage.get(uri) == b"hello world"

    def test_get_unknown_uri_raises_key_error(self):
        storage = InMemoryStorageService()

        with pytest.raises(KeyError):
            storage.get("memory://does-not-exist")

    def test_put_overwrites_existing_key(self):
        storage = InMemoryStorageService()
        first_uri = storage.put("k", b"first")
        second_uri = storage.put("k", b"second")

        assert first_uri == second_uri
        assert storage.get(first_uri) == b"second"

    def test_put_preserves_byte_fidelity(self):
        storage = InMemoryStorageService()
        payload = bytes(range(256))

        uri = storage.put("binary", payload)

        assert storage.get(uri) == payload


class TestFileSystemStorage:
    def test_put_writes_file_and_returns_file_uri(self, tmp_path):
        storage = FileSystemStorageService(tmp_path)

        uri = storage.put("documents/abc/policy.txt", b"content")

        assert uri.startswith("file://")
        assert (tmp_path / "documents" / "abc" / "policy.txt").read_bytes() == b"content"

    def test_get_round_trips_through_filesystem(self, tmp_path):
        storage = FileSystemStorageService(tmp_path)
        uri = storage.put("k.txt", b"round trip")

        assert storage.get(uri) == b"round trip"

    def test_put_creates_intermediate_directories(self, tmp_path):
        storage = FileSystemStorageService(tmp_path)

        uri = storage.put("a/b/c/d.txt", b"deep")

        assert (tmp_path / "a" / "b" / "c" / "d.txt").read_bytes() == b"deep"
        assert storage.get(uri) == b"deep"

    def test_init_creates_root_when_missing(self, tmp_path):
        target = tmp_path / "fresh"
        assert not target.exists()

        FileSystemStorageService(target)

        assert target.is_dir()

    def test_put_rejects_absolute_keys(self, tmp_path):
        storage = FileSystemStorageService(tmp_path)

        with pytest.raises(ValueError, match="parent traversal|relative"):
            storage.put("/etc/passwd", b"nope")

    def test_get_rejects_non_file_uri_scheme(self, tmp_path):
        storage = FileSystemStorageService(tmp_path)

        with pytest.raises(ValueError, match="file://"):
            storage.get("memory://something")

        with pytest.raises(ValueError, match="file://"):
            storage.get("https://example.com/x")

    def test_root_can_be_constructed_from_string(self, tmp_path):
        storage = FileSystemStorageService(str(tmp_path))

        uri = storage.put("k", b"x")

        assert storage.get(uri) == b"x"
