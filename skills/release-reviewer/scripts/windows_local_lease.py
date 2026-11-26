"""Windows adapter for the shared local lease and process supervisor."""
import atexit
import base64
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import uuid


class WindowsLeaseError(OSError):
    def __init__(self, code, native_code=None, *, message=None):
        self.code = code
        self.native_code = native_code
        message = message or code
        super().__init__(message if native_code is None else f"{message} (Win32 {native_code})")


LAUNCHER = Path(__file__).with_name("windows-local-lease.ps1")
COMMAND = ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
           "-File", str(LAUNCHER)]
CREATION_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)
HELLO = "GitLabWorkspaceWindowsLease/v2"
SUPERVISOR_TIMEOUT = 0xE0470001


class Broker:
    def __init__(self):
        self.process = subprocess.Popen(COMMAND, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, creationflags=CREATION_FLAGS)
        self.responses = queue.Queue()

        def receive():
            try:
                for line in self.process.stdout:
                    self.responses.put(line)
            finally:
                self.responses.put(None)

        threading.Thread(target=receive, daemon=True).start()
        try:
            if self.line(8).strip() != HELLO:
                raise WindowsLeaseError("Invalid Windows lease helper startup.")
        except BaseException:
            self.close()
            raise

    def line(self, timeout):
        try:
            line = self.responses.get(timeout=timeout)
        except queue.Empty as error:
            raise ConnectionError("Windows lease helper did not respond.") from error
        if line is None:
            raise ConnectionError("Windows lease helper disconnected.")
        return line.decode("utf-8")

    def request(self, request):
        self.process.stdin.write(json.dumps(request).encode("utf-8") + b"\n")
        self.process.stdin.flush()
        response = json.loads(self.line(5))
        if response.get("ok") is not True:
            code = response.get("code")
            raise WindowsLeaseError(code, response.get("nativeCode"))
        return response["data"]

    def close(self):
        try:
            self.process.stdin.close()
            self.process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            self.process.kill()
            self.process.wait()
        finally:
            self.process.stdout.close()


_lock = threading.Lock()
_broker = None


def close_broker():
    global _broker
    if _broker is not None:
        client, _broker = _broker, None
        client.close()


atexit.register(close_broker)


def broker_request(request, *, fallback=False):
    global _broker
    with _lock:
        for _ in range(2):
            if _broker is None:
                try:
                    _broker = Broker()
                except OSError:
                    if fallback:
                        return None
                    raise
            try:
                return _broker.request(request)
            except WindowsLeaseError:
                raise
            except (ConnectionError, BrokenPipeError, json.JSONDecodeError):
                close_broker()
                # The same nonce is essential when a claim committed before
                # the helper's reply was lost.
        raise WindowsLeaseError("Windows lease helper disconnected.")


def transient(error):
    return error.code == "busy" or error.native_code in (32, 33)


def acquire(directory, timeout, cancelled):
    deadline = time.monotonic() + timeout
    if cancelled and cancelled():
        raise WindowsLeaseError("Git network admission cancelled.")
    owner = broker_request({"action": "probe", "pid": os.getpid()}, fallback=True)
    if owner is None or not owner.get("birth"):
        return None
    request = {"action": "claim", "kind": "git", "directory": str(directory),
               "nonce": str(uuid.uuid4()), "pid": os.getpid(), "birth": owner["birth"]}
    directory.parent.mkdir(parents=True, exist_ok=True)
    while True:
        if cancelled and cancelled():
            raise WindowsLeaseError("Git network admission cancelled.")
        try:
            data = broker_request(request)
            token = {key: value for key, value in request.items() if key != "action"}
            token["directoryId"] = data["directoryId"]
            if cancelled and cancelled():
                release(token)
                raise WindowsLeaseError("Git network admission cancelled.")
            return token
        except WindowsLeaseError as error:
            if not (transient(error) or error.code == "metadata-missing"):
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WindowsLeaseError("queue-timeout", error.native_code,
                                        message=f"Git admission timed out ({error.code}).") from error
            time.sleep(min(0.05, remaining))


def release(lease):
    deadline = time.monotonic() + 2
    while True:
        try:
            broker_request({**lease, "action": "release"})
            return
        except WindowsLeaseError as error:
            if not (transient(error) or error.native_code == 5) or time.monotonic() >= deadline:
                raise
            time.sleep(.01)


def run(directory, command, *, input, stdout, stderr, check, env, timeout, cancelled=None, observer=None):
    try:
        lease = acquire(directory, timeout, cancelled)
    except WindowsLeaseError as error:
        error.phase = "admission"
        raise
    if lease is None:
        return None  # Startup failed before the first claim.
    try:
        if observer:
            observer("fetch")
        specification = {"executable": str(command[0]), "args": [str(value) for value in command[1:]],
                         "leases": [lease], "timeoutMs": max(1, int(timeout * 1000)), "cancel": "stdin"}
        if input is not None:
            # Preserve Git's byte-oriented stdin across PowerShell's UTF-8 protocol.
            specification["input"] = base64.b64encode(input).decode("ascii")
        with subprocess.Popen([*COMMAND, "-Run"], stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                              env=env, creationflags=CREATION_FLAGS) as process:
            outputs = [None, None]
            readers = []
            for index, stream in enumerate((process.stdout, process.stderr)):
                if stream is None:
                    continue

                def read(current=stream, position=index):
                    outputs[position] = current.read()

                reader = threading.Thread(target=read)
                readers.append(reader)
                reader.start()
            aborted = False
            deadline = time.monotonic() + timeout + 8
            timed_out = False
            try:
                process.stdin.write(json.dumps(specification).encode("utf-8") + b"\n")
                process.stdin.flush()
                while process.poll() is None:
                    try:
                        process.wait(timeout=.05)
                    except subprocess.TimeoutExpired:
                        if cancelled and cancelled():
                            aborted = True
                            break
                        if time.monotonic() >= deadline:
                            timed_out = True
                            break
            finally:
                if process.poll() is None:
                    try:
                        process.stdin.write(b"cancel\n")
                        process.stdin.flush()
                    except OSError:
                        pass
                    try:
                        process.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                for reader in readers:
                    reader.join()
            if aborted:
                raise WindowsLeaseError("Git network operation cancelled.")
            if timed_out or process.returncode & 0xFFFFFFFF == SUPERVISOR_TIMEOUT:
                raise subprocess.TimeoutExpired(command, timeout, outputs[0], outputs[1])
            result = subprocess.CompletedProcess(command, process.returncode, *outputs)
            if check:
                result.check_returncode()
            return result
    finally:
        try:
            release(lease)
        except WindowsLeaseError as error:
            error.phase = "cleanup"
            raise
