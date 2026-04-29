import pytest

from app.services.storage_service import (
    FileSystemStorageService,
    InMemoryStorageService,
    safe_storage_key,
)


class TestSafeStorageKey:
    def test_passes_through_already_safe_filenames(self):
        assert safe_storage_key("v1", "policy.txt") == "documents/v1/policy.txt"
        assert (
            safe_storage_key("v1", "annual-report_2024.pdf")
            == "documents/v1/annual-report_2024.pdf"
        )

    def test_strips_path_components_to_basename(self):
        assert safe_storage_key("v1", "/etc/passwd") == "documents/v1/passwd"
        assert safe_storage_key("v1", "../../etc/passwd") == "documents/v1/passwd"
        assert safe_storage_key("v1", "foo/bar.txt") == "documents/v1/bar.txt"

    def test_strips_windows_path_components(self):
        assert safe_storage_key("v1", r"C:\Users\me\policy.txt") == "documents/v1/policy.txt"

    def test_replaces_control_and_null_bytes(self):
        assert safe_storage_key("v1", "a\x00b.txt") == "documents/v1/a_b.txt"
        assert safe_storage_key("v1", "x\ny.txt") == "documents/v1/x_y.txt"

    def test_replaces_shell_metacharacters(self):
        assert safe_storage_key("v1", "a;b|c.txt") == "documents/v1/a_b_c.txt"
        assert safe_storage_key("v1", "$(rm -rf).txt") == "documents/v1/__rm_-rf_.txt"

    def test_strips_leading_dots_to_block_dotfiles(self):
        assert safe_storage_key("v1", ".htaccess") == "documents/v1/htaccess"
        assert safe_storage_key("v1", "...config") == "documents/v1/config"

    def test_caps_long_filenames_at_200_chars(self):
        very_long = "a" * 1000 + ".txt"
        result = safe_storage_key("v1", very_long)

        assert result.startswith("documents/v1/")
        # The basename portion is capped at 200 chars.
        basename = result.removeprefix("documents/v1/")
        assert len(basename) == 200

    def test_falls_back_when_sanitization_yields_empty(self):
        assert safe_storage_key("v1", "") == "documents/v1/upload"
        assert safe_storage_key("v1", "...") == "documents/v1/upload"
        assert safe_storage_key("v1", "/") == "documents/v1/upload"

    def test_preserves_unicode_alphanumerics(self):
        # `.isalnum()` is unicode-aware, so accented letters round-trip cleanly.
        assert safe_storage_key("v1", "résumé.pdf") == "documents/v1/résumé.pdf"


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
