#!/usr/bin/env python3
"""Own, validate, and clean temporary Release Reviewer evidence sessions."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
import uuid

sys.dont_write_bytecode = True
import git_network_lease

OWNER_FILE = ".release-reviewer-owned.json"
OWNER_SCHEMA = "release-reviewer-session/v1"
TOKEN_PATTERN = re.compile(r"^[a-f0-9]{32}$")


class SessionError(OSError):
    """An owned temporary session could not be safely created or removed."""


def temp_root() -> Path:
    return Path(tempfile.gettempdir()).resolve(strict=True)


def _is_link(path: Path) -> bool:
    try:
        info = os.lstat(git_network_lease.extended_windows_path(path))
    except FileNotFoundError:
        return False
    return (stat.S_ISLNK(info.st_mode)
            or bool(getattr(info, "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)))


def _path_in_temp(path: Path, root: Path) -> Path:
    lexical = Path(os.path.abspath(path))
    try:
        resolved = lexical.resolve(strict=True)
        is_root = os.path.samefile(resolved, root)
    except OSError as exc:
        raise SessionError(f"審查暫存目錄必須位於系統暫存目錄內：{path}") from exc
    if is_root:
        raise SessionError(f"拒絕以系統暫存目錄本身作為審查暫存：{path}")

    probe = lexical
    while True:
        if _is_link(probe):
            raise SessionError(f"審查暫存路徑不能包含符號連結或 Junction：{probe}")
        try:
            reaches_root = os.path.samefile(probe, root)
        except OSError as exc:
            raise SessionError(f"審查暫存目錄必須位於系統暫存目錄內：{path}") from exc
        if reaches_root:
            break
        if probe.parent == probe:
            raise SessionError(f"審查暫存目錄必須位於系統暫存目錄內：{path}")
        probe = probe.parent
    return resolved


def create_session(requested: str | Path | None = None, *, runtime_root=None, request_id=None) -> Path:
    """Create one fresh temporary evidence directory with a private owner marker."""
    if runtime_root is not None:
        import review_coordinator as coordination
        root = coordination.runtime(runtime_root)
        if not coordination.IDENTIFIER.fullmatch(request_id or ""):
            raise SessionError("Invalid review request ID.")
        path = root / "sessions" / request_id
        coordination.plain(path)
        path.mkdir(parents=True)
        marker = {"schema": "release-reviewer-session/v2", "directory": str(path.resolve()),
                  "runtime": str(root), "request_id": request_id, "token": uuid.uuid4().hex}
        coordination.atomic(path / OWNER_FILE, marker)
        return path.resolve()
    root = temp_root()
    if requested is None:
        path = Path(tempfile.mkdtemp(prefix="release-reviewer-session-", dir=root))
    else:
        candidate = Path(requested).expanduser().absolute()
        if candidate.exists() or candidate.is_symlink():
            raise SessionError(f"審查暫存目錄必須是全新路徑：{candidate}")
        try:
            if candidate.parent.resolve(strict=True) != root:
                _path_in_temp(candidate.parent, root)
        except (OSError, ValueError) as exc:
            raise SessionError(f"--context-dir 必須位於系統暫存目錄內：{candidate}") from exc
        candidate.mkdir()
        path = candidate

    resolved = _path_in_temp(path, root)
    token = uuid.uuid4().hex
    marker = {"schema": OWNER_SCHEMA, "directory": str(resolved), "token": token}
    try:
        with (resolved / OWNER_FILE).open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(marker, stream, ensure_ascii=True, separators=(",", ":"))
            stream.write("\n")
    except BaseException:
        shutil.rmtree(resolved, ignore_errors=True)
        raise
    return resolved


def _owned_session(requested: str | Path) -> Path | None:
    raw = Path(requested).expanduser().absolute()
    if not raw.exists() and not raw.is_symlink():
        return None
    # v2 contexts live outside Git roots in a marked workspace runtime. v1 keeps
    # its historical system-temp boundary and cannot opt out via a forged path.
    if not _is_link(raw) and not _is_link(raw / OWNER_FILE):
        try:
            early = json.loads((raw / OWNER_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            early = {}
        if isinstance(early, dict) and early.get("schema") == "release-reviewer-session/v2":
            import review_coordinator as coordination
            root = coordination.runtime(early.get("runtime", ""))
            identifier = early.get("request_id", "")
            resolved = coordination.plain(raw).resolve(strict=True)
            if (not isinstance(identifier, str) or not coordination.IDENTIFIER.fullmatch(identifier)
                    or resolved != root / "sessions" / identifier
                    or early.get("directory") != str(resolved)
                    or not isinstance(early.get("token"), str)
                    or not TOKEN_PATTERN.fullmatch(early["token"])):
                raise SessionError("Review session ownership differs.")
            return resolved
    root = temp_root()
    resolved = _path_in_temp(raw, root)
    marker_path = resolved / OWNER_FILE
    if _is_link(marker_path) or not marker_path.is_file():
        raise SessionError(f"審查暫存目錄缺少有效的所有權標記，未刪除任何檔案：{resolved}")
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SessionError(f"無法讀取審查暫存目錄的所有權標記，未刪除任何檔案：{resolved}") from exc
    if (not isinstance(marker, dict) or marker.get("schema") != OWNER_SCHEMA
            or marker.get("directory") != str(resolved)
            or not isinstance(marker.get("token"), str)
            or not TOKEN_PATTERN.fullmatch(marker["token"])):
        raise SessionError(f"審查暫存目錄的所有權標記不符，未刪除任何檔案：{resolved}")
    return resolved


def _remove_tree(path: Path) -> None:
    path = Path(path)

    def path_units(value: Path) -> int:
        return len(str(value).encode("utf-16-le", errors="surrogatepass")) // 2

    def needs_extended(value: Path) -> bool:
        if path_units(value) >= 248:
            return True
        target = git_network_lease.extended_windows_path(value)
        if _is_link(value) or not os.path.isdir(target):
            return False
        with os.scandir(target) as entries:
            names = [entry.name for entry in entries]
        return any(needs_extended(value / name) for name in names)

    def make_writable(function, target: str, _error) -> None:
        target = git_network_lease.extended_windows_path(target)
        try:
            os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            pass
        function(target)

    def remove_one(function, target: str) -> None:
        try:
            function(target)
        except OSError:
            make_writable(function, target, None)

    def remove_entry(entry: Path) -> None:
        target = git_network_lease.extended_windows_path(entry)
        if _is_link(entry):
            operation = os.rmdir if os.name == "nt" and os.path.isdir(target) else os.unlink
            operation(target)
            return
        if os.path.isdir(target):
            if not needs_extended(entry):
                shutil.rmtree(entry, onerror=make_writable)
                return
            with os.scandir(target) as children:
                names = [child.name for child in children]
            for name in names:
                remove_entry(entry / name)
            remove_one(os.rmdir, target)
            return
        remove_one(os.unlink, target)

    # Keep the verified owner marker until every other child is gone. A pack
    # ACL failure must leave a session that the next cleanup can still own.
    marker = path / OWNER_FILE
    marker_path = git_network_lease.extended_windows_path(marker)
    if not os.path.isfile(marker_path):
        # Retain the private helper used by existing verified test-temp cleanup.
        # Public cleanup_session always validates an owned marker first.
        try:
            remove_entry(path)
        except OSError as exc:
            raise SessionError(str(exc)) from exc
        return
    with open(marker_path, "rb") as stream:
        ownership = stream.read()
    info = os.stat(git_network_lease.extended_windows_path(path))
    identity = (info.st_dev, info.st_ino)
    try:
        with os.scandir(git_network_lease.extended_windows_path(path)) as entries:
            child_names = [entry.name for entry in entries]
        for name in child_names:
            if name == OWNER_FILE:
                continue
            remove_entry(path / name)
        if path_units(marker) < 248:
            marker.unlink()
        else:
            remove_one(os.unlink, marker_path)
        if path_units(path) < 248:
            path.rmdir()
        else:
            remove_one(os.rmdir, git_network_lease.extended_windows_path(path))
    except OSError as exc:
        # Root deletion itself can fail after marker removal. Restore only in
        # the same plain directory; never authorize a replaced or linked tree.
        try:
            path_value = git_network_lease.extended_windows_path(path)
            marker_value = git_network_lease.extended_windows_path(marker)
            restored_info = os.stat(path_value)
            if (os.path.isdir(path_value) and not _is_link(path)
                    and (restored_info.st_dev, restored_info.st_ino) == identity
                    and not os.path.exists(marker_value)):
                with open(marker_value, "xb") as stream:
                    stream.write(ownership)
        except OSError:
            pass
        raise SessionError(f"審查暫存清理失敗，請手動檢查：{path}（{exc}）") from exc


def cleanup_session(requested: str | Path) -> bool:
    """Remove only a verified, session-owned tree. Missing sessions are a no-op."""
    path = _owned_session(requested)
    if path is None:
        return False
    _remove_tree(path)
    return True


def validate_session(requested: str | Path) -> Path:
    """Return a session path only when its owner marker and location are valid."""
    path = _owned_session(requested)
    if path is None:
        raise SessionError(f"審查暫存目錄已不存在或缺少所有權標記：{requested}")
    return path


def cleanup_if_owned(requested: str | Path) -> bool:
    """Remove a managed session while preserving legacy caller-owned directories."""
    path = Path(requested).expanduser().absolute()
    if not path.exists() and not path.is_symlink():
        return False
    if not (path / OWNER_FILE).is_file() or _is_link(path / OWNER_FILE):
        return False
    return cleanup_session(path)


def main(argv: list[str] | None = None) -> int:
    values = sys.argv[1:] if argv is None else argv
    if values and values[0] in ("start", "step", "check", "finish", "status", "cancel"):
        from review_flow import main as flow_main
        return flow_main(values)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    cleanup = commands.add_parser("cleanup", help="Remove one verified owned evidence session.")
    cleanup.add_argument("--context-dir", required=True)
    args = parser.parse_args(values)
    try:
        removed = cleanup_session(args.context_dir)
    except (OSError, ValueError) as exc:
        print(f"review-session: error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"context_dir": str(Path(args.context_dir).expanduser().absolute()),
                      "removed": removed}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
