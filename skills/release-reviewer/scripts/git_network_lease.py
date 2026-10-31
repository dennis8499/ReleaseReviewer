"""Cross-language, fail-closed local admission for Git network process trees.

The directory protocol is shared with GitLabWorkspace. No age/PID based reclaim
is permitted: an interrupted owner requires explicit operator investigation.
"""
from __future__ import annotations

import base64
import errno
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import signal
import subprocess
import sys
import time
import uuid

SCHEMA = "GitLabWorkspaceLease/v1"


class LeaseError(OSError):
    pass


def extended_windows_path(path):
    """Return a Win32 path that remains usable beyond the legacy MAX_PATH limit."""
    value = os.fspath(path)
    if os.name != "nt" or not isinstance(value, str) or value.startswith("\\\\?\\"):
        return value
    value = os.path.abspath(value)
    units = len(value.encode("utf-16-le", errors="surrogatepass")) // 2
    if units < 248:
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


def _sharing_retry(operation):
    """Bound Windows reader/replace sharing races without reclaiming ownership."""
    deadline = time.monotonic() + 2
    while True:
        try:
            return operation()
        except OSError as exc:
            sharing = exc.errno in (errno.EACCES, errno.EPERM, errno.EBUSY) or getattr(exc, "winerror", None) in (5, 32, 33)
            if not sharing or time.monotonic() >= deadline:
                raise
            time.sleep(.005 + random.random() * .005)


def runtime_root():
    override = os.environ.get("GITLAB_WORKSPACE_RUNTIME_DIR")
    if override:
        return Path(override).expanduser().absolute()
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") / "GitLabWorkspace/runtime-v1"
    base = Path(os.environ["XDG_RUNTIME_DIR"]) / "gitlab-workspace" if os.environ.get("XDG_RUNTIME_DIR") else Path.home() / ".cache/gitlab-workspace"
    return base / "runtime-v1"


def _atomic_json(path, value):
    temporary = path.with_name("." + path.name + "-" + uuid.uuid4().hex)
    try:
        with open(extended_windows_path(temporary), "x", encoding="utf-8") as stream:
            json.dump(value, stream, separators=(",", ":"))
            stream.write("\n")
        _sharing_retry(lambda: os.replace(extended_windows_path(temporary), extended_windows_path(path)))
    finally:
        try:
            os.unlink(extended_windows_path(temporary))
        except FileNotFoundError:
            pass


def _plain_directory(path):
    import stat
    path = Path(path)
    try:
        info = os.lstat(extended_windows_path(path))
    except FileNotFoundError:
        return
    if (stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
        raise LeaseError(f"Lease directory cannot be a link: {path}")


class DirectoryLease:
    def __init__(self, root=None, *, name="git-network.lock", kind="git", timeout=180.0, cancelled=None):
        self.root = Path(root) if root is not None else runtime_root()
        self.path = self.root / name
        self.timeout, self.cancelled = timeout, cancelled
        self.owner = {"schema": SCHEMA, "nonce": uuid.uuid4().hex, "pid": os.getpid(),
                      "createdAt": datetime.now(timezone.utc).isoformat(), "kind": kind,
                      "state": "reserved", "childPid": None, "childStartedAt": None,
                      "processTreeConfirmed": False}
        self.acquired = False
        self.wait_seconds = 0.0

    def acquire(self):
        if self.timeout <= 0:
            raise ValueError("Lease timeout must be positive")
        self.root.mkdir(parents=True, exist_ok=True)
        for parent in (self.root, *self.root.parents):
            _plain_directory(parent)
        wait_started = time.monotonic()
        deadline = wait_started + self.timeout
        while True:
            if self.cancelled and self.cancelled():
                raise LeaseError("Git network admission cancelled")
            _plain_directory(self.path)
            try:
                self.path.mkdir()
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise LeaseError(f"Git admission timed out; existing lease is preserved: {self.path}")
                time.sleep(min(0.1 + random.random() * 0.025, max(0, deadline - time.monotonic())))
                continue
            self.acquired = True
            self.wait_seconds = time.monotonic() - wait_started
            try:
                _atomic_json(self.path / "owner.json", self.owner)
            except BaseException:
                # A partial reservation is ambiguous to other runtimes.
                self.acquired = False
                raise
            return self

    def update(self, **values):
        self._verify()
        self.owner.update(values)
        _atomic_json(self.path / "owner.json", self.owner)

    def _verify(self):
        _plain_directory(self.path)
        marker = self.path / "owner.json"
        if marker.is_symlink():
            raise LeaseError("Lease owner cannot be a link")
        value = json.loads(_sharing_retry(lambda: marker.read_text(encoding="utf-8")))
        if not self.acquired or value.get("schema") != SCHEMA or value.get("nonce") != self.owner["nonce"]:
            raise LeaseError(f"Lease ownership changed; preserved: {self.path}")

    def release(self, *, process_tree_confirmed=False):
        self._verify()
        if self.owner["state"] != "reserved" and not process_tree_confirmed:
            self.update(state="quarantined", processTreeConfirmed=False)
            raise LeaseError(f"Git process tree is uncertain; lease quarantined: {self.path}")
        self.update(processTreeConfirmed=bool(process_tree_confirmed))
        _sharing_retry(lambda: (self.path / "owner.json").unlink())
        _sharing_retry(self.path.rmdir)
        self.acquired = False


def _windows_job():
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong), ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]
    class EXTENDED(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO), ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
    class ACCOUNT(ctypes.Structure):
        _fields_ = [("TotalUserTime", ctypes.c_longlong), ("TotalKernelTime", ctypes.c_longlong), ("ThisPeriodTotalUserTime", ctypes.c_longlong), ("ThisPeriodTotalKernelTime", ctypes.c_longlong), ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD), ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel.QueryInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.CreateJobObjectW(None, None)
    if not handle:
        raise LeaseError("Cannot create Git process-tree job")
    info = EXTENDED()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
        kernel.CloseHandle(handle)
        raise LeaseError("Cannot configure Git process-tree job")
    def active():
        accounting = ACCOUNT()
        if not kernel.QueryInformationJobObject(handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
            raise LeaseError("Cannot confirm Git process-tree termination")
        return accounting.ActiveProcesses
    return kernel, handle, active


def run(command, *, input=None, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, env=None, timeout=180.0):
    """Run a network command under one shared slot; preserve ambiguous crashes.

    The supervisor waits for a stdin handshake before starting Git. On Windows
    it is assigned to a kill-on-close Job before that handshake, closing the
    spawn/child-assignment race for Git and all of its descendants.
    """
    lease = DirectoryLease(timeout=timeout).acquire()
    process = None
    job = None
    confirmed = False
    try:
        lease.update(state="starting")
        if os.name == "nt":
            job = _windows_job()
        process = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()), "--guard"],
                                   stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                                   env=env, start_new_session=os.name != "nt",
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0)
        if job and not job[0].AssignProcessToJobObject(job[1], int(process._handle)):
            process.kill()
            process.communicate()
            confirmed = True  # Handshake was never sent; no Git child exists.
            raise LeaseError("Cannot assign Git supervisor to process-tree job")
        lease.update(state="running", childPid=process.pid,
                     childStartedAt=datetime.now(timezone.utc).isoformat())
        specification = json.dumps({"command": list(command), "input": base64.b64encode(input).decode() if input is not None else None}).encode()
        try:
            output, error = process.communicate(specification, timeout=timeout)
        except BaseException:
            if job:
                job[0].TerminateJobObject(job[1], 1)
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5)
            raise
        finally:
            if process.poll() is not None:
                deadline = time.monotonic() + 2
                while True:
                    if job:
                        remaining = job[2]()
                    else:
                        try:
                            os.killpg(process.pid, 0)
                            remaining = 1
                        except ProcessLookupError:
                            remaining = 0
                    if not remaining:
                        confirmed = True
                        break
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.02)
        result = subprocess.CompletedProcess(command, process.returncode, output, error)
        result.network_lock_wait_seconds = lease.wait_seconds
        if check:
            result.check_returncode()
        return result
    finally:
        if process is None:
            confirmed = True  # Failure before spawn is safe to release.
        if job:
            job[0].CloseHandle(job[1])
        if confirmed:
            lease.release(process_tree_confirmed=True)
        elif lease.acquired:
            lease.update(state="quarantined", processTreeConfirmed=False)
            raise LeaseError(f"Git process tree uncertain; admission remains quarantined: {lease.path}")


def _guard():
    raw = sys.stdin.buffer.read()
    if not raw:
        return 2
    specification = json.loads(raw)
    data = base64.b64decode(specification["input"]) if specification.get("input") is not None else None
    result = subprocess.run(specification["command"], input=data, stdout=sys.stdout.buffer, stderr=sys.stderr.buffer, check=False)
    return result.returncode


if __name__ == "__main__" and sys.argv[1:] == ["--guard"]:
    raise SystemExit(_guard())
