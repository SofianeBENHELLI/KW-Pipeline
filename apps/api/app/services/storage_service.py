from dataclasses import dataclass, field


@dataclass
class InMemoryStorageService:
    objects: dict[str, bytes] = field(default_factory=dict)

    def put(self, key: str, content: bytes) -> str:
        uri = f"memory://{key}"
        self.objects[uri] = content
        return uri

    def get(self, uri: str) -> bytes:
        return self.objects[uri]
