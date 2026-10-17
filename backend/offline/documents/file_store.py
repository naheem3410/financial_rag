"""One door for saving and reading an uploaded file's bytes: `save` and
`read`. Today it is the local folder `data/uploads/`; swappable for S3 or
Azure Blob later (`FileStore`) without any caller changing. Step 3.2.

Files are saved by document id, with a sha256 fingerprint recorded on
every save so a repeat upload of the same bytes is recognized rather than
silently rewritten.
"""
from __future__ import annotations

import hashlib
import os
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import NamedTuple, Union

# Windows can briefly refuse a rename or an open onto/of a file another
# thread is touching at the same instant (a sharing violation, not a real
# permission problem) -- POSIX rename never fails this way, but os.replace
# on NTFS can. A rename is near-instantaneous, so a few short retries are
# enough to ride it out rather than surface a spurious failure.
_RETRY_ATTEMPTS = 50
_RETRY_DELAY_SECONDS = 0.01

DEFAULT_UPLOADS_DIR = Path(__file__).resolve().parents[2] / "data" / "uploads"


class FileStoreError(RuntimeError):
    """The store could not save a file (disk full, no permission, ...).
    Nothing is left half-written when this is raised."""


class SaveResult(NamedTuple):
    fingerprint: str
    already_existed: bool  # True: a repeat of the exact same bytes for this id


def fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FileStore(ABC):
    @abstractmethod
    def save(self, doc_id: str, data: bytes) -> SaveResult: ...

    @abstractmethod
    def read(self, doc_id: str) -> bytes: ...

    @abstractmethod
    def exists(self, doc_id: str) -> bool: ...

    @abstractmethod
    def delete(self, doc_id: str) -> None: ...


class LocalFileStore(FileStore):
    """Today's real store: a plain folder on disk."""

    def __init__(self, root: Union[str, Path] = DEFAULT_UPLOADS_DIR):
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, doc_id: str) -> Path:
        return self._root / doc_id

    def save(self, doc_id: str, data: bytes) -> SaveResult:
        digest = fingerprint(data)
        path = self._path(doc_id)
        if path.exists() and fingerprint(self._read_bytes_with_retry(path)) == digest:
            return SaveResult(fingerprint=digest, already_existed=True)

        # unique temp name per call: two writers never collide on the same
        # temp file, and a reader never sees a half-written real file either
        # way, since only the atomic rename below ever touches it.
        tmp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            tmp_path.write_bytes(data)
            self._replace_with_retry(tmp_path, path)
        except OSError as exc:
            tmp_path.unlink(missing_ok=True)
            raise FileStoreError(f"could not save {doc_id!r}: {exc}") from exc
        return SaveResult(fingerprint=digest, already_existed=False)

    @staticmethod
    def _replace_with_retry(tmp_path: Path, path: Path) -> None:
        for attempt in range(_RETRY_ATTEMPTS):
            try:
                os.replace(tmp_path, path)
                return
            except PermissionError:
                if attempt == _RETRY_ATTEMPTS - 1:
                    raise
                time.sleep(_RETRY_DELAY_SECONDS)

    def read(self, doc_id: str) -> bytes:
        path = self._path(doc_id)
        if not path.exists():
            raise FileNotFoundError(doc_id)
        return self._read_bytes_with_retry(path)

    @staticmethod
    def _read_bytes_with_retry(path: Path) -> bytes:
        for attempt in range(_RETRY_ATTEMPTS):
            try:
                return path.read_bytes()
            except PermissionError:
                if attempt == _RETRY_ATTEMPTS - 1:
                    raise
                time.sleep(_RETRY_DELAY_SECONDS)

    def exists(self, doc_id: str) -> bool:
        return self._path(doc_id).exists()

    def delete(self, doc_id: str) -> None:
        self._path(doc_id).unlink(missing_ok=True)


class InMemoryFileStore(FileStore):
    """A fake for tests -- proves callers only depend on the `FileStore`
    shape, not on files actually existing on disk."""

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    def save(self, doc_id: str, data: bytes) -> SaveResult:
        digest = fingerprint(data)
        if doc_id in self._data and fingerprint(self._data[doc_id]) == digest:
            return SaveResult(fingerprint=digest, already_existed=True)
        self._data[doc_id] = data
        return SaveResult(fingerprint=digest, already_existed=False)

    def read(self, doc_id: str) -> bytes:
        if doc_id not in self._data:
            raise FileNotFoundError(doc_id)
        return self._data[doc_id]

    def exists(self, doc_id: str) -> bool:
        return doc_id in self._data

    def delete(self, doc_id: str) -> None:
        self._data.pop(doc_id, None)
