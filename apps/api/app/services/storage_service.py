from dataclasses import dataclass, field


@dataclass
class InMemoryStorageService:
    """Small object-store adapter for local tests and MVP demos."""

    objects: dict[str, bytes] = field(default_factory=dict)

    def put(self, key: str, content: bytes) -> str:
        """Store bytes and return a URI-like handle."""
        uri = f"memory://{key}"
        self.objects[uri] = content
        return uri

    def get(self, uri: str) -> bytes:
        """Load bytes previously stored under a memory URI."""
        return self.objects[uri]
