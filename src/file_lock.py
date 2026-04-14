import os
from pathlib import Path
from typing import BinaryIO

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class PosixLockBackend:
    def acquire(self, handle: BinaryIO) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)

    def release(self, handle: BinaryIO) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class WindowsLockBackend:
    _LOCK_REGION_SIZE = 1

    def acquire(self, handle: BinaryIO) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, self._LOCK_REGION_SIZE)

    def release(self, handle: BinaryIO) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, self._LOCK_REGION_SIZE)


_BACKEND = WindowsLockBackend() if os.name == "nt" else PosixLockBackend()


class FileLock:
    def __init__(self, lock_path: Path):
        self._lock_path = lock_path
        self._handle: BinaryIO | None = None

    def __enter__(self):
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(self._lock_path, "a+b")
        _BACKEND.acquire(self._handle)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._handle is None:
            return False
        _BACKEND.release(self._handle)
        self._handle.close()
        self._handle = None
        return False


def file_lock(lock_path: Path) -> FileLock:
    return FileLock(lock_path)
