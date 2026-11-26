"""Recover request-owned Git refs after an interrupted remote capture."""

import hashlib
import json
import os
from pathlib import Path
from queue import Empty, Queue
import re
import subprocess
from threading import Thread
import time
from uuid import uuid4

import git_network_lease
import git_support as git
import review_coordinator as coordination


SCHEMA = "ReleaseReviewSyncRefs/v1"
OID = re.compile(r"[a-f0-9]{40}(?:[a-f0-9]{24})?")
RECOVERY_TIMEOUT = 8.0


def _time_left(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("ReleaseReviewer Git ref 回收逾時。")
    return remaining


def _delete_direct_ref(repo, ref, sha, deadline):
    """Hold Git's transaction lock across the final ref type check."""
    _time_left(deadline)
    env = git.local_git_environment({"GIT_FLUSH": "1", "LC_ALL": "C"})
    # This is an internal scratch-ref transaction. A Repo hook could block while
    # the ref is locked, so this command uses only Git's own transaction checks.
    argv = ["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null", "update-ref", "--stdin"]
    replies = Queue()
    started = time.perf_counter()
    output_bytes = 0

    def read_replies(stream):
        try:
            for line in stream:
                replies.put(line.strip())
        finally:
            replies.put(None)

    def wait_reply(expected):
        nonlocal output_bytes
        response = replies.get(timeout=_time_left(deadline))
        output_bytes += len(response or b"")
        return response == expected

    try:
        with subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              env=env, bufsize=0) as child:
            reader = Thread(target=read_replies, args=(child.stdout,), daemon=True)
            reader.start()
            try:
                child.stdin.write(f"start\noption no-deref\ndelete {ref} {sha}\nprepare\n".encode("ascii"))
                if not wait_reply(b"start: ok") or not wait_reply(b"prepare: ok"):
                    return False
                status, _, _ = git.run_git_capture(repo, ["symbolic-ref", "--quiet", "--no-recurse", ref],
                                                   timeout=_time_left(deadline))
                action = b"commit" if status == 1 else b"abort"
                _time_left(deadline)
                child.stdin.write(action + b"\n")
                child.stdin.close()
                acknowledged = wait_reply(action + b": ok")
                return child.wait(timeout=_time_left(deadline)) == 0 and acknowledged and action == b"commit"
            finally:
                # EOF aborts an uncommitted transaction. Allow Git to remove its
                # lock, then reap a stuck process before releasing our owner lock.
                if not child.stdin.closed:
                    child.stdin.close()
                try:
                    child.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
                reader.join()
    except (Empty, OSError, subprocess.TimeoutExpired):
        return False
    finally:
        git._record_capture_git(["update-ref"], time.perf_counter() - started, output_bytes)


def journal_root():
    root = coordination.plain(git_network_lease.runtime_root() / "release-reviewer-refs").resolve()
    if any((parent / ".git").exists() for parent in (root, *root.parents)):
        raise git.ReviewContextError("ReleaseReviewer 的 Git ref 紀錄必須位於 Repo 外。")
    return root


class PrivateRefs:
    def __init__(self, repo, request_id=None, *, deadline=None):
        status, output, error = git.run_git_capture(repo, ["rev-parse", "--path-format=absolute", "--git-common-dir"],
                                                    timeout=_time_left(deadline) if deadline is not None else None)
        if status or not Path(output.strip()).is_absolute():
            raise git.ReviewContextError(f"無法核對 ReleaseReviewer 的 common Git 目錄：{error.strip()}")
        common = Path(output.strip()).resolve(strict=True)
        info = common.stat()
        if not common.is_dir() or not info.st_ino:
            raise git.ReviewContextError("無法確認 ReleaseReviewer 的 common Git 目錄身分。")
        self.repo = repo
        self.identity = [os.path.normcase(str(common)), info.st_dev, info.st_ino]
        key = hashlib.sha256(json.dumps(self.identity, separators=(",", ":")).encode()).hexdigest()[:24]
        self.directory = journal_root() / key
        self.scope_sha256 = coordination.digest([os.path.normcase(str(self.directory)), self.identity])
        self.identifier = uuid4().hex
        self.namespace = f"refs/release-reviewer-sync/{self.identifier}"
        self.path = self.directory / (self.identifier + ".json")
        self.owner = None
        self.record = {"schema": SCHEMA, "repository": self.identity, "request_id": request_id,
                       "fetching": False, "refs": {}}

    def _write(self, **fields):
        record = {**self.record, **fields}
        coordination.atomic(self.path, record)
        self.record = record

    def begin(self, refs):
        if self.owner is None:
            self.owner = coordination.FileLock(self.path.with_suffix(".lock")).acquire()
            seen = self._observe(self.namespace, supervised=False)
            if seen or self.path.exists():
                raise git.ReviewContextError("ReleaseReviewer 本次同步的暫存 ref 空間已被使用。")
        self._write(fetching=True, refs={**self.record["refs"], **dict.fromkeys(refs)})

    def settled(self):
        # The supervised fetch has returned, including release of its admission lease.
        self._write(fetching=False)

    def pin(self, refs):
        self._write(refs={**self.record["refs"], **refs})

    def _observe(self, namespace, *, supervised, deadline=None):
        args = ["for-each-ref", "--format=%(refname)%00%(objectname)%00%(symref)", namespace + "/"]
        status, output, error = git.run_git_capture(self.repo, args, supervised=supervised,
                                                    timeout=_time_left(deadline) if deadline is not None else None)
        if status:
            raise git.GitCommandError(args, status, error)
        return {ref: (sha, symbolic) for ref, sha, symbolic in (row.split("\0") for row in output.splitlines())}

    def _clean(self, path, record, deadline):
        refs = dict(record["refs"])
        namespace = f"refs/release-reviewer-sync/{path.stem}"
        # A dead Python owner alone cannot prove that its Git child has stopped.
        # Acquire the same supervised Git lease for the observation when that proof is missing.
        observed = self._observe(namespace, supervised=record["fetching"], deadline=deadline)
        for ref, expected in refs.items():
            sha, symbolic = observed.get(ref, ("", ""))
            if expected is None and not symbolic and OID.fullmatch(sha):
                refs[ref] = sha
        if record["fetching"] or refs != record["refs"]:
            # Persist the observed SHA before changing any refs. A conflict must
            # never let the next recovery adopt the competing writer's SHA.
            record = {**record, "fetching": False, "refs": refs}
            coordination.atomic(path, record)
        remaining = dict(refs)
        for ref, expected in refs.items():
            current = observed.get(ref)
            if current is None:
                # for-each-ref can omit a dangling symbolic ref.
                status, _, _ = git.run_git_capture(self.repo, ["symbolic-ref", "--quiet", "--no-recurse", ref],
                                                   timeout=_time_left(deadline))
                if status == 1:
                    del remaining[ref]
                continue
            sha, symbolic = current
            if symbolic or not OID.fullmatch(sha) or expected != sha:
                continue
            if _delete_direct_ref(self.repo, ref, expected, deadline):
                del remaining[ref]
        if remaining:
            if remaining != refs:
                coordination.atomic(path, {**record, "refs": remaining})
            return False
        path.unlink(missing_ok=True)
        return True

    def _valid(self, path, record):
        prefix = rf"refs/release-reviewer-sync/{path.stem}/[0-9]+"
        return (isinstance(record, dict) and record.get("schema") == SCHEMA and
                record.get("repository") == self.identity and type(record.get("fetching")) is bool and
                isinstance(record.get("refs"), dict) and all(
                    re.fullmatch(prefix, ref) and (sha is None or isinstance(sha, str) and OID.fullmatch(sha))
                    for ref, sha in record["refs"].items()))

    def _recover_journal(self, path, request_id, deadline, *, registered=False):
        _time_left(deadline)
        try:
            if registered:
                coordination.plain(path).lstat()
            elif request_id is not None:
                record = coordination.read_json(path)
                if isinstance(record, dict) and "request_id" in record and record["request_id"] != request_id:
                    return True
        except FileNotFoundError:
            return True

        lock = coordination.FileLock(path.with_suffix(".lock"))
        if not lock.acquire(blocking=False):
            return request_id is None
        try:
            try:
                record = coordination.read_json(path)
            except FileNotFoundError:
                return True
            if not isinstance(record, dict):
                return False
            if request_id is not None and record.get("request_id") != request_id:
                return not registered and "request_id" in record
            return self._valid(path, record) and self._clean(path, record, deadline)
        finally:
            self._release(lock, path)

    def recover_registered(self, request_id, journal, *, deadline):
        if not isinstance(journal, dict) or journal.get("scope_sha256") != self.scope_sha256:
            return False
        identifier = journal.get("id")
        if not isinstance(identifier, str) or not coordination.IDENTIFIER.fullmatch(identifier):
            return False
        return self._recover_journal(self.directory / (identifier + ".json"), request_id, deadline, registered=True)

    def recover(self, request_id=None, *, deadline=None):
        if not coordination.plain(self.directory).is_dir():
            return True
        if deadline is None:
            deadline = time.monotonic() + min(RECOVERY_TIMEOUT, git.GIT_TIMEOUT_SECONDS.get())
        clean = True
        for path in sorted(self.directory.glob("*.json")):
            if not coordination.IDENTIFIER.fullmatch(path.stem) or path == self.path:
                continue
            try:
                if not self._recover_journal(path, request_id, deadline):
                    clean = False
            except FileNotFoundError:
                continue
            except (OSError, ValueError, git.ReviewContextError):
                clean = False
        return clean

    @staticmethod
    def _release(lock, path):
        lock.release()
        try:
            path.lstat()
        except FileNotFoundError:
            try:
                lock.path.unlink(missing_ok=True)
            except OSError:
                pass  # A concurrent reaper can briefly retain the lock handle.
        except OSError:
            pass

    def close(self):
        if self.owner is None:
            return True
        try:
            deadline = time.monotonic() + git.GIT_TIMEOUT_SECONDS.get()
            return not self.record["refs"] or self._clean(self.path, self.record, deadline)
        except (OSError, ValueError, git.ReviewContextError):
            return False
        finally:
            self._release(self.owner, self.path)
            self.owner = None


def recover_request(repo, request_id, *, journal=None):
    if journal is None and not journal_root().is_dir():
        return True
    deadline = time.monotonic() + min(RECOVERY_TIMEOUT, git.GIT_TIMEOUT_SECONDS.get())
    refs = PrivateRefs(repo, deadline=deadline)
    if journal is not None:
        return refs.recover_registered(request_id, journal, deadline=deadline)
    return refs.recover(request_id, deadline=deadline)
