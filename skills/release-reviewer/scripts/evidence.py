#!/usr/bin/env python3
"""Read and search frozen source versions, including the whole baseline Repo."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import subprocess
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import review_session

SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_CACHE = ContextVar("release_evidence_operation", default=None)


@contextmanager
def operation_cache(manifest: dict, context: Path):
    """Share immutable Git evidence only within one batch or validation operation.

    A nested operation receives its own cache. All public readers still validate
    source, Repo, SHA and path before consulting cached data.
    """
    token = _CACHE.set({"manifest": manifest, "context": Path(context),
                        "sources": {}, "blobs": {}, "metadata": {}, "searches": {}})
    try:
        yield
    finally:
        _CACHE.reset(token)


def _cache(manifest, context):
    state = _CACHE.get()
    if state is not None and state["manifest"] is manifest and state["context"] == Path(context):
        return state
    return None


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def safe_path(value: str) -> bool:
    if not isinstance(value, str) or not value or "\0" in value or "\\" in value or ":" in value:
        return False
    paths = (PurePosixPath(value), PureWindowsPath(value))
    return not any(p.is_absolute() or ".." in p.parts for p in paths) and not paths[1].drive


def load_manifest(context: Path) -> dict:
    context = review_session.validate_session(context)
    path = context / "manifest.json"
    require(not path.is_symlink(), "manifest 不能是符號連結。")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(manifest, dict) and manifest.get("schema") == "ReleaseReviewContext/v3",
            "context schema 無效。")
    require(Path(manifest["context_dir"]).resolve() == context, "context 目錄與 manifest 不符。")
    report_root = manifest.get("report_root")
    require(isinstance(report_root, str) and Path(report_root).is_absolute(), "缺少工作區報告根目錄。")
    require(Path(report_root).is_dir() and Path(report_root).resolve() == Path(report_root),
            "工作區報告根目錄不存在或已改指其他位置。")
    for field in ("base_sha", "head_sha", "diff_base"):
        require(isinstance(manifest.get(field), str) and SHA.fullmatch(manifest[field]), f"{field} 無效。")
    require(manifest.get("mode") == "merge", "只支援合併前審查。")
    require(manifest.get("entry_repo") == "entry" and manifest.get("scope") in {"group", "repository"},
            "工作區身分無效。")
    repositories = manifest.get("repositories")
    require(isinstance(repositories, list) and bool(repositories), "Repo catalog 無效。")
    identifiers = set()
    for item in repositories:
        require(isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"] not in identifiers,
                "Repo 身分重複或無效。")
        identifiers.add(item["id"])
        require(item.get("role") in {"entry", "supporting"} and item.get("status") in {"captured", "unavailable"},
                "Repo 角色或狀態無效。")
        require(isinstance(item.get("path"), str) and Path(item["path"]).is_absolute(), "Repo 路徑無效。")
        if item["id"] == "entry":
            require(item["role"] == "entry" and item["path"] == manifest["repo"], "入口 Repo 不符。")
        else:
            require(item["role"] == "supporting", "關聯 Repo 角色無效。")
            if item["status"] == "captured":
                require(isinstance(item.get("ref"), str) and SHA.fullmatch(item["ref"]), "關聯 SHA 無效。")
            else:
                require(bool(item.get("reason")) and not manifest["context_complete"], "關聯版本缺口未保留。")
    require("entry" in identifiers, "缺少入口 Repo。")
    require(type(manifest.get("context_complete")) is bool, "context_complete 無效。")
    require(isinstance(manifest.get("context_gaps"), list), "context_gaps 無效。")
    require(manifest["context_complete"] == (not manifest["context_gaps"]), "context 完整狀態不一致。")
    require(type(manifest.get("working_tree_unchanged")) is bool, "工作區狀態無效。")
    for field in ("changed_files", "release_changes", "merge_commits"):
        require(isinstance(manifest.get(field), list), f"{field} 無效。")
    preview = manifest.get("merge_preview")
    require(isinstance(preview, dict) and preview.get("status") in {"clean", "conflicts", "unavailable"},
            "合併預覽無效。")
    require(isinstance(preview.get("changed_files"), list), "合併預覽檔案清單無效。")
    require(preview["status"] == "clean" or not manifest["context_complete"],
            "有合併衝突或缺口時不能標為完整。")
    return manifest


def repository(manifest: dict, identifier: str | None = None) -> dict:
    identifier = identifier or "entry"
    matches = [r for r in manifest["repositories"] if r["id"] == identifier]
    require(len(matches) == 1, "證據 Repo 不在固定 catalog。")
    return matches[0]


def source_version(manifest: dict, context: Path, source: str, ref: str | None = None, repo=None):
    selected = repository(manifest, repo)
    require((selected["id"] == "entry") == (source != "supporting"), "證據來源與 Repo 角色不符。")
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
    # Do not inherit an unrelated session's alternate Git object store.
    for key in ("GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
        env.pop(key, None)
    fixed = {"base": manifest["base_sha"], "head": manifest["head_sha"],
             "merge-base": manifest.get("merge_base")}
    objects = None
    if source == "supporting":
        require(selected["status"] == "captured", "關聯 Repo 沒有固定版本。")
        reference = selected["ref"]
    elif source == "merge-preview":
        preview = manifest.get("merge_preview") or {}
        require(preview.get("status") == "clean", "沒有可用的乾淨合併預覽。")
        reference = preview.get("tree_sha")
        require(preview.get("object_directory") == "objects", "預覽物件目錄無效。")
        objects = context / "objects"
        require(objects.is_dir() and not objects.is_symlink() and objects.resolve().is_relative_to(context),
                "預覽物件目錄超出 context。")
    elif source == "merge-parent":
        allowed = {p for m in manifest["merge_commits"] for p in m["parents"]}
        require(ref in allowed, "merge-parent 版本不在固定紀錄內。")
        reference = ref
    else:
        require(source in fixed, "未知的證據來源。")
        reference = fixed[source]
    require(isinstance(reference, str) and SHA.fullmatch(reference), "證據版本無效。")
    require(ref is None or ref == reference, "證據 SHA 與固定來源不符。")
    if objects is not None:
        state = _cache(manifest, context)
        key = (selected["id"], selected["path"], manifest["repo"], source, reference, str(objects))
        alternate = state["sources"].get(key) if state is not None else None
        if alternate is None:
            repo_objects = command(manifest, ["rev-parse", "--git-path", "objects"], env=env).decode().strip()
            alternate = Path(repo_objects)
            if not alternate.is_absolute():
                alternate = Path(manifest["repo"]) / alternate
            alternate = str(alternate)
            if state is not None:
                state["sources"][key] = alternate
        env["GIT_OBJECT_DIRECTORY"] = str(objects)
        env["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = alternate
    return reference, env


def command(manifest: dict, args: list[str], *, env=None, acceptable=(0,)) -> bytes:
    timeout = manifest.get("git_timeout", 180.0)
    require(isinstance(timeout, (int, float)) and math.isfinite(timeout) and timeout > 0, "Git 逾時無效。")
    result = subprocess.run(["git", "-C", manifest["repo"], *args], capture_output=True,
                            timeout=timeout, env=env or {**os.environ, "GIT_OPTIONAL_LOCKS": "0",
                                                       "GIT_TERMINAL_PROMPT": "0"})
    if result.returncode not in acceptable:
        raise ValueError("無法讀取固定 Git 證據：" + result.stderr.decode("utf-8", errors="replace").strip())
    return result.stdout


def _read_blob(manifest: dict, context: Path, source: str, path: str, ref=None, repo=None):
    require(safe_path(path), "證據路徑必須是安全的 Repo 相對路徑。")
    reference, env = source_version(manifest, context, source, ref, repo)
    selected = repository(manifest, repo)
    key = (selected["id"], selected["path"], reference, path,
           env.get("GIT_OBJECT_DIRECTORY"), env.get("GIT_ALTERNATE_OBJECT_DIRECTORIES"))
    state = _cache(manifest, context)
    if state is not None and key in state["blobs"]:
        content = state["blobs"][key]
    else:
        content = command({**manifest, "repo": selected["path"]},
                          ["cat-file", "blob", f"{reference}:{path}"], env=env)
        if state is not None:
            state["blobs"][key] = content
    return reference, key, content


def read_blob(manifest: dict, context: Path, source: str, path: str, ref=None, repo=None) -> bytes:
    return _read_blob(manifest, context, source, path, ref, repo)[2]


def blob_metadata(manifest: dict, context: Path, source: str, path: str, ref=None, repo=None) -> dict:
    """Read, hash and count a validated blob once per operation."""
    _, key, content = _read_blob(manifest, context, source, path, ref, repo)
    state = _cache(manifest, context)
    if state is not None and key in state["metadata"]:
        return state["metadata"][key].copy()
    result = {"content": content, "binary": b"\0" in content,
              "content_sha256": hashlib.sha256(content).hexdigest(), "line_count": len(content.splitlines())}
    if state is not None:
        state["metadata"][key] = result
    return result.copy()


def _list_files(manifest: dict, context: Path, source: str, ref=None, repo=None):
    reference, env = source_version(manifest, context, source, ref, repo)
    selected = {**manifest, "repo": repository(manifest, repo)["path"]}
    raw = command(selected, ["ls-tree", "-r", "--full-tree", "-z", "--name-only", reference], env=env)
    return reference, [p.decode("utf-8", errors="surrogateescape") for p in raw.split(b"\0") if p]


def list_files(manifest: dict, context: Path, source: str, ref=None, repo=None) -> list[str]:
    return _list_files(manifest, context, source, ref, repo)[1]


def search(manifest: dict, context: Path, source: str, query: str, *, ignore_case=False, ref=None, repo=None) -> dict:
    require(isinstance(query, str) and query and "\0" not in query and "\n" not in query,
            "搜尋字串須為非空白的單行 literal。")
    reference, env = source_version(manifest, context, source, ref, repo)
    selected_repo = repository(manifest, repo)
    key = (selected_repo["id"], selected_repo["path"], source, reference, query, bool(ignore_case),
           env.get("GIT_OBJECT_DIRECTORY"), env.get("GIT_ALTERNATE_OBJECT_DIRECTORIES"))
    state = _cache(manifest, context)
    if state is not None and key in state["searches"]:
        cached = state["searches"][key]
        return {**cached, "matches": [match.copy() for match in cached["matches"]]}
    args = ["grep", "--no-textconv", "--full-name", "-I", "-n", "-z", "-F"]
    if ignore_case:
        args.append("-i")
    args += ["-e", query, reference, "--"]
    selected = {**manifest, "repo": selected_repo["path"]}
    raw = command(selected, args, env=env, acceptable=(0, 1))
    matches = []
    offset = 0
    prefix = reference.encode() + b":"
    while offset < len(raw):
        filename_end = raw.find(b"\0", offset)
        require(filename_end >= 0, "Git grep 回傳格式不完整。")
        number_end = raw.find(b"\0", filename_end + 1)
        require(number_end >= 0, "Git grep 回傳格式不完整。")
        filename = raw[offset:filename_end]
        number = raw[filename_end + 1:number_end]
        require(number.isdigit(), "Git grep 回傳格式不完整。")
        line_end = raw.find(b"\n", number_end + 1)
        if line_end < 0:
            line_end = len(raw)
        line = raw[number_end + 1:line_end]
        require(filename.startswith(prefix), "搜尋結果版本不符。")
        matches.append({"path": filename[len(prefix):].decode("utf-8", errors="surrogateescape"),
                        "line": int(number), "text": line.decode("utf-8", errors="replace")})
        offset = line_end + 1
    result = {"repo": selected_repo["id"], "source": source, "ref": reference, "query": query,
              "ignore_case": bool(ignore_case), "scope": "repo", "matches": matches}
    if state is not None:
        state["searches"][key] = {**result, "matches": [match.copy() for match in matches]}
    return result


def record_search(context: Path, result: dict) -> str:
    identifier = hashlib.sha256(canonical(result)).hexdigest()
    directory = context / "searches"
    require(not directory.is_symlink(), "搜尋紀錄目錄不能是符號連結。")
    directory.mkdir(exist_ok=True)
    path = directory / f"{identifier}.json"
    require(not path.is_symlink(), "搜尋紀錄不能是符號連結。")
    if not path.exists():
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return identifier


def validate_search(context: Path, manifest: dict, identifier: str, *, source="base", repo="entry") -> dict:
    require(isinstance(identifier, str) and re.fullmatch(r"[a-f0-9]{64}", identifier), "基礎搜尋紀錄 ID 無效。")
    directory = context / "searches"
    path = directory / f"{identifier}.json"
    require(not directory.is_symlink() and not path.is_symlink(), "搜尋紀錄不能是符號連結。")
    record = json.loads(path.read_text(encoding="utf-8"))
    require(hashlib.sha256(canonical(record)).hexdigest() == identifier, "基礎搜尋紀錄摘要不符。")
    fixed, _ = source_version(manifest, context, source, repo=repo)
    require(record.get("source") == source and record.get("ref") == fixed
            and record.get("repo") == repo and record.get("scope") == "repo", "判定必須搜尋基礎版本整個 Repo 或指定關聯固定 Repo。")
    current = search(manifest, context, source, record["query"], ignore_case=record["ignore_case"], repo=repo)
    require(record == current, "基礎搜尋紀錄與固定來源不符。")
    return record


def validate_baseline_search(context: Path, manifest: dict, identifier: str) -> dict:
    return validate_search(context, manifest, identifier)


SOURCES = ("base", "head", "merge-base", "merge-preview", "merge-parent", "supporting")
REQUEST_FIELDS = {"action", "source", "ref", "repo", "path", "query", "ignore_case"}
EVIDENCE_ERRORS = (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired)


def request_result(manifest: dict, context: Path, request: dict) -> dict:
    """Execute one request with the same result shape as the original CLI."""
    require(isinstance(request, dict), "batch request 必須是物件。")
    require(not request.keys() - REQUEST_FIELDS, "batch request 含未知欄位。")
    action = request.get("action")
    require(action in ("list", "read", "search"), "未知的證據操作。")
    source, repo = request.get("source", "base"), request.get("repo", "entry")
    require(source in SOURCES, "未知的證據來源。")
    require(isinstance(repo, str) and bool(repo), "證據 Repo 必須是非空白身分。")
    ref, path = request.get("ref"), request.get("path")
    ignore_case = request.get("ignore_case", False)
    require(type(ignore_case) is bool, "ignore_case 必須是布林值。")
    if action == "list":
        reference, paths = _list_files(manifest, context, source, ref, repo)
        return {"source": source, "ref": reference, "repo": repo, "paths": paths}
    if action == "read":
        reference, _, content = _read_blob(manifest, context, source, path, ref, repo)
        require(b"\0" not in content, "二進位內容請列為 metadata-only，不能當作文字讀取。")
        return {"repo": repo, "source": source, "ref": reference, "path": path,
                "lines": [{"line": n, "text": line} for n, line in
                          enumerate(content.decode("utf-8", errors="replace").splitlines(), 1)]}
    result = search(manifest, context, source, request.get("query"),
                    ignore_case=ignore_case, ref=ref, repo=repo)
    result["search_id"] = record_search(context, result)
    return result


def batch(manifest: dict, context: Path, requests: list) -> dict:
    """Keep input order and expose every failure while sharing operation-local data."""
    require(isinstance(requests, list), "batch requests 必須是 JSON 陣列。")
    results = []
    with operation_cache(manifest, context):
        for index, request in enumerate(requests):
            try:
                result = request_result(manifest, context, request)
            except EVIDENCE_ERRORS as exc:
                results.append({"index": index, "ok": False, "error": str(exc)})
            else:
                results.append({"index": index, "ok": True, "result": result})
    return {"results": results, "complete": all(item["ok"] for item in results)}


def load_requests(value: str) -> list:
    require(isinstance(value, str) and bool(value.strip()), "batch 必須指定 --requests。")
    inline = value.lstrip("\ufeff \t\r\n")
    raw = inline if inline.startswith(("[", "{")) else Path(value).read_text(encoding="utf-8-sig")
    requests = json.loads(raw)
    if isinstance(requests, dict):
        require(requests.get("schema") == "ReleaseReviewWorklist/v1", "batch worklist schema 無效。")
        requests = requests.get("requests")
    require(isinstance(requests, list), "batch requests 必須是 JSON 陣列。")
    return requests


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("list", "read", "search", "batch"))
    parser.add_argument("--context-dir", required=True)
    parser.add_argument("--source", choices=SOURCES, default="base")
    parser.add_argument("--ref")
    parser.add_argument("--repo", default="entry")
    parser.add_argument("--path")
    parser.add_argument("--query")
    parser.add_argument("--ignore-case", action="store_true")
    parser.add_argument("--requests", help="JSON array or worklist file (inline JSON is also accepted)")
    args = parser.parse_args(argv)
    try:
        context = review_session.validate_session(args.context_dir)
        manifest = load_manifest(context)
        if args.action == "batch":
            result = batch(manifest, context, load_requests(args.requests))
        else:
            with operation_cache(manifest, context):
                result = request_result(manifest, context,
                                        {key: getattr(args, key) for key in REQUEST_FIELDS})
    except EVIDENCE_ERRORS as exc:
        print(f"release-evidence: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False,
                     **({"separators": (",", ":")} if args.action == "batch" else {"indent": 2})))
    return 2 if args.action == "batch" and not result["complete"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
