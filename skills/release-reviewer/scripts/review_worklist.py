"""Plan fixed-source reads and seed an explicitly unfinished v3 review draft."""
from __future__ import annotations

import json
from pathlib import Path

import evidence as ev
import merge_parent_coverage
import coverage_policy

RISK_POLICY = "diff-causality/v1"


def comparison_scopes(manifest):
    if coverage_policy.ancestor_noop(manifest):
        return []
    head = ("head", manifest["head_sha"])
    scopes = [(manifest["changed_files"], ("merge-base", manifest["diff_base"]), head)]
    preview = manifest.get("merge_preview") or {}
    if preview.get("status") == "clean":
        merged = ("merge-preview", preview["tree_sha"])
        scopes += [(preview["changed_files"], head, merged),
                   (manifest["release_changes"], ("base", manifest["base_sha"]), merged)]
    return scopes


def diff_roots(manifest):
    result = []
    for changes, before, after in comparison_scopes(manifest):
        for change in changes:
            result.append({"id": f"DIFF-{len(result) + 1:03}", "path": change["path"],
                           "old_path": change.get("old_path"), "status": change["status"],
                           "before": {"source": before[0], "ref": before[1]},
                           "after": {"source": after[0], "ref": after[1]}})
    return result


def validate_diff_roots(item, manifest, evidence, *, require=ev.require):
    """Every extension points back to a captured code change, even across peers."""
    roots = manifest.get("diff_roots")
    if roots is None:  # Older v3 contexts keep their original contract.
        return []
    require(roots == diff_roots(manifest), "固定 Diff 起點與變更範圍不符。")
    by_id = {root["id"]: root for root in roots}
    identifiers = item.get("diff_roots")
    require(isinstance(identifiers, list) and bool(identifiers) and all(
        isinstance(value, str) and value in by_id for value in identifiers),
        "延伸項目須連回本次固定 Diff 起點。")
    require(len(identifiers) == len(set(identifiers)), "Diff 起點不能重複。")
    require(isinstance(item.get("diff_reason"), str) and item["diff_reason"].strip(),
            "延伸項目須說明與固定 Diff 的因果關係。")
    return identifiers


def validate_risk_links(item, manifest, evidence, *, require=ev.require):
    """Check structural Diff links and fixed-source absence; never infer a bug."""
    identifiers = validate_diff_roots(item, manifest, evidence, require=require)
    roots = [root for root in manifest["diff_roots"] if root["id"] in identifiers]
    def before_path(root):
        return root.get("old_path") or root["path"]
    require(any(citation.get("repo") == "entry" and any(
        (citation.get("path"), citation.get("ref")) in {
            (before_path(root), root["before"]["ref"]), (root["path"], root["after"]["ref"])}
        for root in roots) for citation in evidence), "程式風險須引用所選 Diff 起點的新舊版本位置。")
    absence = item.get("final_absence", [])
    require(isinstance(absence, list), "final_absence 必須是陣列。")
    preview = manifest.get("merge_preview") or {}
    final = ("merge-preview", preview["tree_sha"]) if preview.get("status") == "clean" else ("head", manifest["head_sha"])
    entries = ev.repository(manifest, "entry")["snapshot"]["sources"][final[1]]["entries"]
    for proof in absence:
        require(isinstance(proof, dict) and proof.get("repo") == "entry"
                and (proof.get("source"), proof.get("ref")) == final and ev.safe_path(proof.get("path")),
                "final_absence 須指定最終固定來源與安全路徑。")
        path = proof["path"]
        require(not any(entry["path"] == path for entry in entries), "最終版本仍包含聲稱不存在的路徑。")
        require(any((root["status"].startswith("D") or root.get("old_path")) and before_path(root) == path
                    and any(c.get("repo") == "entry" and c.get("path") == path
                            and c.get("ref") == root["before"]["ref"] for c in evidence)
                    for root in roots), "刪除或改名缺失須附固定 Diff 舊版位置。")
    return identifiers, absence


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
    required = {(view["commit"], view["parent"]): set(view["paths"])
                for view in manifest.get("required_merge_views", [])}
    for merge in manifest["merge_commits"]:
        for view in merge["parent_views"]:
            parent_views.append({"commit": merge["sha"], "parent": view["sha"],
                                 "position": view["position"], "changes": view["changes"]})
            # Older contexts do not have this policy and retain their original reads.
            selected = required.get((merge["sha"], view["sha"]))
            if "required_merge_views" in manifest and selected is None:
                continue
            for change in view["changes"]:
                if selected is not None and not selected.intersection((change.get("path"), change.get("old_path"))):
                    continue
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
                "merge_parent_views": parent_views, "diff_roots": manifest.get("diff_roots", [])}
    draft = {"schema": "ReleaseReviewDraft/v3", "report_layout": "engineering-review/v2", "summary": "尚未完成本次審查。",
             **({"risk_causality_policy": manifest["risk_causality_policy"]} if manifest.get("risk_causality_policy") else {}),
             "merge_parent_review": merge_parent_coverage.unfinished(manifest.get("required_merge_views", [])),
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
    owner_path = context / ".release-reviewer-owned.json"
    if owner_path.is_file():
        try:
            owner = json.loads(owner_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            owner = {}
        identifier = owner.get("request_id") if owner.get("schema") == "release-reviewer-session/v2" else None
        if isinstance(identifier, str) and len(identifier) == 32:
            progress = {"schema": "ReleaseReviewProgress/v1", "request_id": identifier,
                        "revision": 0,
                        "progress": {"read_evidence_ids": [], "unread_cursor": None,
                                     "excluded_questions": [], "next_action": ""},
                        "applied_updates": {}}
            with (context / "review-progress.json").open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(progress, ensure_ascii=False, indent=2) + "\n")
