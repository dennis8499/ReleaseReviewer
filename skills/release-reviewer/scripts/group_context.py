"""Discover workspace Git roots and pin current committed HEADs without fetching."""
from __future__ import annotations

from pathlib import Path
import stat

import git_support as git


def is_link(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0)
                                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def discover(workspace: Path, workspace_file: Path | None) -> tuple[list[Path], list[str]]:
    repositories, gaps = set(), []
    for root in git.workspace_roots(workspace, workspace_file):
        if not root.is_dir():
            gaps.append(f"工作區根目錄不存在：{root}")
            continue
        if is_link(root):
            gaps.append(f"工作區根目錄為連結，未讀取：{root}")
            continue
        try:
            candidates = [root] if git.has_git_marker(root) else sorted(root.iterdir())
        except OSError as exc:
            gaps.append(f"工作區目錄無法列出：{root}；{exc}")
            continue
        for candidate in candidates:
            if candidate.name in git.SKIP_DIRECTORIES or not candidate.is_dir():
                continue
            if is_link(candidate):
                gaps.append(f"Group 子目錄為連結，未讀取：{candidate}")
                continue
            if not git.has_git_marker(candidate):
                continue
            try:
                actual = git.normalize_path(str(git.run_git(candidate, ["rev-parse", "--show-toplevel"])).strip())
                if actual != candidate.resolve():
                    raise git.ReviewContextError("不是獨立 Git 根目錄")
                repositories.add(actual)
            except git.ReviewContextError as exc:
                gaps.append(f"無法核對 Repo {candidate}：{exc}")
    return sorted(repositories, key=lambda p: str(p).casefold()), gaps


def choose(repositories: list[Path], workspace: Path, project: str) -> Path:
    if not project or not project.strip():
        raise git.ReviewContextError("Repo 為必填，不能自動選取。")
    requested = Path(project).expanduser()
    target = git.normalize_path(requested if requested.is_absolute() else workspace / requested)
    matches = [p for p in repositories if p == target]
    if not matches:
        matches = [p for p in repositories if p.name.casefold() == project.casefold()]
    if len(matches) != 1:
        raise git.ReviewContextError("Repo 無法唯一定位；請指定完整路徑。候選：" +
                                     ", ".join(map(str, repositories)))
    return matches[0]


def branch_ref(repo: Path, value: str) -> str:
    """Only branch namespaces are accepted, even when a tag has the same name."""
    if not isinstance(value, str) or not value or value == "HEAD" or value.startswith("-"):
        raise git.ReviewContextError("請提供明確分支，不能使用 HEAD、tag 或 commit。")
    remote = git.remote_target_for_ref(repo, value)
    ref = f"refs/remotes/{remote.remote}/{remote.branch}" if remote else (
        value if value.startswith("refs/heads/") else f"refs/heads/{value}")
    if value.startswith("refs/") and not value.startswith(("refs/heads/", "refs/remotes/")):
        raise git.ReviewContextError("只接受本機或明確遠端分支，不接受 tag／commit。")
    if ref.endswith("/HEAD") or not git.git_ok(repo, ["check-ref-format", ref]):
        raise git.ReviewContextError(f"分支名稱無效：{value}")
    return ref


def supporting(repo: Path, identifier: str, hints: list[dict]) -> dict:
    record = {"id": identifier, "name": repo.name, "path": str(repo), "role": "supporting",
              "status": "unavailable", "branch": None, "branch_source": "local-HEAD", "ref": None}
    try:
        branch = str(git.run_git(repo, ["symbolic-ref", "--quiet", "HEAD"], check=False)).strip()
        record["branch"] = branch.removeprefix("refs/heads/") or None
        sha = git.verified_commit(repo, "HEAD")
        if not sha:
            raise git.ReviewContextError("目前 HEAD 沒有已提交版本")
        record.update(status="captured", ref=sha)
    except (OSError, KeyError, TypeError, git.ReviewContextError) as exc:
        record["reason"] = str(exc)
    return record


def catalog(repositories: list[Path], entry: Path, hints: dict) -> tuple[list[dict], list[str]]:
    if not isinstance(hints, dict) or not isinstance(hints.get("repositories", []), list):
        raise git.ReviewContextError("workspace context 必須包含 repositories 陣列。")
    records = hints.get("repositories", [])
    for hint in records:
        if not isinstance(hint, dict) or not isinstance(hint.get("path"), str):
            raise git.ReviewContextError("工作區 Repo 資料必須含 path。")
        name = hint.get("defaultBranch")
        if name is not None and not isinstance(name, str):
            raise git.ReviewContextError("defaultBranch 必須是文字或 null。")
    gaps = [f"工作區提供的 Repo 不存在或未通過 Git 根目錄核對：{h['path']}"
            for h in records if git.normalize_path(h["path"]) not in repositories]
    result = [{"id": "entry", "name": entry.name, "path": str(entry), "role": "entry", "status": "captured"}]
    for number, repo in enumerate((p for p in repositories if p != entry), 1):
        item = supporting(repo, f"repo-{number:03}", records)
        result.append(item)
        if item["status"] != "captured":
            gaps.append(f"{repo.name} 關聯版本未取得：{item['reason']}")
    return result, gaps
