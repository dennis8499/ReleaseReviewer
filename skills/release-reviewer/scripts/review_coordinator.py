"""Local coordination for opt-in review sessions; no daemon or network client.

Only the existing Git runner may use the network. Cache entries are accelerators,
never a replacement for verification of the owned evidence copied into a session.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

import git_network_lease

ACTIVE = ContextVar("review_coordination", default=None)
RUNTIME_SCHEMA = "ReleaseReviewRuntime/v1"
IDENTIFIER = re.compile(r"^[a-f0-9]{32}$")
DIGEST = re.compile(r"^[a-f0-9]{64}$")
LOCK_METRICS = ContextVar("release_review_lock_metrics", default=None)


@contextmanager
def measure_lock_wait():
    metrics = {"local_lock_wait_seconds": 0.0}
    token = LOCK_METRICS.set(metrics)
    try:
        yield metrics
    finally:
        LOCK_METRICS.reset(token)


def record_lock_wait(seconds):
    metrics = LOCK_METRICS.get()
    if metrics is not None:
        metrics["local_lock_wait_seconds"] += max(0.0, float(seconds))


class Cancelled(OSError):
    pass


def plain(path):
    path = Path(path)
    for candidate in (path, *path.parents):
        git_network_lease._plain_directory(candidate)
    return path


def atomic(path, value):
    plain(path.parent)
    git_network_lease._atomic_json(path, value)


def read_json(path):
    plain(path)
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


class FileLock:
    """OS-owned local lock; process death releases it without guessing PID/age."""

    def __init__(self, path, timeout=180, cancelled=None):
        self.path, self.timeout = Path(path), timeout
        self.cancelled, self.stream = cancelled, None
        self.wait_seconds = 0.0

    def acquire(self, blocking=True):
        plain(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        stream.seek(0, 2)
        if not stream.tell():
            stream.write(b"\0")
            stream.flush()
        wait_started = time.monotonic()
        deadline = wait_started + self.timeout
        while True:
            if self.cancelled and self.cancelled():
                stream.close()
                raise Cancelled("Review cancelled before the local operation.")
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.stream = stream
                self.wait_seconds = time.monotonic() - wait_started
                record_lock_wait(self.wait_seconds)
                return self
            except OSError as exc:
                if not blocking:
                    stream.close()
                    return None
                if time.monotonic() >= deadline:
                    stream.close()
                    raise TimeoutError(f"Local review operation is busy: {self.path}") from exc
                time.sleep(.04)

    def release(self):
        if self.stream:
            stream, self.stream = self.stream, None
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_UN)
            finally:
                stream.close()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_):
        self.release()


def runtime(root, *, create=False):
    root = plain(Path(root).expanduser().absolute()).resolve()
    marker = root / ".release-reviewer-runtime.json"
    if create:
        if any((parent / ".git").exists() for parent in (root, *root.parents)):
            raise ValueError("Review runtime must be outside every Git Repo.")
        root.mkdir(parents=True, exist_ok=True)
        # The first simultaneous starts must never observe a partial marker.
        with FileLock(root / ".ownership.lock"):
            if not marker.exists():
                if any(item.name != ".ownership.lock" for item in root.iterdir()):
                    raise ValueError(f"Review runtime is not owned: {root}")
                atomic(marker, {"schema": RUNTIME_SCHEMA, "directory": str(root)})
    value = read_json(marker)
    if value != {"schema": RUNTIME_SCHEMA, "directory": str(root)}:
        raise ValueError("Invalid review runtime ownership.")
    return root


def safe_error(error):
    text = str(error)
    text = re.sub(r"(https?://)[^/\s@]+@", r"\1<redacted>@", text)
    return re.sub(r"(?i)(authorization|private-token|access_token|password)([=: ]+)\S+",
                  r"\1\2<redacted>", text)


class Coordination:
    def __init__(self, root, identifier, notify=None, refresh=False, auth_scope=""):
        self.root, self.identifier, self.notify = Path(root), identifier, notify
        self.refresh, self.auth_scope = refresh, auth_scope

    def cancelled(self):
        return (self.root / "jobs" / (self.identifier + ".cancel")).exists()

    def check(self):
        if self.cancelled():
            raise Cancelled("Release review cancelled.")

    def phase(self, value):
        self.check()
        if self.notify:
            self.notify(value, {})

    @contextmanager
    def activate(self):
        token = ACTIVE.set(self)
        try:
            yield self
        finally:
            ACTIVE.reset(token)

