import os
from pathlib import Path

import pytest

from src import file_lock, utils


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only lock backend test")
def test_posix_backend_uses_flock_for_acquire_and_release(monkeypatch, tmp_path: Path):
    calls = []

    def _fake_flock(fd: int, operation: int):
        calls.append((fd, operation))

    monkeypatch.setattr(file_lock.fcntl, "flock", _fake_flock)
    lock_file = tmp_path / "state.lock"
    backend = file_lock.PosixLockBackend()

    with open(lock_file, "a+b") as handle:
        backend.acquire(handle)
        backend.release(handle)
        expected_fd = handle.fileno()

    assert calls == [
        (expected_fd, file_lock.fcntl.LOCK_EX),
        (expected_fd, file_lock.fcntl.LOCK_UN),
    ]


@pytest.mark.skipif(os.name != "nt", reason="Windows-only lock backend test")
def test_windows_backend_uses_msvcrt_locking_for_acquire_and_release(monkeypatch, tmp_path: Path):
    calls = []

    def _fake_locking(fd: int, operation: int, size: int):
        calls.append((fd, operation, size))

    monkeypatch.setattr(file_lock.msvcrt, "locking", _fake_locking)
    lock_file = tmp_path / "state.lock"
    backend = file_lock.WindowsLockBackend()

    with open(lock_file, "a+b") as handle:
        backend.acquire(handle)
        backend.release(handle)
        expected_fd = handle.fileno()

    assert calls == [
        (expected_fd, file_lock.msvcrt.LK_LOCK, 1),
        (expected_fd, file_lock.msvcrt.LK_UNLCK, 1),
    ]


def test_utils_file_lock_acquires_and_releases_with_active_backend(monkeypatch, tmp_path: Path):
    class _FakeBackend:
        def __init__(self):
            self.acquire_calls = 0
            self.release_calls = 0
            self.handle_closed_after_release = None

        def acquire(self, handle):
            self.acquire_calls += 1
            assert handle.closed is False

        def release(self, handle):
            self.release_calls += 1
            self.handle_closed_after_release = handle.closed

    fake_backend = _FakeBackend()
    monkeypatch.setattr(file_lock, "_BACKEND", fake_backend)

    lock_path = tmp_path / "nested" / "runtime_state.lock"
    with utils._file_lock(lock_path):
        assert lock_path.parent.exists()

    assert fake_backend.acquire_calls == 1
    assert fake_backend.release_calls == 1
    assert fake_backend.handle_closed_after_release is False
