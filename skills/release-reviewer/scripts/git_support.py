"""Git primitives adapted from MergeReviewer 0.8.0.

Source commit: efabb07f054b0dfe0a3ba1f5f8cd9f5aa9438f6a.
Only explicit-ref review is included; no runtime dependency on that project.
"""


from __future__ import annotations


import argparse


import contextvars


import hashlib


import json


import math


import os


import re


import shutil


import stat


import subprocess
import git_network_lease


import sys


import tempfile


from dataclasses import dataclass


from datetime import datetime, timezone


from pathlib import Path, PurePosixPath, PureWindowsPath


from typing import Any, Iterable, Mapping, Sequence


from urllib.parse import unquote, urlparse


SKIP_DIRECTORIES = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "out",
    ".next",
    ".nuxt",
    ".venv",
    "venv",
    "__pycache__",
    ".idea",
}


class ReviewContextError(RuntimeError):
    """A user-actionable preparation failure; ``hint`` is the suggested next step."""

    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.hint = hint


class GitCommandError(ReviewContextError):
    def __init__(self, args: Sequence[str], returncode: int, stderr: str):
        command = "git " + " ".join(args)
        detail = stderr.strip() or "Git command failed without diagnostic output."
        hint = (
            "請確認 repository 或遠端連線狀態；可使用 --git-timeout 增加等待時間後重試。"
            if returncode == 124
            else None
        )
        super().__init__(f"{command} (exit {returncode}): {detail}", hint=hint)
        self.args_list = list(args)
        self.returncode = returncode
        self.stderr = detail


@dataclass(frozen=True)
class RemoteTarget:
    remote: str
    branch: str
    input_ref: str


DIFF_FLAGS = ("--no-ext-diff", "--no-textconv")


DEFAULT_GIT_TIMEOUT_SECONDS = 180.0


GIT_TIMEOUT_SECONDS = contextvars.ContextVar(
    "release_reviewer_git_timeout", default=DEFAULT_GIT_TIMEOUT_SECONDS
)
NETWORK_ACTIVITY = contextvars.ContextVar("release_reviewer_network_activity", default=None)
NETWORK_LOG = contextvars.ContextVar("release_reviewer_network_log", default=None)
CAPTURE_PHASE = contextvars.ContextVar("release_reviewer_capture_phase", default="local-preflight")


def empty_network_activity() -> dict[str, int]:
    return {"fetch_attempt_count": 0, "fetch_success_count": 0,
            "requested_ref_count": 0, "synced_ref_count": 0}


def network_activity() -> dict[str, int]:
    """Capture-scoped fetch command counts; these are not connection counts."""
    return dict(NETWORK_ACTIVITY.get() or empty_network_activity())


def empty_network_log():
    return {"reason": "not-evaluated", "phase": "local-preflight", "fetches": [],
            "http_requests": None, "ssh_requests": None}


def network_log():
    import copy
    return copy.deepcopy(NETWORK_LOG.get() or empty_network_log())


def capture_phase(value: str) -> None:
    CAPTURE_PHASE.set(value)
    import review_coordinator
    active = review_coordinator.ACTIVE.get()
    if active:
        active.phase(value)


def capture_diagnostic(exc: Exception) -> dict[str, Any]:
    return {"phase": getattr(exc, "phase", CAPTURE_PHASE.get()),
            "network_activity": getattr(exc, "network_activity", network_activity()),
            "network_log": getattr(exc, "network_log", network_log())}


def run_git(
    repo: Path,
    args: Sequence[str],
    *,
    check: bool = True,
    binary: bool = False,
    env: Mapping[str, str] | None = None,
    input_bytes: bytes | None = None,
    output_file=None,
) -> str | bytes:
    import review_coordinator
    active = review_coordinator.ACTIVE.get()
    if active:
        active.check()
    command_env = os.environ.copy()
    if env:
        command_env.update(env)
    command_env["GIT_OPTIONAL_LOCKS"] = "0"
    command_env["GIT_TERMINAL_PROMPT"] = "0"
    command_env["GIT_NO_LAZY_FETCH"] = "1"
    command_env["GIT_NO_REPLACE_OBJECTS"] = "1"
    if not (args and args[0] == "fetch"):
        # Older Git ignores GIT_NO_LAZY_FETCH; deny implicit transports too.
        command_env["GIT_ALLOW_PROTOCOL"] = ""
    try:
        runner = git_network_lease.run if args and args[0] == "fetch" else subprocess.run
        completed = runner(
            ["git", "-C", str(repo), *args],
            input=input_bytes,
            stdout=output_file if output_file is not None else subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            env=command_env,
            timeout=GIT_TIMEOUT_SECONDS.get(),
        )
    except subprocess.TimeoutExpired as exc:
        raise ReviewContextError(
            f"Git 指令逾時（{GIT_TIMEOUT_SECONDS.get():g} 秒）：git {' '.join(args)}",
            hint="請確認 repository、磁碟或遠端連線狀態；可使用 --git-timeout 增加等待時間後重試。",
        ) from exc
    if check and completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace")
        raise GitCommandError(args, completed.returncode, stderr)
    if binary:
        return completed.stdout or b""
    return (completed.stdout or b"").decode("utf-8", errors="replace")


def run_git_capture(
    repo: Path,
    args: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run Git without raising so callers can provide a useful diagnostic."""
    command_env = os.environ.copy()
    if env:
        command_env.update(env)
    command_env["GIT_OPTIONAL_LOCKS"] = "0"
    command_env["GIT_TERMINAL_PROMPT"] = "0"
    command_env["GIT_NO_LAZY_FETCH"] = "1"
    command_env["GIT_NO_REPLACE_OBJECTS"] = "1"
    command_env["GIT_ALLOW_PROTOCOL"] = ""
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            env=command_env,
            timeout=GIT_TIMEOUT_SECONDS.get(),
        )
    except subprocess.TimeoutExpired as exc:
        message = (
            f"Git 指令逾時（{GIT_TIMEOUT_SECONDS.get():g} 秒）："
            f"git {' '.join(args)}"
        )
        return 124, "", message
    return (
        completed.returncode,
        completed.stdout.decode("utf-8", errors="replace"),
        completed.stderr.decode("utf-8", errors="replace"),
    )


def git_ok(repo: Path, args: Sequence[str]) -> bool:
    command_env = os.environ.copy()
    command_env["GIT_OPTIONAL_LOCKS"] = "0"
    command_env["GIT_TERMINAL_PROMPT"] = "0"
    command_env["GIT_NO_LAZY_FETCH"] = "1"
    command_env["GIT_NO_REPLACE_OBJECTS"] = "1"
    command_env["GIT_ALLOW_PROTOCOL"] = ""
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), *args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            env=command_env,
            timeout=GIT_TIMEOUT_SECONDS.get(),
        )
    except subprocess.TimeoutExpired as exc:
        raise ReviewContextError(
            f"Git 指令逾時（{GIT_TIMEOUT_SECONDS.get():g} 秒）：git {' '.join(args)}",
            hint="請確認 repository 狀態；可使用 --git-timeout 增加等待時間後重試。",
        ) from exc
    return completed.returncode == 0


def normalize_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def safe_relative_git_path(value: str) -> bool:
    """Reject paths that could escape a bundle on either supported OS."""
    posix_path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    return bool(value) and not (
        posix_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or ".." in posix_path.parts
        or ".." in windows_path.parts
    )


def strip_jsonc(text: str) -> str:
    """Remove JSONC comments without treating URL text as a comment."""
    output: list[str] = []
    in_string = False
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            output.append(char)
            index += 1
            continue
        if char == "/" and next_char == "/":
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                index += 1
            continue
        if char == "/" and next_char == "*":
            index += 2
            while index + 1 < len(text) and not (text[index] == "*" and text[index + 1] == "/"):
                index += 1
            index += 2 if index + 1 <= len(text) else 0
            continue
        output.append(char)
        index += 1
    return "".join(output)


def strip_trailing_commas(text: str) -> str:
    """Accept the trailing commas allowed by VS Code JSONC files."""
    output: list[str] = []
    in_string = False
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            output.append(char)
            index += 1
            continue
        if char == ",":
            lookahead = index + 1
            while lookahead < len(text) and text[lookahead].isspace():
                lookahead += 1
            if lookahead < len(text) and text[lookahead] in "}]":
                index += 1
                continue
        output.append(char)
        index += 1
    return "".join(output)


def workspace_roots(workspace: Path, workspace_file: Path | None) -> list[Path]:
    if workspace_file is None:
        return [workspace]
    try:
        content = workspace_file.read_text(encoding="utf-8-sig")
        raw = json.loads(strip_trailing_commas(strip_jsonc(content)))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewContextError(f"無法讀取 VS Code workspace: {workspace_file}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ReviewContextError(
            f"VS Code workspace 必須是包含 folders 陣列的 JSON 物件：{workspace_file}"
        )
    folders = raw.get("folders", [])
    if not isinstance(folders, list):
        raise ReviewContextError(
            f"VS Code workspace 的 folders 必須是陣列：{workspace_file}"
        )
    roots: list[Path] = []
    for folder in folders:
        if not isinstance(folder, dict):
            continue
        value = folder.get("path")
        if not value and folder.get("uri"):
            parsed = urlparse(str(folder["uri"]))
            if parsed.scheme == "file":
                value = unquote(parsed.path)
                if os.name == "nt" and value.startswith("/") and len(value) > 2 and value[2] == ":":
                    value = value[1:]
        if not value:
            continue
        candidate = Path(str(value))
        if not candidate.is_absolute():
            candidate = workspace_file.parent / candidate
        # Preserve lexical roots so the Group reader can reject links before resolving.
        roots.append(candidate.absolute())
    return roots or [workspace_file.parent]


def has_git_marker(path: Path) -> bool:
    marker = path / ".git"
    return marker.is_dir() or marker.is_file()


def discover_repositories(workspace: Path, workspace_file: Path | None) -> list[Path]:
    roots = workspace_roots(workspace, workspace_file)
    repositories: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        if has_git_marker(root):
            repositories.add(root)
            continue
        for current, directories, _files in os.walk(root, topdown=True, followlinks=False):
            current_path = Path(current)
            directories[:] = [name for name in directories if name not in SKIP_DIRECTORIES]
            if has_git_marker(current_path):
                repositories.add(normalize_path(current_path))
                directories[:] = []
    return sorted(repositories, key=lambda item: str(item).casefold())


def choose_repository(
    workspace: Path,
    workspace_file: Path | None,
    project: str | None,
) -> tuple[Path, list[Path]]:
    repositories = discover_repositories(workspace, workspace_file)
    if project:
        requested = Path(project).expanduser()
        candidates: list[Path] = []
        relative_target = workspace / requested
        if requested.is_absolute() or relative_target.exists():
            target = normalize_path(requested if requested.is_absolute() else workspace / requested)
            candidates = [repo for repo in repositories if repo == target]
        if not candidates:
            needle = project.casefold().rstrip("\\/")
            candidates = [
                repo
                for repo in repositories
                if repo.name.casefold() == needle or str(repo).casefold() == needle
            ]
        if len(candidates) != 1:
            available = ", ".join(str(repo) for repo in repositories) or "（找不到 Git repository）"
            raise ReviewContextError(
                f"專案名稱「{project}」無法唯一定位 repository。候選：{available}",
                hint="請從候選清單擇一，以完整路徑或資料夾名稱重新指定專案名稱。",
            )
        return candidates[0], repositories
    if len(repositories) != 1:
        available = ", ".join(str(repo) for repo in repositories) or "（找不到 Git repository）"
        raise ReviewContextError(
            f"未提供專案名稱，工作區必須恰好有一個 repository；候選：{available}",
            hint="請指定專案名稱或 repository 路徑；若找不到 repository，請確認工作區資料夾是否正確。",
        )
    return repositories[0], repositories


def remote_names(repo: Path) -> list[str]:
    output = str(run_git(repo, ["remote"]))
    return [line.strip() for line in output.splitlines() if line.strip()]


def is_shallow_repository(repo: Path) -> bool:
    return str(
        run_git(repo, ["rev-parse", "--is-shallow-repository"], check=False)
    ).strip().lower() == "true"


def verified_commit(repo: Path, ref: str) -> str | None:
    value = str(
        run_git(
            repo,
            ["rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"],
            check=False,
        )
    ).strip()
    return value or None


def resolve_commit_ref(repo: Path, ref: str) -> tuple[str | None, str]:
    """Resolve a ref while keeping local and remote branch namespaces separate."""
    target = remote_target_for_ref(repo, ref)
    if target:
        remote_ref = f"refs/remotes/{target.remote}/{target.branch}"
        remote_commit = verified_commit(repo, remote_ref)
        return remote_commit, remote_ref

    if ref.startswith("refs/heads/"):
        local_commit = verified_commit(repo, ref)
        return (local_commit, ref)

    local_branch_ref = f"refs/heads/{ref}"
    if local_branch_exists(repo, ref):
        local_commit = verified_commit(repo, local_branch_ref)
        return (local_commit, local_branch_ref)

    if ref.startswith("refs/tags/"):
        tag_commit = verified_commit(repo, ref)
        return (tag_commit, ref)

    # Keep short tag names available, but never use an arbitrary Git revision
    # lookup here: that could silently resolve a stale remote-tracking ref.
    tag_commit = verified_commit(repo, f"refs/tags/{ref}")
    if tag_commit:
        return tag_commit, f"refs/tags/{ref}"

    if ref == "HEAD" or re.fullmatch(r"[0-9a-fA-F]{4,64}", ref):
        object_commit = verified_commit(repo, ref)
        if object_commit:
            return object_commit, ref
    return None, ref


def local_branch_exists(repo: Path, branch: str) -> bool:
    return git_ok(repo, ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"])


def remote_target_for_ref(repo: Path, ref: str) -> RemoteTarget | None:
    """Classify only explicitly remote-qualified refs.

    An unqualified branch name is deliberately never inferred from a remote or
    from a local upstream configuration.  This keeps ``release`` and
    ``origin/release`` as two distinct user choices.
    """
    remotes = remote_names(repo)
    if ref.startswith("refs/remotes/"):
        normalized = ref[len("refs/remotes/") :]
        if "/" not in normalized:
            raise ReviewContextError(f"遠端 ref「{ref}」缺少分支名稱。")
        remote, branch = normalized.split("/", 1)
        if remote not in remotes:
            choices = ", ".join(remotes) or "（沒有已設定的 remote）"
            raise ReviewContextError(
                f"指定的 remote「{remote}」不存在；候選：{choices}。",
                hint="請改用已設定的 remote 名稱，例如 origin/main。",
            )
        if not branch:
            raise ReviewContextError(f"遠端 ref「{ref}」缺少分支名稱。")
        return RemoteTarget(remote, branch, ref)

    if not ref.startswith("refs/") and "/" in ref:
        remote, branch = ref.split("/", 1)
        if remote in remotes:
            if not branch:
                raise ReviewContextError(f"遠端 ref「{ref}」缺少分支名稱。")
            return RemoteTarget(remote, branch, ref)
    return None


def fetch_inputs(repo: Path, refs: Iterable[str], enabled: bool) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    grouped: dict[str, list[RemoteTarget]] = {}
    for ref in refs:
        target = remote_target_for_ref(repo, ref)
        if not target:
            continue
        if (target.remote, target.branch) in seen:
            continue
        seen.add((target.remote, target.branch))
        grouped.setdefault(target.remote, []).append(target)
    activity = NETWORK_ACTIVITY.get()
    log = NETWORK_LOG.get()
    if log is not None:
        log["reason"] = "explicit-remote-branches" if grouped else "local-branches"
        log["phase"] = "fetch" if grouped else "local-preflight"
    if activity is not None:
        activity["requested_ref_count"] += len(seen)
    if grouped and not enabled:
        target = next(iter(grouped.values()))[0]
        raise ReviewContextError(
            f"遠端 ref「{target.input_ref}」需要連線確認，不能搭配 --no-fetch。",
            hint="若要離線審查，請改用明確本機分支。",
        )
    for remote, targets in grouped.items():
        try:
            capture_phase("fetch")
            event = {"reason": "explicit-remote-branches", "phase": "fetch", "remote": remote,
                     "requested_refs": [item.input_ref for item in targets],
                     "fetch_attempt_count": 1, "fetch_success_count": 0}
            if log is not None:
                log["fetches"].append(event)
            if activity is not None:
                activity["fetch_attempt_count"] += 1
            run_git(
                repo,
                [
                    "fetch",
                    "--no-tags",
                    "--no-prune",
                    "--atomic",
                    "--no-recurse-submodules",
                    "--no-auto-maintenance",
                    "--no-write-fetch-head",
                    "--jobs=1",
                    remote,
                    *[f"refs/heads/{t.branch}:refs/remotes/{remote}/{t.branch}" for t in targets],
                ],
            )
            if activity is not None:
                activity["fetch_success_count"] += 1
                activity["synced_ref_count"] += len(targets)
            event["fetch_success_count"] = 1
        except GitCommandError as exc:
            raise ReviewContextError(
                f"fetch {remote} 失敗（輸入 {', '.join(t.input_ref for t in targets)}）：{exc.stderr}",
                hint="請確認網路、帳號權限，以及遠端是否有此分支；為避免審查過期內容，不會自動改用舊版本。",
            ) from exc
        actions.extend({"remote": remote, "branch": t.branch, "input_ref": t.input_ref} for t in targets)
    return actions


def ref_source_description(repo: Path, ref: str) -> str:
    target = remote_target_for_ref(repo, ref)
    if target:
        return f"指定遠端分支 {target.remote}/{target.branch}"
    if ref.startswith("refs/heads/"):
        return "本機分支"
    if ref.startswith("refs/tags/"):
        return "指定 tag"
    if local_branch_exists(repo, ref):
        return "本機分支"
    if ref == "HEAD":
        return "HEAD"
    if re.fullmatch(r"[0-9a-fA-F]{4,64}", ref):
        return "commit SHA"
    return "本機分支或短 tag"


def parse_name_status(raw: str) -> list[dict[str, str | None]]:
    parts = raw.split("\0")
    if parts and parts[-1] == "":
        parts.pop()
    changes: list[dict[str, str | None]] = []
    index = 0
    while index < len(parts):
        status = parts[index]
        index += 1
        if status.startswith(("R", "C")):
            if index + 1 >= len(parts):
                raise ReviewContextError("Git 回傳的 rename/copy name-status 資料不完整。")
            old_path, new_path = parts[index], parts[index + 1]
            index += 2
            changes.append({"status": status, "old_path": old_path, "path": new_path})
        else:
            if index >= len(parts):
                raise ReviewContextError("Git 回傳的 name-status 資料不完整。")
            changes.append({"status": status, "old_path": None, "path": parts[index]})
            index += 1
    return changes


def parse_numstat(raw: str) -> dict[str, tuple[str, str]]:
    records: dict[str, tuple[str, str]] = {}
    parts = raw.split("\0")
    if parts and not parts[-1]:
        parts.pop()
    index = 0
    while index < len(parts):
        fields = parts[index].split("\t", 2)
        index += 1
        if len(fields) != 3:
            raise ReviewContextError("Git 回傳的 numstat 資料不完整。")
        additions, deletions, path = fields
        if path:
            records[path] = (additions, deletions)
            continue
        if index + 1 >= len(parts):
            raise ReviewContextError("Git 回傳的 rename/copy numstat 路徑不完整。")
        _old_path, new_path = parts[index], parts[index + 1]
        index += 2
        records[new_path] = (additions, deletions)
    return records


def collect_changes(
    repo: Path,
    left: str,
    right: str,
    *,
    env: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    name_status = str(
        run_git(
            repo,
            [
                "diff",
                *DIFF_FLAGS,
                "--name-status",
                "--find-renames",
                "--find-copies",
                "-z",
                "--diff-algorithm=histogram",
                left,
                right,
                "--",
            ],
            env=env,
        )
    )
    changes = parse_name_status(name_status)
    # Raw metadata and numstat use the same diff options. Git emits all raw
    # records before numstat, so they can share one traversal. Keep the
    # histogram name-status pass separate to preserve its rename decisions.
    raw_status = str(
        run_git(
            repo,
            ["diff", *DIFF_FLAGS, "--raw", "--numstat", "--no-abbrev", "--find-renames", "--find-copies", "-z", left, right, "--"],
            env=env,
        )
    )
    raw_parts = raw_status.split("\0")
    raw_records: dict[tuple[str | None, str], dict[str, str]] = {}
    index = 0
    while index < len(raw_parts) and raw_parts[index].startswith(":"):
        metadata = raw_parts[index]
        index += 1
        if not metadata:
            continue
        fields = metadata.removeprefix(":").split()
        if len(fields) < 5:
            raise ReviewContextError("Git 回傳的 raw diff 資料不完整。")
        old_mode, new_mode, old_blob, new_blob, status = fields[:5]
        path_count = 2 if status.startswith(("R", "C")) else 1
        if index + path_count > len(raw_parts):
            raise ReviewContextError("Git 回傳的 raw diff 路徑資料不完整。")
        paths = raw_parts[index : index + path_count]
        index += path_count
        old_path, path = (paths[0], paths[1]) if path_count == 2 else (None, paths[0])
        raw_records[(old_path, path)] = {
            "old_mode": old_mode,
            "new_mode": new_mode,
            "old_blob": old_blob,
            "new_blob": new_blob,
        }
    numstat = parse_numstat("\0".join(raw_parts[index:]))
    for change in changes:
        path = str(change["path"])
        additions, deletions = numstat.get(path, ("0", "0"))
        change["additions"] = None if additions == "-" else int(additions)
        change["deletions"] = None if deletions == "-" else int(deletions)
        change["binary"] = additions == "-" or deletions == "-"
        change.update(raw_records.get((change["old_path"], path), {}))
    return changes


def collect_submodule_paths(
    repo: Path,
    left: str,
    right: str,
    *,
    env: Mapping[str, str] | None = None,
) -> set[str]:
    """Return changed paths that are gitlinks on either side of a diff."""
    raw = str(
        run_git(
            repo,
            ["diff", *DIFF_FLAGS, "--raw", "--no-abbrev", "-z", left, right, "--"],
            env=env,
        )
    )
    parts = raw.split("\0")
    paths: set[str] = set()
    index = 0
    while index < len(parts):
        metadata = parts[index]
        index += 1
        if not metadata:
            continue
        fields = metadata.removeprefix(":").split()
        if len(fields) < 5:
            raise ReviewContextError("Git 回傳的 raw diff 資料不完整。")
        old_mode, new_mode, _old_sha, _new_sha, status = fields[:5]
        path_count = 2 if status.startswith(("R", "C")) else 1
        if index + path_count > len(parts):
            raise ReviewContextError("Git 回傳的 raw diff 路徑資料不完整。")
        changed_paths = [path for path in parts[index : index + path_count] if path]
        index += path_count
        if old_mode == "160000" or new_mode == "160000":
            paths.update(changed_paths)
    return paths


def commit_subject(repo: Path, commit: str) -> str:
    return str(run_git(repo, ["show", "-s", "--format=%s", commit])).strip()


def merge_commit_records(repo: Path, left: str, right: str) -> list[dict[str, Any]]:
    # NUL-delimited fields keep subjects independent of line-oriented parsing,
    # and one log walk replaces separate parent/subject queries per merge.
    metadata = str(run_git(
        repo, ["log", "--merges", "--reverse", "--format=%H%x00%P%x00%s", "-z", f"{left}..{right}"]
    )).split("\0")
    if metadata and not metadata[-1]:
        metadata.pop()
    if len(metadata) % 3:
        raise ReviewContextError("Git 回傳的 merge commit 資料不完整。")
    records: list[dict[str, Any]] = []
    for index in range(0, len(metadata), 3):
        merge_sha, parents_line, subject = metadata[index:index + 3]
        parents = parents_line.split()
        parent_views = []
        for position, parent in enumerate(parents, start=1):
            parent_views.append(
                {
                    "position": position,
                    "sha": parent,
                    "changes": collect_changes(repo, parent, merge_sha),
                    "stat": str(run_git(repo, ["diff", "--stat", "--find-renames", parent, merge_sha])).strip(),
                }
            )
        records.append(
            {
                "sha": merge_sha,
                "subject": subject.strip(),
                "parents": parents,
                "parent_views": parent_views,
            }
        )
    return records


def create_merge_preview(
    repo: Path,
    base_sha: str,
    head_sha: str,
    merge_base: str,
    temporary_dir: Path,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Preview a merge in temporary object storage without writing the repository."""
    object_directory = temporary_dir / "objects"
    object_directory.mkdir(parents=True, exist_ok=True)
    env = {
        "GIT_OBJECT_DIRECTORY": str(object_directory),
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(git_path(repo, "objects")),
    }
    args = [
        "merge-tree",
        "--write-tree",
        "--messages",
        f"--merge-base={merge_base}",
        base_sha,
        head_sha,
    ]
    returncode, output, error = run_git_capture(repo, args, env=env)
    first_line = output.splitlines()[0].strip() if output.splitlines() else ""
    if returncode not in (0, 1) or not re.fullmatch(r"[0-9a-fA-F]{40,64}", first_line):
        detail = error.strip() or output.strip() or f"git merge-tree failed (exit {returncode})"
        return {
            "status": "unavailable",
            "exit_code": returncode,
            "detail": detail,
            "tree_sha": None,
            "changed_files": [],
        }, env
    changes = collect_changes(repo, head_sha, first_line, env=env)
    return {
        "status": "conflicts" if returncode == 1 else "clean",
        "exit_code": returncode,
        "detail": output,
        "tree_sha": first_line,
        "head_sha": head_sha,
        "changed_files": changes,
        "env": env,
        "manifest_info": {},
    }, env


def git_path(repo: Path, name: str) -> Path:
    value = Path(str(run_git(repo, ["rev-parse", "--git-path", name])).strip())
    return value if value.is_absolute() else repo / value
