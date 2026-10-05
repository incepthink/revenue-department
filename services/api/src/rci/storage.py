"""Blob storage: local filesystem (tests), Azurite (local-first dev) or Azure Blob (deployment).

Containers:
  raw    original uploads (restricted — contain PII)
  pages  rasterised page images (restricted — contain PII)
  ocr    un-redacted OCR lines + bboxes (restricted)
  sources  rule notification / gazette texts uploaded by admins
  out    exports
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from rci.config import Settings, get_settings

CONTAINERS = ("raw", "pages", "ocr", "sources", "out")


class BlobStore(Protocol):
    def put(self, container: str, key: str, data: bytes, content_type: str = "application/octet-stream") -> str: ...
    def get(self, container: str, key: str) -> bytes: ...
    def exists(self, container: str, key: str) -> bool: ...


class LocalBlobStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, container: str, key: str) -> Path:
        if container not in CONTAINERS:
            raise ValueError(f"unknown container {container}")
        p = (self.root / container / key).resolve()
        if self.root.resolve() not in p.parents:
            raise ValueError("blob key escapes storage root")
        return p

    def put(self, container: str, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        p = self._path(container, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)
        return key

    def get(self, container: str, key: str) -> bytes:
        return self._path(container, key).read_bytes()

    def exists(self, container: str, key: str) -> bool:
        return self._path(container, key).exists()


class AzureBlobStore:
    def __init__(self, connection_string: str, prefix: str):
        from azure.storage.blob import BlobServiceClient

        self.client = BlobServiceClient.from_connection_string(connection_string)
        self.prefix = prefix
        self._ready: set[str] = set()

    def _container(self, container: str):
        if container not in CONTAINERS:
            raise ValueError(f"unknown container {container}")
        name = f"{self.prefix}-{container}"
        cc = self.client.get_container_client(name)
        if name not in self._ready:
            from azure.core.exceptions import ResourceExistsError

            try:
                cc.create_container()
            except ResourceExistsError:
                pass
            self._ready.add(name)
        return cc

    def put(self, container: str, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        from azure.storage.blob import ContentSettings

        self._container(container).upload_blob(
            key, data, overwrite=True, content_settings=ContentSettings(content_type=content_type)
        )
        return key

    def get(self, container: str, key: str) -> bytes:
        return self._container(container).download_blob(key).readall()

    def exists(self, container: str, key: str) -> bool:
        return self._container(container).get_blob_client(key).exists()


def get_blob_store(settings: Settings | None = None) -> BlobStore:
    s = settings or get_settings()
    if s.storage_backend == "local":
        return LocalBlobStore(s.storage_local_root)
    return AzureBlobStore(s.azure_storage_connection_string, s.blob_container_prefix)
