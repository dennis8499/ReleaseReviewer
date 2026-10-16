"""Plan fixed-source reads and seed an explicitly unfinished v3 review draft."""
from __future__ import annotations

import json
from pathlib import Path

import evidence as ev


def comparison_scopes(manifest):
    head = ("head", manifest["head_sha"])
    scopes = [(manifest["changed_files"], ("merge-base", manifest["diff_base"]), head)]
    preview = manifest.get("merge_preview") or {}
    if preview.get("status") == "clean":
        merged = ("merge-preview", preview["tree_sha"])
        scopes += [(preview["changed_files"], head, merged),
                   (manifest["release_changes"], ("base", manifest["base_sha"]), merged)]
    return scopes


def expected_coverage(manifest):
    expected = {}
    for changes, before, after in comparison_scopes(manifest):
        for change in changes:
            path = change["path"]
            ev.require(ev.safe_path(path), "變更清單包含無法安全引用的路徑。")
            pair = before if change["status"].startswith("D") else after
            expected.setdefault(path, set()).add(pair)
            old = change.get("old_path")
            if old:
                ev.require(ev.safe_path(old), "變更清單包含無法安全引用的舊路徑。")
                expected.setdefault(old, set()).add(before)
    return expected


def prepare(manifest):
    """Only infer identities and required sources, never semantic review status."""
    requests, metadata, visited = [], [], set()

    def add(change, pair, path, *, old=False):
        ev.require(ev.safe_path(path), "待讀清單包含無法安全引用的路徑。")
        source, ref = pair
        key = (source, ref, path)
        if key in visited:
            return
        visited.add(key)
        mode = change.get("old_mode" if old else "new_mode")
        request = {"action": "read", "repo": "entry", "source": source, "ref": ref, "path": path}
        if change.get("binary") or mode == "160000":
            metadata.append({**request, "reason": "二進位或 submodule，須保留內容審查缺口。"})
        else:
            requests.append(request)

    for changes, before, after in comparison_scopes(manifest):
        for change in changes:
            status = change["status"]
            if not status.startswith("A"):
                add(change, before, change.get("old_path") or change["path"], old=True)
            if not status.startswith("D"):
                add(change, after, change["path"])

    parent_views = []
    for merge in manifest["merge_commits"]:
        for view in merge["parent_views"]:
            parent_views.append({"commit": merge["sha"], "parent": view["sha"],
                                 "position": view["position"], "changes": view["changes"]})
            for change in view["changes"]:
                if not change["status"].startswith("A"):
                    add(change, ("merge-parent", view["sha"]), change.get("old_path") or change["path"], old=True)

    metadata_paths = {item["path"] for item in metadata}
    coverage = [{"path": path, "status": "not-reviewed", "reason": "尚未完成語意審查。",
                 "evidence": [] if path in metadata_paths else [
                     {"repo": "entry", "source": source, "ref": ref, "path": path}
                     for source, ref in sorted(pairs)]}
                for path, pairs in expected_coverage(manifest).items()]
    worklist = {"schema": "ReleaseReviewWorklist/v1", "requests": requests,
                "metadata_only": metadata, "required_coverage": coverage,
                "merge_parent_views": parent_views}
    draft = {"schema": "ReleaseReviewDraft/v3", "summary": "尚未完成本次審查。",
             "coverage": coverage, "code_risks": [], "config_changes": [], "db_changes": [],
             "external_changes": [], "functional_chain": [], "chain_gaps": [],
             "supporting_coverage": [
                 {"repo": repo["id"], "status": "not-reviewed", "reason": repo.get("reason") or "尚未追查固定關聯來源。",
                  "searches": [], "evidence": []}
                 for repo in manifest["repositories"] if repo["role"] == "supporting"],
             "dependency_coverage": [
                 {"kind": kind, "status": "not-reviewed", "reason": "尚未核對此類依賴。", "evidence": []}
                 for kind in ("config", "db", "ftp", "sftp", "redis", "kafka", "other")],
             "next_steps": ["完成固定變更、功能鏈與依賴語意審查，再由負責人核對實際環境。"],
             "tests_executed": [], "tests_not_executed": ["本次未執行專案測試、部署或環境連線。"],
             "limitations": []}
    return worklist, draft


def write_worklist(context: Path, manifest):
    worklist, draft = prepare(manifest)
    for name, data in (("review-worklist.json", worklist), ("review-result-draft.json", draft)):
        with (context / name).open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
