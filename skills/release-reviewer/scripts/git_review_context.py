#!/usr/bin/env python3
"""Capture explicit Git refs for a self-contained Release Reviewer session."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from datetime import datetime, timezone

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import git_support as git
import group_context
import review_session
import review_worklist
import frozen_evidence
import review_runtime
import merge_parent_coverage

SCHEMA = "ReleaseReviewContext/v3"
SUMMARY_SCHEMA = "ReleaseReviewCaptureSummary/v1"


def capture_summary(manifest: dict) -> dict:
    """Project saved metadata without changing the canonical manifest or evidence."""
    context = Path(manifest["context_dir"])
    worklist = json.loads((context / "review-worklist.json").read_text(encoding="utf-8"))
    identity = ("generated_at", "repo", "project", "report_root", "base_input", "head_input",
                "base_resolved_ref", "head_resolved_ref", "base_sha", "head_sha", "merge_base",
                "diff_base", "mode", "scope", "entry_repo", "repositories", "review_scope",
                "working_tree_unchanged", "fetches", "context_gaps", "context_complete",
                "context_dir", "git_timeout", "snapshot_policy", "capture_completed_at", "workspace_instructions", "diff_roots")
    preview = manifest["merge_preview"]
    files = {"manifest": "manifest.json", "worklist": "review-worklist.json",
             "draft": "review-result-draft.json", "diff": "diff.patch",
             "merge_output": "merge-output.txt"}
    if preview["status"] == "clean":
        files["release_diff"] = "release.patch"
    return {
        "schema": SUMMARY_SCHEMA, "context_schema": manifest["schema"],
        "skill_version": (Path(__file__).resolve().parents[1] / "VERSION").read_text(encoding="utf-8").strip(),
        **{key: manifest[key] for key in identity},
        "merge_preview": {key: preview[key] for key in ("status", "tree_sha")},
        "counts": {"repositories": len(manifest["repositories"]),
                   "changed_files": len(manifest["changed_files"]),
                   "release_changes": len(manifest["release_changes"]),
                   "merge_preview_changed_files": len(preview["changed_files"]),
                   "merge_commits": len(manifest["merge_commits"]),
                   "read_requests": len(worklist["requests"]),
                   "metadata_only": len(worklist["metadata_only"]),
                   "required_coverage": len(worklist["required_coverage"]),
                   "merge_parent_views": len(worklist["merge_parent_views"])},
        "files": {key: str(context / value) for key, value in files.items()},
    }


def capture_summary_v2(manifest: dict) -> dict:
    """Compact catalog; complete tree inventories remain in the manifest."""
    result = capture_summary(manifest)
    result["schema"] = "ReleaseReviewCaptureSummary/v2"
    result["repositories"] = []
    for record in manifest["repositories"]:
        projected = {key: value for key, value in record.items() if key != "snapshot"}
        snapshot = record.get("snapshot")
        if snapshot:
            projected["snapshot"] = {key: value for key, value in snapshot.items() if key != "sources"}
            projected["sources"] = {ref: {"tree": source["tree"], "file_count": len(source["entries"])}
                                    for ref, source in snapshot["sources"].items()}
        result["repositories"].append(projected)
    return result


def worktree_fingerprint(repo: Path) -> str:
    """Hash the index, HEAD and saved files without following file symlinks."""
    digest = hashlib.sha256()
    digest.update(str(git.run_git(repo, ["rev-parse", "HEAD"])).encode())
    index = git.git_path(repo, "index")
    digest.update(index.read_bytes() if index.exists() else b"no-index")
    tracked = set(str(git.run_git(repo, ["ls-files", "-z"])).split("\0"))
    paths = str(git.run_git(repo, ["ls-files", "-co", "--exclude-standard", "-z"]))
    for relative in sorted(set(paths.split("\0")) - {""}):
        if relative.startswith("review-reports/") and relative not in tracked:
            continue
        path = repo / relative
        digest.update(relative.encode("utf-8", errors="surrogateescape") + b"\0")
        if path.is_symlink():
            digest.update(os.readlink(path).encode("utf-8", errors="surrogateescape"))
        elif path.is_file():
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        elif path.is_dir():
            digest.update(b"directory-or-gitlink")
        else:
            digest.update(b"missing")
        digest.update(b"\0")
    return digest.hexdigest()


def build_manifest(args: argparse.Namespace) -> dict:
    capture_started_at = datetime.now(timezone.utc).isoformat()
    timeout = args.git_timeout
    if not math.isfinite(timeout) or timeout <= 0:
        raise git.ReviewContextError("--git-timeout 必須是正數。")
    token = git.GIT_TIMEOUT_SECONDS.set(timeout)
    session = None
    succeeded = False
    try:
        raw_workspace = Path(args.workspace).expanduser().absolute()
        if raw_workspace.exists() and group_context.is_link(raw_workspace):
            raise git.ReviewContextError("工作區根目錄不能是連結。")
        workspace = git.normalize_path(args.workspace)
        workspace_file = git.normalize_path(args.workspace_file) if args.workspace_file else None
        if workspace_file and not workspace_file.is_file():
            raise git.ReviewContextError(f"找不到 workspace：{workspace_file}")
        if not workspace.is_dir():
            raise git.ReviewContextError("無法辨識工作區報告根目錄；請確認目前 VSCode 工作區。")
        repositories, discovery_gaps = group_context.discover(workspace, workspace_file)
        repo = group_context.choose(repositories, workspace, args.project)
        report_root = repo / "review-reports"
        catalog, catalog_gaps = group_context.catalog(repositories, repo, args.workspace_context_json)
        # Validate both namespaces before any remote operation. Tags/revisions never fetch.
        base_ref = group_context.branch_ref(repo, args.base)
        head_ref = group_context.branch_ref(repo, args.head)
        if git.is_shallow_repository(repo):
            raise git.ReviewContextError("合併前審查需要完整歷史；請先取得完整歷史。")
        for label, raw, reference in (("基礎版本", args.base, base_ref), ("比較版本", args.head, head_ref)):
            if git.remote_target_for_ref(repo, raw) is None and not git.verified_commit(repo, reference):
                raise git.ReviewContextError(f"找不到{label}（本機分支）：{raw}；不會連線或改用其他同名版本。")
        session = review_session.create_session(args.context_dir)
        workspace_instructions = frozen_evidence.capture_workspace_instructions(
            session, git.workspace_roots(workspace, workspace_file), repositories)
        before = worktree_fingerprint(repo)
        fetches = git.fetch_inputs(repo, [args.base, args.head], True)
        base, head = git.verified_commit(repo, base_ref), git.verified_commit(repo, head_ref)
        for label, sha, raw in (("基礎版本", base, args.base), ("比較版本", head, args.head)):
            if not sha:
                raise git.ReviewContextError(f"找不到{label}：{raw}；不會改用其他同名版本。")
        if git.is_shallow_repository(repo):
            raise git.ReviewContextError("合併前審查需要完整歷史；請先取得完整歷史。")
        code, output, error = git.run_git_capture(repo, ["merge-base", "--all", base, head])
        if code not in (0, 1):
            raise git.ReviewContextError(f"無法取得共同起點：{error}")
        ancestors = output.splitlines()
        if len(ancestors) != 1:
            raise git.ReviewContextError("合併前審查需要唯一共同起點；不會自動改用直接比較。")
        merge_base = ancestors[0] if len(ancestors) == 1 else None
        diff_base = merge_base
        changes = git.collect_changes(repo, diff_base, head)
        (session / "diff.patch").write_bytes(git.run_git(
            repo, ["diff", *git.DIFF_FLAGS, "--binary", "--find-renames",
                   "--find-copies", diff_base, head, "--"], binary=True))
        gaps = discovery_gaps + catalog_gaps
        # New objects are written only inside the owned temporary session.
        preview, preview_env = git.create_merge_preview(repo, base, head, merge_base, session)
        preview.pop("env", None)
        preview.pop("manifest_info", None)
        preview["object_directory"] = "objects"
        (session / "merge-output.txt").write_text(preview["detail"], encoding="utf-8")
        if preview["status"] != "clean":
            gaps.append("無法取得乾淨的合併預覽：" + preview["status"])
            release_changes = []
        else:
            release_changes = git.collect_changes(repo, base, preview["tree_sha"], env=preview_env)
            (session / "release.patch").write_bytes(git.run_git(
                repo, ["diff", *git.DIFF_FLAGS, "--binary", "--find-renames",
                       "--find-copies", base, preview["tree_sha"], "--"],
                binary=True, env=preview_env))
        all_changes = changes + release_changes + (preview or {}).get("changed_files", [])
        for item in all_changes:
            if "160000" in (item.get("old_mode"), item.get("new_mode")):
                gaps.append(f"僅取得 submodule 版本指標，未審查內部內容：{item['path']}")
        unchanged = before == worktree_fingerprint(repo)
        if not unchanged:
            gaps.append("擷取期間偵測到本機工作檔、HEAD 或暫存區異動。")
        merge_commits = git.merge_commit_records(repo, diff_base, head)
        entry_refs = [base, head, merge_base] + [p for m in merge_commits for p in m["parents"]]
        if preview.get("tree_sha"):
            entry_refs.append(preview["tree_sha"])
        frozen_evidence.seal(session, catalog[0], entry_refs, env=preview_env)
        for item in catalog[1:]:
            if item["status"] != "captured":
                continue
            try:
                frozen_evidence.seal(session, item, [item["ref"]])
            except (OSError, ValueError, git.ReviewContextError) as exc:
                item.update(status="unavailable", reason=str(exc), ref=None)
                item.pop("snapshot", None)
                item.pop("instructions", None)
                gaps.append(f"{item['name']} 關聯快照未完成：{exc}")
        manifest = {
            "schema": SCHEMA,
            "generated_at": capture_started_at,
            "repo": str(repo), "project": repo.name,
            "report_root": str(report_root),
            "base_input": args.base, "head_input": args.head,
            "base_resolved_ref": base_ref, "head_resolved_ref": head_ref,
            "base_sha": base, "head_sha": head, "merge_base": merge_base,
            "diff_base": diff_base, "mode": "merge",
            "scope": "group" if len(repositories) > 1 or not git.has_git_marker(workspace) else "repository",
            "entry_repo": "entry", "repositories": catalog,
            "review_scope": "committed", "working_tree_unchanged": unchanged,
            "fetches": fetches, "changed_files": changes,
            "release_changes": release_changes, "merge_preview": preview,
            "merge_commits": merge_commits,
            "context_gaps": sorted(set(gaps)), "context_complete": not gaps,
            "context_dir": str(session), "git_timeout": timeout,
            "snapshot_policy": frozen_evidence.POLICY,
            "capture_completed_at": datetime.now(timezone.utc).isoformat(),
            "workspace_instructions": workspace_instructions,
        }
        manifest["diff_roots"] = review_worklist.diff_roots(manifest)
        manifest["merge_parent_policy"] = merge_parent_coverage.POLICY
        manifest["required_merge_views"] = merge_parent_coverage.required(
            release_changes if preview["status"] == "clean" else changes, merge_commits,
            {entry["path"] for entry in catalog[0]["snapshot"]["sources"][preview["tree_sha"] if preview["status"] == "clean" else head]["entries"] if entry["kind"] == "blob"})
        review_runtime.save_manifest(session, manifest)
        review_worklist.write_worklist(session, manifest)
        succeeded = True
        return manifest
    finally:
        git.GIT_TIMEOUT_SECONDS.reset(token)
        if session and not succeeded:
            review_session.cleanup_session(session)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--workspace-file")
    parser.add_argument("--project", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--workspace-context-json", type=json.loads, default={})
    parser.add_argument("--context-dir")
    parser.add_argument("--git-timeout", type=float, default=180.0)
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--summary-v2", action="store_true", help="Compact catalog without full tree inventories")
    parser.add_argument("--summary", action="store_true",
                        help="Return capture identity, scope counts and saved evidence paths; keep the full manifest on disk")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        result = build_manifest(args)
    except (OSError, ValueError, git.ReviewContextError) as exc:
        print(f"release-reviewer: {exc}", file=sys.stderr)
        return 2
    if args.summary or args.summary_v2:
        try:
            result = capture_summary_v2(result) if args.summary_v2 else capture_summary(result)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            review_session.cleanup_session(Path(result["context_dir"]))
            print(f"release-reviewer: {exc}", file=sys.stderr)
            return 2
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
