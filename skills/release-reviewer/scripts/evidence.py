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
import time

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import review_session
import frozen_evidence
import review_runtime

SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
SEARCH_FLAGS = ("grep", "--no-textconv", "--full-name", "-I", "-n", "-z", "-F")
_CACHE = ContextVar("release_evidence_operation", default=None)
_METRICS = ContextVar("release_evidence_metrics", default=None)
_NO_RAW_OVERRIDE = object()


@contextmanager
def operation_cache(manifest: dict, context: Path, *, persistent=False):
    """Share immutable Git evidence only within one batch or validation operation.

    A nested operation receives its own cache. All public readers still validate
    source, Repo, SHA and path before consulting cached data.
    """
    state = {"manifest": manifest, "context": Path(context),
             "sources": {}, "blobs": {}, "metadata": {}, "searches": {}, "path_indexes": {},
             "persistent": persistent, "search_hits": 0, "search_runs": 0,
             "metrics": {"git_commands": 0, "git_seconds": 0.0, "git_output_bytes": 0,
                         "batched_blobs": 0}}
    token = _CACHE.set(state)
    metric_token = _METRICS.set(state["metrics"])
    try:
        yield state
    finally:
        _METRICS.reset(metric_token)
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


def load_manifest(context: Path, *, full=True) -> dict:
    """Keep the legacy full check; metadata mode never asserts source integrity."""
    context = review_session.validate_session(context)
    path = context / "manifest.json"
    require(not path.is_symlink(), "manifest 不能是符號連結。")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(manifest, dict) and manifest.get("schema") == "ReleaseReviewContext/v3",
            "context schema 無效。")
    require(Path(manifest["context_dir"]).resolve() == context, "context 目錄與 manifest 不符。")
    report_root = manifest.get("report_root")
    require(isinstance(report_root, str) and Path(report_root).is_absolute(), "缺少工作區報告根目錄。")
    if manifest.get("snapshot_policy") is not None:
        require(manifest["snapshot_policy"] == frozen_evidence.POLICY, "不支援的固定快照政策。")
        expected_root = Path(manifest["repo"]) / "review-reports"
        require(Path(report_root) == expected_root and expected_root.resolve() == expected_root,
                "報告目錄必須位於受審 Repo/review-reports。")
        require(Path(manifest["repo"]).is_absolute(), "受審 Repo 路徑無效。")
        require(isinstance(manifest.get("capture_completed_at"), str), "缺少快照完成時間。")
        frozen_evidence.validate_workspace_instructions(context, manifest.get("workspace_instructions", []))
    else:
        require(Path(report_root).is_dir() and Path(report_root).resolve() == Path(report_root),
                "工作區報告根目錄不存在或已改指其他位置。")
    for field in ("base_sha", "head_sha", "diff_base"):
        require(isinstance(manifest.get(field), str) and SHA.fullmatch(manifest[field]), f"{field} 無效。")
    require(manifest.get("mode") == "merge", "只支援合併前審查。")
    require(manifest.get("entry_repo") == "entry" and manifest.get("scope") in {"group", "repository"},
            "工作區身分無效。")
    repositories = manifest.get("repositories")
    require(isinstance(repositories, list) and bool(repositories), "Repo catalog 無效。")
    validation_policy = manifest.get("validation_policy")
    require(validation_policy is None or validation_policy == frozen_evidence.RECEIPT_POLICY,
            "不支援的快照驗證收據政策。")
    require(manifest.get("snapshot_policy") is not None or not any(
        isinstance(item, dict) and item.get("snapshot") is not None for item in repositories),
        "自足快照不能移除固定政策而降級為舊來源。")
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
        if manifest.get("snapshot_policy") is not None and item["status"] == "captured":
            if validation_policy is not None:
                receipt = item.get("validation_receipt")
                require(isinstance(receipt, dict) and receipt.get("schema") == validation_policy,
                        "固定快照缺少驗證收據。")
            if full:
                frozen_evidence.validate(context, item)
            refs = ({manifest["base_sha"], manifest["head_sha"], manifest["diff_base"]}
                    if item["id"] == "entry" else {item["ref"]})
            if item["id"] == "entry":
                refs.update(p for m in manifest["merge_commits"] for p in m["parents"])
                if manifest["merge_preview"].get("tree_sha"):
                    refs.add(manifest["merge_preview"]["tree_sha"])
            require(refs <= set(item["snapshot"]["sources"]), "快照缺少必要固定版本。")
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
    import coverage_policy
    if full:
        coverage_policy.validate(manifest, context)
    else:
        coverage_policy.ancestor_noop(manifest)
    return manifest


def repository(manifest: dict, identifier: str | None = None) -> dict:
    identifier = identifier or "entry"
    matches = [r for r in manifest["repositories"] if r["id"] == identifier]
    require(len(matches) == 1, "證據 Repo 不在固定 catalog。")
    return matches[0]


def source_version(manifest: dict, context: Path, source: str, ref: str | None = None, repo=None):
    selected = repository(manifest, repo)
    require(manifest.get("snapshot_policy") is not None or selected.get("snapshot") is None,
            "自足快照不能降級為可變來源。")
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
        if manifest.get("snapshot_policy") is None:
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
    if manifest.get("snapshot_policy") is not None:
        require(manifest["snapshot_policy"] == frozen_evidence.POLICY, "不支援的固定快照政策。")
        env = frozen_evidence.environment(context, selected)
        require(reference in selected["snapshot"]["sources"], "證據版本不在自足快照內。")
        return reference, env
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


def command(manifest: dict, args: list[str], *, env=None, acceptable=(0,), input_bytes=None) -> bytes:
    import review_coordinator
    active = review_coordinator.ACTIVE.get()
    if active:
        active.check()
    timeout = manifest.get("git_timeout", 180.0)
    require(isinstance(timeout, (int, float)) and math.isfinite(timeout) and timeout > 0, "Git 逾時無效。")
    location = env["GIT_DIR"] if env and manifest.get("snapshot_policy") is not None else manifest["repo"]
    command_env = dict(env) if env else {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
    command_env.update(GIT_NO_LAZY_FETCH="1", GIT_ALLOW_PROTOCOL="")
    metrics = _METRICS.get()
    began = time.perf_counter()
    try:
        result = subprocess.run(["git", "-C", str(location), *args], capture_output=True, input=input_bytes,
                                timeout=timeout, env=command_env)
    finally:
        if metrics is not None:
            metrics["git_commands"] += 1
            metrics["git_seconds"] += time.perf_counter() - began
    if metrics is not None:
        metrics["git_output_bytes"] += len(result.stdout) + len(result.stderr)
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


def _search_identity(manifest, context, source, query, ignore_case, ref, repo):
    reference, env = source_version(manifest, context, source, ref, repo)
    selected_repo = repository(manifest, repo)
    effective = dict(env) if env else dict(os.environ)
    effective.update(GIT_NO_LAZY_FETCH="1", GIT_ALLOW_PROTOCOL="")
    environment_fingerprint = hashlib.sha256(json.dumps(
        {name: value for name, value in effective.items() if name.startswith("GIT_")},
        sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    flags = (*SEARCH_FLAGS, *(("-i",) if ignore_case else ()))
    key = (str(Path(context).resolve()), selected_repo["id"], selected_repo["path"], source, reference,
           query, bool(ignore_case), flags, environment_fingerprint, effective.get("GIT_DIR"),
           effective.get("GIT_OBJECT_DIRECTORY"), effective.get("GIT_ALTERNATE_OBJECT_DIRECTORIES"))
    state = _cache(manifest, context)
    persistent = bool(state is not None and state.get("persistent") and manifest.get("snapshot_policy"))
    snapshot = selected_repo.get("snapshot", {})
    identity = {"schema": "ReleaseSearchInput/v1", "repo": selected_repo["id"], "repo_path": selected_repo["path"], "source": source,
                "context": str(Path(context).resolve()), "git_dir": effective.get("GIT_DIR"),
                "git_environment_sha256": environment_fingerprint, "flags": list(flags),
                "ref": reference, "snapshot": {name: snapshot.get(name) for name in ("policy", "git_dir", "object_format")},
                "tree": snapshot.get("sources", {}).get(reference, {}).get("tree"),
                "query": query, "ignore_case": bool(ignore_case)}
    cache_key = hashlib.sha256(canonical(identity)).hexdigest()
    cache_file = context / "search-cache" / (cache_key + ".json")
    return reference, env, selected_repo, effective, flags, key, state, persistent, identity, cache_file


def search(manifest: dict, context: Path, source: str, query: str, *, ignore_case=False, ref=None, repo=None,
           _raw_override=_NO_RAW_OVERRIDE) -> dict:
    require(isinstance(query, str) and query and "\0" not in query and "\n" not in query,
            "搜尋字串須為非空白的單行 literal。")
    (reference, env, selected_repo, effective, flags, key, state, persistent,
     identity, cache_file) = _search_identity(manifest, context, source, query, ignore_case, ref, repo)
    if state is not None and key in state["searches"]:
        cached = state["searches"][key]
        return {**cached, "matches": [match.copy() for match in cached["matches"]]}
    if persistent:
        try:
            require(cache_file.is_file(), "Search cache miss.")
            require(not review_runtime.path_is_symlink(cache_file)
                    and not review_runtime.path_is_symlink(cache_file.parent), "Linked search cache.")
            envelope = json.loads(review_runtime.path_read_text(cache_file, encoding="utf-8"))
            cached = review_runtime.load_view(context, envelope["artifact"])
            require(cached["identity"] == identity, "Search cache source differs.")
            result = cached["result"]
            expected = {"repo": selected_repo["id"], "source": source, "ref": reference,
                        "query": query, "ignore_case": bool(ignore_case), "scope": "repo"}
            require(isinstance(result, dict) and all(result.get(name) == value for name, value in expected.items())
                    and type(result.get("ignore_case")) is bool
                    and isinstance(result.get("matches"), list)
                    and all(isinstance(match, dict) and isinstance(match.get("path"), str)
                            and type(match.get("line")) is int and match["line"] > 0
                            and isinstance(match.get("text"), str) for match in result["matches"]),
                    "Search cache is malformed.")
            state["searches"][key] = result
            state["search_hits"] += 1
            return {**result, "matches": [match.copy() for match in result["matches"]]}
        except (OSError, ValueError, KeyError, TypeError):
            # This optional accelerator is rebuilt; authoritative search records
            # remain unchanged and publication independently replays them.
            pass
    if _raw_override is _NO_RAW_OVERRIDE:
        args = list(flags)
        args += ["-e", query, reference, "--"]
        selected = {**manifest, "repo": selected_repo["path"]}
        raw = command(selected, args, env=env, acceptable=(0, 1))
    else:
        raw = _raw_override
    if state is not None and _raw_override is _NO_RAW_OVERRIDE:
        state["search_runs"] += 1
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
    if persistent:
        import review_coordinator
        try:
            review_coordinator.plain(cache_file.parent)
            review_runtime.path_mkdir(cache_file.parent, exist_ok=True)
            artifact = review_runtime.store_view(context, {"identity": identity, "result": result})
            review_coordinator.atomic(cache_file, {"artifact": artifact})
        except (OSError, ValueError):
            # Only disposable transport-cache writes are optional. The caller
            # still saves and later verifies the authoritative search record.
            pass
    return result


def search_many(manifest: dict, context: Path, source: str, queries: list[str], *, ref=None, repo=None) -> list[dict]:
    """Batch consecutive case-sensitive literal searches without changing ledgers."""
    require(isinstance(queries, list) and queries, "search_many requires a nonempty query array.")
    require(all(isinstance(query, str) and query and "\0" not in query and "\n" not in query
                for query in queries), "Each search query must be a nonempty single-line literal.")
    unique = list(dict.fromkeys(queries))
    if len(unique) > 32 or sum(len(query.encode("utf-8")) + 3 for query in unique) > 8192:
        return [search(manifest, context, source, query, ref=ref, repo=repo) for query in queries]

    # Reuse the exact prior cache path/identity rules. If any query might hit
    # either cache, let the existing serial path load it without a Git command.
    for query in unique:
        (_reference, _env, _selected, _effective, _flags, key, state,
         persistent, _identity, cache_file) = _search_identity(
             manifest, context, source, query, False, ref, repo)
        if ((state is not None and key in state["searches"])
                or (persistent and cache_file.exists())):
            return [search(manifest, context, source, item, ref=ref, repo=repo) for item in queries]

    reference, env = source_version(manifest, context, source, ref, repo)
    selected_repo = repository(manifest, repo)
    # When one literal is contained in every query, it is a complete superset
    # search by itself. Avoid asking Git to evaluate redundant patterns (which
    # can be much slower on dense results), then partition its rows below.
    covering_query = min((candidate for candidate in unique
                          if all(candidate in query for query in unique)),
                         key=len, default=None)
    args = list(SEARCH_FLAGS)
    for query in ([covering_query] if covering_query is not None else unique):
        args.extend(("-e", query))
    args.extend((reference, "--"))
    raw = command({**manifest, "repo": selected_repo["path"]}, args, env=env, acceptable=(0, 1))
    state = _cache(manifest, context)
    if state is not None:
        state["search_runs"] += 1
    prefix = reference.encode() + b":"
    rows = []
    offset = 0
    while offset < len(raw):
        filename_end = raw.find(b"\0", offset)
        require(filename_end >= 0, "Git grep output is incomplete.")
        number_end = raw.find(b"\0", filename_end + 1)
        require(number_end >= 0, "Git grep output is incomplete.")
        line_end = raw.find(b"\n", number_end + 1)
        record_end = len(raw) if line_end < 0 else line_end + 1
        filename = raw[offset:filename_end]
        number = raw[filename_end + 1:number_end]
        line = raw[number_end + 1:len(raw) if line_end < 0 else line_end]
        require(filename.startswith(prefix) and number.isdigit(), "Git grep output identity is invalid.")
        rows.append((raw[offset:record_end], line))
        offset = record_end

    by_query = {}
    for query in unique:
        literal = query.encode("utf-8")
        selected = b"".join(record for record, line in rows if literal in line)
        by_query[query] = search(manifest, context, source, query, ref=ref, repo=repo,
                                 _raw_override=selected)
    return [{**by_query[query], "matches": [match.copy() for match in by_query[query]["matches"]]}
            for query in queries]


def record_search(context: Path, result: dict) -> str:
    identifier = hashlib.sha256(canonical(result)).hexdigest()
    directory = context / "searches"
    require(not review_runtime.path_is_symlink(directory), "搜尋紀錄目錄不能是符號連結。")
    review_runtime.path_mkdir(directory, exist_ok=True)
    path = directory / f"{identifier}.json"
    require(not review_runtime.path_is_symlink(path), "搜尋紀錄不能是符號連結。")
    raw = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if review_runtime.path_exists(path):
        require(review_runtime.path_read_text(path, encoding="utf-8") == raw,
                "搜尋紀錄 ID 已存在但內容不同。")
    else:
        try:
            review_runtime.path_write_text(path, raw, encoding="utf-8", exclusive=True)
        except FileExistsError:
            require(not review_runtime.path_is_symlink(path)
                    and review_runtime.path_read_text(path, encoding="utf-8") == raw,
                    "搜尋紀錄 ID 已存在但內容不同。")
    return identifier


def validate_search(context: Path, manifest: dict, identifier: str, *, source="base", repo="entry") -> dict:
    require(isinstance(identifier, str) and re.fullmatch(r"[a-f0-9]{64}", identifier), "基礎搜尋紀錄 ID 無效。")
    directory = context / "searches"
    path = directory / f"{identifier}.json"
    require(not review_runtime.path_is_symlink(directory)
            and not review_runtime.path_is_symlink(path), "搜尋紀錄不能是符號連結。")
    record = json.loads(review_runtime.path_read_text(path, encoding="utf-8"))
    require(hashlib.sha256(canonical(record)).hexdigest() == identifier, "基礎搜尋紀錄摘要不符。")
    fixed, _ = source_version(manifest, context, source,
                              ref=record.get("ref") if source == "merge-parent" else None, repo=repo)
    require(record.get("source") == source and record.get("ref") == fixed
            and record.get("repo") == repo and record.get("scope") == "repo", "判定必須搜尋基礎版本整個 Repo 或指定關聯固定 Repo。")
    current = search(manifest, context, source, record["query"], ignore_case=record["ignore_case"],
                     ref=record.get("ref") if source == "merge-parent" else None, repo=repo)
    require(record == current, "基礎搜尋紀錄與固定來源不符。")
    return record


def validate_baseline_search(context: Path, manifest: dict, identifier: str) -> dict:
    return validate_search(context, manifest, identifier)


def validate_recorded_searches(context: Path, manifest: dict):
    """Replay the authoritative ledger with the fresh publication-local cache."""
    directory = context / "searches"
    require(not review_runtime.path_is_symlink(directory), "搜尋紀錄目錄不能是符號連結。")
    if not review_runtime.path_exists(directory):
        return
    for path in sorted(review_runtime.path_children(directory, suffix=".json")):
        require(not review_runtime.path_is_symlink(path), "搜尋紀錄不能是符號連結。")
        record = json.loads(review_runtime.path_read_text(path, encoding="utf-8"))
        validate_search(context, manifest, path.stem, source=record.get("source"), repo=record.get("repo"))


SOURCES = ("base", "head", "merge-base", "merge-preview", "merge-parent", "supporting")
REQUEST_FIELDS = {"action", "source", "ref", "repo", "path", "query", "ignore_case",
                  "line_start", "line_end", "line_count"}
EVIDENCE_ERRORS = (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired)


def request_result(manifest: dict, context: Path, request: dict, *, contents=None) -> dict:
    """Validate one request, optionally sharing its read body within a batch."""
    require(isinstance(request, dict), "batch request 必須是物件。")
    require(not request.keys() - REQUEST_FIELDS, "batch request 含未知欄位。")
    action = request.get("action")
    require(action in ("list", "read", "read-window", "search"), "未知的證據操作。")
    has_line_range = "line_start" in request or "line_end" in request
    require(not has_line_range or action in ("read", "read-window"),
            "line_start／line_end 僅適用於 read 或 read-window。")
    bounded = action == "read" and has_line_range
    windowed = action == "read-window"
    if windowed:
        start, count = request.get("line_start"), request.get("line_count")
        require(type(start) is int and start >= 1,
                "read-window line_start must be an integer of at least 1.")
        require(type(count) is int and 1 <= count <= 1000,
                "read-window line_count must be between 1 and 1000; reduce the window size.")
        require("line_end" not in request, "read-window does not accept line_end; use line_count.")
    elif "line_count" in request:
        require(False, "line_count is only valid for read-window.")
    if bounded:
        require(action == "read", "line_start／line_end 僅適用於 read。")
        start, end = request.get("line_start"), request.get("line_end")
        require(type(start) is int and type(end) is int,
                "line_start／line_end 須成對提供，且必須是整數。")
        require(1 <= start <= end, "行段須符合 1 <= line_start <= line_end。")
    source, repo = request.get("source", "base"), request.get("repo", "entry")
    require(source in SOURCES, "未知的證據來源。")
    require(isinstance(repo, str) and bool(repo), "證據 Repo 必須是非空白身分。")
    ref, path = request.get("ref"), request.get("path")
    ignore_case = request.get("ignore_case", False)
    require(type(ignore_case) is bool, "ignore_case 必須是布林值。")
    if action == "list":
        reference, paths = _list_files(manifest, context, source, ref, repo)
        return {"source": source, "ref": reference, "repo": repo, "paths": paths}
    if action in ("read", "read-window"):
        reference, _, content = _read_blob(manifest, context, source, path, ref, repo)
        require(b"\0" not in content, "二進位內容請列為 metadata-only，不能當作文字讀取。")
        identity = {"repo": repo, "source": source, "ref": reference, "path": path}
        if windowed:
            lines = text_lines(content)
            start, count = request["line_start"], request["line_count"]
            selected = lines[start - 1:start - 1 + count]
            end = start + len(selected) - 1 if selected else None
            next_start = end + 1 if end is not None and end < len(lines) else None
            result = {**identity, "content_sha256": hashlib.sha256(content).hexdigest(),
                      "line_start": start, "line_end": end, "line_count": len(selected),
                      "requested_line_count": count,
                      "total_lines": len(lines), "lines": selected,
                      "next_line_start": next_start}
            if next_start is not None:
                result["next_window"] = {"op": "read-window", "repo": repo, "source": source,
                                         "ref": reference, "path": path,
                                         "line_start": next_start, "line_count": count}
            return result
        if bounded:
            lines = text_lines(content)
            require(end <= len(lines), f"line_end 超過固定檔案總行數：{len(lines)}。")
            return {**identity, "content_sha256": hashlib.sha256(content).hexdigest(),
                    "line_start": start, "line_end": end, "total_lines": len(lines),
                    "lines": lines[start - 1:end]}
        if contents is not None:
            content_id = hashlib.sha256(content).hexdigest()
            if content_id not in contents:
                contents[content_id] = {"lines": text_lines(content)}
            return {**identity, "content_id": content_id}
        return {**identity, "lines": text_lines(content)}
    result = search(manifest, context, source, request.get("query"),
                    ignore_case=ignore_case, ref=ref, repo=repo)
    result["search_id"] = record_search(context, result)
    return result


def text_lines(content: bytes) -> list[dict]:
    return [{"line": n, "text": line} for n, line in
            enumerate(content.decode("utf-8", errors="replace").splitlines(), 1)]


def compact_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _normal_batch(shared: dict) -> dict:
    """Expand only read wrappers; keep bodies and search trees shared in memory."""
    results = []
    for item in shared["results"]:
        result = item.get("result", {})
        if item["ok"] and "content_id" in result:
            identity = {key: value for key, value in result.items() if key != "content_id"}
            results.append({**item, "result": {**identity, **shared["contents"][result["content_id"]]}})
        else:
            results.append(item)
    return {"results": results, "complete": shared["complete"]}


def batch(manifest: dict, context: Path, requests: list, *, shared_content=False,
          auto_content=False) -> dict:
    """Keep input order and expose every failure while sharing operation-local data."""
    require(isinstance(requests, list), "batch requests 必須是 JSON 陣列。")
    require(not (shared_content and auto_content), "shared_content 與 auto_content 不可同時使用。")
    results = []
    contents = {} if shared_content or auto_content else None
    with operation_cache(manifest, context):
        _prefetch_reads(manifest, context, requests)
        index = 0
        while index < len(requests):
            request = requests[index]
            group_key = _literal_search_group_key(request)
            end = index + 1
            if group_key is not None:
                while end < len(requests) and _literal_search_group_key(requests[end]) == group_key:
                    end += 1
            if group_key is not None and end - index > 1:
                try:
                    searched = search_many(
                        manifest, context, group_key[0],
                        [requests[position].get("query") for position in range(index, end)],
                        ref=group_key[1], repo=group_key[2])
                except EVIDENCE_ERRORS:
                    # Preserve the established per-operation errors and allow
                    # valid queries around one malformed query to succeed.
                    searched = None
                if searched is not None:
                    for offset, result in enumerate(searched):
                        try:
                            result["search_id"] = record_search(context, result)
                        except EVIDENCE_ERRORS as exc:
                            results.append({"index": index + offset, "ok": False, "error": str(exc)})
                        else:
                            results.append({"index": index + offset, "ok": True, "result": result})
                    index = end
                    continue
            try:
                result = request_result(manifest, context, request, contents=contents)
            except EVIDENCE_ERRORS as exc:
                results.append({"index": index, "ok": False, "error": str(exc)})
            else:
                results.append({"index": index, "ok": True, "result": result})
            index += 1
    result = {"results": results, "complete": all(item["ok"] for item in results)}
    if shared_content or auto_content:
        result.update(schema="ReleaseReviewEvidenceBatch/v1", contents=contents)
    if auto_content:
        normal = _normal_batch(result)
        # Compare the exact CLI wire encoding. A tie uses the original shape.
        if len(compact_json(normal).encode("utf-8")) <= len(compact_json(result).encode("utf-8")):
            return normal
    return result


def _literal_search_group_key(request):
    """Return the common source identity only for compatible literal searches."""
    if not isinstance(request, dict) or request.get("action") != "search":
        return None
    allowed = {"action", "source", "ref", "repo", "query", "ignore_case"}
    if request.keys() - allowed or request.get("ignore_case", False) is not False:
        return None
    return (request.get("source", "base"), request.get("ref"), request.get("repo", "entry"))


def _prefetch_reads(manifest, context, requests):
    """Batch frozen blob transport; per-request validation/errors remain intact."""
    state = _cache(manifest, context)
    if state is None or manifest.get("snapshot_policy") is None:
        return
    groups = {}
    for request in requests:
        if not isinstance(request, dict) or request.get("action") not in ("read", "read-window"):
            continue
        path = request.get("path")
        if not safe_path(path) or "\n" in path or "\r" in path:
            continue
        try:
            source = request.get("source", "base")
            reference, env = source_version(manifest, context, source, request.get("ref"), request.get("repo"))
            selected = repository(manifest, request.get("repo"))
            index_key = (selected["id"], reference)
            if index_key not in state["path_indexes"]:
                entries = selected["snapshot"]["sources"][reference]["entries"]
                state["path_indexes"][index_key] = {entry["path"]: entry for entry in entries}
            found = state["path_indexes"][index_key].get(path)
            require(found is not None and found["kind"] == "blob", "path is not a fixed blob")
            key = (selected["id"], selected["path"], reference, path,
                   env.get("GIT_OBJECT_DIRECTORY"), env.get("GIT_ALTERNATE_OBJECT_DIRECTORIES"))
            if key in state["blobs"]:
                continue
            environment_key = (selected["id"], env.get("GIT_DIR"), env.get("GIT_OBJECT_DIRECTORY"),
                               env.get("GIT_ALTERNATE_OBJECT_DIRECTORIES"))
            group = groups.setdefault(environment_key,
                                      {"repo": selected, "env": env, "items": [], "keys": []})
            # The fixed tree inventory already binds each path to its blob OID.
            # Read each distinct object only once even across refs and paths.
            oid = found["object"]
            group["items"].append((oid, oid))
            group["keys"].append((key, oid))
        except EVIDENCE_ERRORS:
            continue  # The ordinary request path records this item's error.
    for group in groups.values():
        selected = group["repo"]
        try:
            bodies = review_runtime.batch_blobs(lambda data: command({**manifest, "repo": selected["path"]},
                ["cat-file", "--batch"], env=group["env"], input_bytes=data), group["items"], selected["snapshot"]["object_format"])
        except EVIDENCE_ERRORS:
            continue
        for key, expression in group["keys"]:
            state["blobs"][key] = bodies[expression]
        state["metrics"]["batched_blobs"] += len(bodies)


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
    parser.add_argument("action", choices=("list", "read", "search", "batch", "view"))
    parser.add_argument("--context-dir", required=True)
    parser.add_argument("--source", choices=SOURCES, default="base")
    parser.add_argument("--ref")
    parser.add_argument("--repo", default="entry")
    parser.add_argument("--path")
    parser.add_argument("--line-start", type=int, help="First fixed line (1-based; requires --line-end)")
    parser.add_argument("--line-end", type=int, help="Last fixed line (inclusive; requires --line-start)")
    parser.add_argument("--query")
    parser.add_argument("--ignore-case", action="store_true")
    parser.add_argument("--requests", help="JSON array or worklist file (inline JSON is also accepted)")
    parser.add_argument("--page-size", type=int)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--artifact")
    parser.add_argument("--search-id", action="append", help="Select a saved 64-hex search hash for view; repeatable")
    formats = parser.add_mutually_exclusive_group()
    formats.add_argument("--shared-content", action="store_true",
                         help="Batch only: emit a versioned wrapper with each unique read body once")
    formats.add_argument("--auto-content", action="store_true",
                         help="Batch only: choose the smaller compact UTF-8 normal/shared representation")
    args = parser.parse_args(argv)
    if (args.shared_content or args.auto_content) and args.action != "batch":
        parser.error("--shared-content／--auto-content 僅適用於 batch。")
    if (args.line_start is not None or args.line_end is not None) and args.action != "read":
        parser.error("--line-start／--line-end 僅適用於 read；batch 請使用 JSON 欄位。")
    if args.search_id is not None and args.action != "view":
        parser.error("--search-id 僅適用於 view。")
    try:
        context = review_session.validate_session(args.context_dir)
        manifest = load_manifest(context, full=args.action != "view")
        if args.action == "view":
            require(args.artifact and args.page_size, "view needs --artifact and --page-size")
            saved = review_runtime.load_view(context, args.artifact)
            if args.search_id is not None:
                saved = review_runtime.select_searches(saved, args.search_id)
            result = review_runtime.page(saved, context,
                                         size=args.page_size, offset=args.offset, artifact=args.artifact)
        elif args.action == "batch":
            result = batch(manifest, context, load_requests(args.requests),
                           shared_content=args.shared_content, auto_content=args.auto_content)
        else:
            with operation_cache(manifest, context):
                result = request_result(manifest, context,
                                         {key: getattr(args, key) for key in REQUEST_FIELDS
                                          if hasattr(args, key) and
                                          (key not in {"line_start", "line_end", "line_count"} or
                                           getattr(args, key) is not None)})
        failed = args.action == "batch" and not result["complete"]
        if args.page_size and args.action != "view":
            result = review_runtime.page(result, context, size=args.page_size, offset=args.offset)
    except EVIDENCE_ERRORS as exc:
        print(f"release-evidence: {exc}", file=sys.stderr)
        return 2
    print(compact_json(result) if args.action == "batch" or args.page_size else json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
