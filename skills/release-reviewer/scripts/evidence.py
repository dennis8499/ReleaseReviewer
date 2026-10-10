#!/usr/bin/env python3
"""Read and search frozen source versions, including the whole baseline Repo."""
from __future__ import annotations

import argparse
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
    require(isinstance(manifest, dict) and manifest.get("schema") == "ReleaseReviewContext/v1",
            "context schema 無效。")
    require(Path(manifest["context_dir"]).resolve() == context, "context 目錄與 manifest 不符。")
    for field in ("base_sha", "head_sha", "diff_base"):
        require(isinstance(manifest.get(field), str) and SHA.fullmatch(manifest[field]), f"{field} 無效。")
    require(manifest.get("mode") in {"merge", "direct"}, "比較模式無效。")
    require(type(manifest.get("context_complete")) is bool, "context_complete 無效。")
    require(isinstance(manifest.get("context_gaps"), list), "context_gaps 無效。")
    require(manifest["context_complete"] == (not manifest["context_gaps"]), "context 完整狀態不一致。")
    require(type(manifest.get("working_tree_unchanged")) is bool, "工作區狀態無效。")
    for field in ("changed_files", "release_changes", "merge_commits"):
        require(isinstance(manifest.get(field), list), f"{field} 無效。")
    preview = manifest.get("merge_preview")
    if manifest["mode"] == "merge":
        require(isinstance(preview, dict) and preview.get("status") in {"clean", "conflicts", "unavailable"},
                "合併預覽無效。")
        require(isinstance(preview.get("changed_files"), list), "合併預覽檔案清單無效。")
        require(preview["status"] == "clean" or not manifest["context_complete"],
                "有合併衝突或缺口時不能標為完整。")
    else:
        require(preview is None, "直接比較不應含合併預覽。")
    return manifest


def source_version(manifest: dict, context: Path, source: str, ref: str | None = None):
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
    # Do not inherit an unrelated session's alternate Git object store.
    for key in ("GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
        env.pop(key, None)
    fixed = {"base": manifest["base_sha"], "head": manifest["head_sha"],
             "merge-base": manifest.get("merge_base")}
    if source == "merge-preview":
        preview = manifest.get("merge_preview") or {}
        require(preview.get("status") == "clean", "沒有可用的乾淨合併預覽。")
        reference = preview.get("tree_sha")
        require(preview.get("object_directory") == "objects", "預覽物件目錄無效。")
        objects = context / "objects"
        require(objects.is_dir() and not objects.is_symlink() and objects.resolve().is_relative_to(context),
                "預覽物件目錄超出 context。")
        repo_objects = command(manifest, ["rev-parse", "--git-path", "objects"], env=env).decode().strip()
        alternate = Path(repo_objects)
        if not alternate.is_absolute():
            alternate = Path(manifest["repo"]) / alternate
        env["GIT_OBJECT_DIRECTORY"] = str(objects)
        env["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = str(alternate)
    elif source == "merge-parent":
        allowed = {p for m in manifest["merge_commits"] for p in m["parents"]}
        require(ref in allowed, "merge-parent 版本不在固定紀錄內。")
        reference = ref
    else:
        require(source in fixed, "未知的證據來源。")
        reference = fixed[source]
    require(isinstance(reference, str) and SHA.fullmatch(reference), "證據版本無效。")
    require(ref is None or ref == reference, "證據 SHA 與固定來源不符。")
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


def read_blob(manifest: dict, context: Path, source: str, path: str, ref=None) -> bytes:
    require(safe_path(path), "證據路徑必須是安全的 Repo 相對路徑。")
    reference, env = source_version(manifest, context, source, ref)
    return command(manifest, ["cat-file", "blob", f"{reference}:{path}"], env=env)


def list_files(manifest: dict, context: Path, source: str, ref=None) -> list[str]:
    reference, env = source_version(manifest, context, source, ref)
    raw = command(manifest, ["ls-tree", "-r", "--full-tree", "-z", "--name-only", reference], env=env)
    return [p.decode("utf-8", errors="surrogateescape") for p in raw.split(b"\0") if p]


def search(manifest: dict, context: Path, source: str, query: str, *, ignore_case=False, ref=None) -> dict:
    require(isinstance(query, str) and query and "\0" not in query and "\n" not in query,
            "搜尋字串須為非空白的單行 literal。")
    reference, env = source_version(manifest, context, source, ref)
    args = ["grep", "--no-textconv", "--full-name", "-I", "-n", "-z", "-F"]
    if ignore_case:
        args.append("-i")
    args += ["-e", query, reference, "--"]
    raw = command(manifest, args, env=env, acceptable=(0, 1))
    matches = []
    while raw:
        filename, separator, rest = raw.partition(b"\0")
        number, separator2, rest = rest.partition(b"\0")
        line, newline, raw = rest.partition(b"\n")
        require(separator and separator2 and number.isdigit(), "Git grep 回傳格式不完整。")
        prefix = reference.encode() + b":"
        require(filename.startswith(prefix), "搜尋結果版本不符。")
        matches.append({"path": filename[len(prefix):].decode("utf-8", errors="surrogateescape"),
                        "line": int(number), "text": line.decode("utf-8", errors="replace")})
        if not newline:
            break
    return {"source": source, "ref": reference, "query": query, "ignore_case": bool(ignore_case),
            "scope": "repo", "matches": matches}


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


def validate_baseline_search(context: Path, manifest: dict, identifier: str) -> dict:
    require(isinstance(identifier, str) and re.fullmatch(r"[a-f0-9]{64}", identifier), "基礎搜尋紀錄 ID 無效。")
    directory = context / "searches"
    path = directory / f"{identifier}.json"
    require(not directory.is_symlink() and not path.is_symlink(), "搜尋紀錄不能是符號連結。")
    record = json.loads(path.read_text(encoding="utf-8"))
    require(hashlib.sha256(canonical(record)).hexdigest() == identifier, "基礎搜尋紀錄摘要不符。")
    require(record.get("source") == "base" and record.get("ref") == manifest["base_sha"]
            and record.get("scope") == "repo", "新增判定必須搜尋基礎版本整個 Repo。")
    current = search(manifest, context, "base", record["query"], ignore_case=record["ignore_case"])
    require(record == current, "基礎搜尋紀錄與固定來源不符。")
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("list", "read", "search"))
    parser.add_argument("--context-dir", required=True)
    parser.add_argument("--source", choices=("base", "head", "merge-base", "merge-preview", "merge-parent"),
                        default="base")
    parser.add_argument("--ref")
    parser.add_argument("--path")
    parser.add_argument("--query")
    parser.add_argument("--ignore-case", action="store_true")
    args = parser.parse_args(argv)
    try:
        context = review_session.validate_session(args.context_dir)
        manifest = load_manifest(context)
        reference, _ = source_version(manifest, context, args.source, args.ref)
        if args.action == "list":
            result = {"source": args.source, "ref": reference,
                      "paths": list_files(manifest, context, args.source, args.ref)}
        elif args.action == "read":
            content = read_blob(manifest, context, args.source, args.path, args.ref)
            require(b"\0" not in content, "二進位內容請列為 metadata-only，不能當作文字讀取。")
            result = {"source": args.source, "ref": reference, "path": args.path,
                      "lines": [{"line": n, "text": line} for n, line in
                                enumerate(content.decode("utf-8", errors="replace").splitlines(), 1)]}
        else:
            result = search(manifest, context, args.source, args.query,
                            ignore_case=args.ignore_case, ref=args.ref)
            result["search_id"] = record_search(context, result)
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        print(f"release-evidence: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
