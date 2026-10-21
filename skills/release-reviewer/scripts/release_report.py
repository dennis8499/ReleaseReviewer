#!/usr/bin/env python3
"""Validate source citations and publish one consistent release review report."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import evidence as ev
import chain_review
import resource_rules
import review_session
import review_worklist
import merge_parent_coverage
import coverage_policy
from review_worklist import expected_coverage

PRIORITIES = ("P0", "P1")
CONFIG_KINDS = {"config-key", "environment-variable", "config-file"}
CONFIG_STATUSES = {"defined": "已有定義", "missing-definition": "未找到定義",
                   "external": "需確認外部注入", "unknown": "待確認"}
DB_KINDS = {"connection": "連線", "database": "資料庫", "table": "資料表",
            "column": "欄位", "view": "View", "procedure": "預存程序"}
EXTERNAL_KINDS = {"ftp", "sftp", "redis", "kafka", "other"}
INTRODUCTIONS = {"first-use", "resource-definition", "target-change", "access-change"}
LABELS = {"P0": "立即處理", "P1": "上版前修正"}
ASSESSMENTS = {"blocked": "存在已確認阻擋", "incomplete": "檢查未完成",
               "pending-confirmation": "待核對上版條件", "no-blockers-found": "已檢查範圍未發現阻擋"}
CHECKLIST_START = "<!-- release-checklist:start -->"
CHECKLIST_END = "<!-- release-checklist:end -->"


class DraftValidationError(ValueError):
    """A draft field or citation can be repaired without replacing frozen sources."""


def draft_require(condition, message):
    if not condition:
        raise DraftValidationError(message)


def text(value, label):
    draft_require(isinstance(value, str) and bool(value.strip()), f"{label} 必須是非空白文字。")
    return value.strip()


def strings(value, label):
    draft_require(isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value),
               f"{label} 必須是文字陣列。")
    return value


def validate_citations(items, manifest, context, *, snapshots=None, analyses=None, all_sources=False,
                       require_ranges=False):
    draft_require(isinstance(items, list), "evidence 必須是陣列。")
    snapshots = {} if snapshots is None else snapshots
    analyses = {} if analyses is None else analyses
    cleaned = []
    for item in items:
        draft_require(isinstance(item, dict), "證據必須是物件。")
        source = item.get("source")
        repo = item.get("repo")
        draft_require(isinstance(repo, str) and bool(repo.strip()), "v3 證據必須含非空白 Repo 身分。")
        ref = item.get("ref")
        path = item.get("path")
        draft_require(isinstance(ref, str) and ev.SHA.fullmatch(ref), "證據必須含完整 SHA。")
        draft_require(ev.safe_path(path), "證據路徑必須是安全的 Repo 相對路徑。")
        catalog = {r["id"]: r for r in manifest["repositories"]}
        draft_require(repo in catalog, "證據 Repo 不在固定 catalog。")
        draft_require(isinstance(source, str) and source in {
            "base", "head", "merge-base", "merge-preview", "merge-parent", "supporting"}, "未知的證據來源。")
        draft_require((repo == "entry") == (source != "supporting"), "證據來源與 Repo 角色不符。")
        fixed = {"base": manifest["base_sha"], "head": manifest["head_sha"],
                 "merge-base": manifest.get("merge_base")}
        if source == "merge-preview":
            preview = manifest.get("merge_preview") or {}
            draft_require(preview.get("status") == "clean", "沒有可用的乾淨合併預覽。")
            reference = preview.get("tree_sha")
        elif source == "merge-parent":
            parents = {p for merge in manifest["merge_commits"] for p in merge["parents"]}
            draft_require(ref in parents, "merge-parent 版本不在固定紀錄內。")
            reference = ref
        elif source == "supporting":
            draft_require(catalog[repo]["status"] == "captured", "關聯 Repo 沒有固定版本。")
            reference = catalog[repo]["ref"]
        else:
            reference = fixed[source]
        ev.require(isinstance(reference, str) and ev.SHA.fullmatch(reference), "固定來源版本無效。")
        draft_require(ref == reference, "證據 SHA 與固定來源不符。")
        # Source resolution and Git failures remain fatal; only the draft checks above
        # are repairable. The evidence cache repeats source/ref/path checks on hits.
        metadata = ev.blob_metadata(manifest, context, source, path, ref, repo)
        content = metadata["content"]
        start, end = item.get("line_start"), item.get("line_end")
        if start is not None or end is not None:
            draft_require(type(start) is int and type(end) is int and 1 <= start <= end,
                       "證據行號必須為有效的正整數區間。")
            draft_require(not metadata["binary"], "二進位證據不能引用文字行號。")
            draft_require(end <= metadata["line_count"], f"證據行號超出固定檔案：{path}")
        if require_ranges and not metadata["binary"] and metadata["line_count"]:
            draft_require(start is not None and end is not None,
                          "新草稿的非空文字問題證據須指定 line_start／line_end。")
        citation = {"repo": repo, "source": source, "ref": ref, "path": path,
                    "content_sha256": metadata["content_sha256"],
                    **({"line_start": start, "line_end": end} if start is not None else {})}
        if (source == "merge-preview" or all_sources) and not metadata["binary"]:
            key = (repo, source, ref, path, start, end)
            if key not in snapshots:
                analysis_key = (metadata["content_sha256"], preview_classification(path))
                if analysis_key not in analyses:
                    analyses[analysis_key] = preview_analysis(content, path)
                snapshots[key] = preview_snapshot(content, path, start, end, analysis=analyses[analysis_key])
            citation.update(snapshots[key])
            if all_sources and "excerpt" not in citation and "snapshot_note" not in citation:
                if not metadata["line_count"]:
                    citation["snapshot_note"] = "固定檔案為空檔（0 行），未擷取程式片段。"
                else:
                    draft_require(not require_ranges, "新草稿的非空文字問題證據須指定 line_start／line_end。")
                    citation["snapshot_note"] = "未指定行號範圍，未擷取程式片段。"
        elif all_sources and metadata["binary"]:
            citation["snapshot_note"] = "二進位檔案，僅保留中繼資料及內容 SHA-256。"
        cleaned.append(citation)
    return cleaned



def preview_classification(path):
    suffix = Path(path).suffix.lower()
    if suffix in {".pem", ".key", ".pfx", ".p12"}:
        return "private"
    if suffix == ".json":
        return "json"
    if suffix in {".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".config", ".env"} or Path(path).name.startswith(".env"):
        return "configuration"
    return "code"


def preview_analysis(content, path):
    """Classify the full file once, before selecting any citation range."""
    decoded = content.decode("utf-8", errors="replace")
    classification = preview_classification(path)
    if re.search(r"(?i)BEGIN.*PRIVATE.*KEY", decoded) or classification == "private":
        return {"kind": "private"}
    if classification in {"json", "configuration"}:
        keys = set()
        def collect(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    keys.add(key)
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)
        if classification == "json":
            try:
                collect(json.loads(decoded))
            except ValueError:
                pass
        return {"kind": "configuration", "configuration_keys": sorted(keys)}
    return {"kind": "code", "decoded": decoded}


def preview_snapshot(content, path, start, end, *, analysis=None):
    """Keep cited code after owned objects are cleaned; omit configuration values."""
    analysis = preview_analysis(content, path) if analysis is None else analysis
    if analysis["kind"] == "private":
        return {"snapshot_note": "認證／私鑰內容已省略，保留原始內容 SHA-256。"}
    if analysis["kind"] == "configuration":
        return {"configuration_keys": list(analysis["configuration_keys"]),
                "snapshot_note": "設定值已省略；僅保留可解析的鍵名及原始內容 SHA-256。"}
    if start is None:
        return {}
    sensitive = re.compile(r"(?i)password|passwd|pwd|token|secret|api.?key|access.?key|private.?key|"
                           r"credential|authorization|connection.?string|data\s*source|initial\s*catalog|"
                           r"server\s*=|host\s*=|begin.*private.*key")
    excerpt = []
    if "lines" not in analysis:
        analysis["lines"] = analysis["decoded"].splitlines()
    for number, line in enumerate(analysis["lines"][start - 1:end], start):
        safe = "[含認證或連線設定的行已省略]" if sensitive.search(line) else redact(line)
        excerpt.append(f"{number}: {safe}")
    return {"excerpt": excerpt}


def final_version(manifest):
    preview = manifest.get("merge_preview") or {}
    if preview.get("status") == "clean":
        return "merge-preview", preview["tree_sha"]
    return "head", manifest["head_sha"]


def validate_result(draft, manifest, context):
    """Validate once with caches scoped to this frozen-source operation."""
    coverage_policy.validate(manifest, context)
    with ev.operation_cache(manifest, context):
        return _validate_result(draft, manifest, context)


def _validate_result(draft, manifest, context):
    snapshots = {}
    analyses = {}

    def cached_citations(items, manifest, context, *, all_sources=False, require_ranges=False):
        return validate_citations(items, manifest, context, snapshots=snapshots, analyses=analyses,
                                  all_sources=all_sources, require_ranges=require_ranges)

    draft_require(isinstance(draft, dict) and draft.get("schema") == "ReleaseReviewDraft/v3",
               "結果須使用 ReleaseReviewDraft/v3。")
    draft_require(not draft.get("checklist"), "初始 Checklist 由 helper 產生，不能預填已確認。")
    layout = draft.get("report_layout")
    draft_require(layout in (None, "engineering-review/v1", "engineering-review/v2"), "不支援的 report_layout。")
    result = {"schema": "ReleaseReviewReport/v3", "summary": text(draft.get("summary"), "summary")}
    for field in ("coverage", "code_risks", "config_changes", "db_changes", "external_changes", "dependency_coverage"):
        draft_require(isinstance(draft.get(field), list), f"{field} 必須是陣列。")
    noop = coverage_policy.ancestor_noop(manifest)
    if noop:
        for field in ("code_risks", "config_changes", "db_changes", "external_changes", "functional_chain"):
            draft_require(not draft.get(field), "祖先無差異範圍不能新增風險、部署依賴或功能鏈。")
    for field in ("next_steps", "tests_executed", "tests_not_executed", "limitations"):
        result[field] = strings(draft.get(field), field)
    expected = expected_coverage(manifest)
    all_changes = (manifest["changed_files"] + manifest["release_changes"]
                   + (manifest.get("merge_preview") or {}).get("changed_files", []))
    metadata_paths = {p for c in all_changes
                      if c.get("binary") or "160000" in (c.get("old_mode"), c.get("new_mode"))
                      for p in (c.get("path"), c.get("old_path")) if p}
    covered = {}
    for item in draft["coverage"]:
        draft_require(isinstance(item, dict), "coverage 項目須為物件。")
        path = item.get("path")
        draft_require(isinstance(path, str) and path in expected and path not in covered,
                      f"覆蓋路徑重複或不在變更範圍：{path}")
        status = item.get("status")
        draft_require(isinstance(status, str) and status in {"reviewed", "metadata-only", "not-reviewed"}, "覆蓋狀態無效。")
        citations = cached_citations(item.get("evidence", []), manifest, context)
        draft_require(all(e["repo"] == "entry" and e["path"] == path for e in citations), "覆蓋證據必須指向入口 Repo 該路徑。")
        entry = {"path": path, "status": status, "evidence": citations}
        if status == "reviewed":
            draft_require(path not in metadata_paths, "二進位或 submodule 路徑須保留內容檢查限制。")
            actual = {(e["source"], e["ref"]) for e in citations}
            draft_require(expected[path] <= actual, f"缺少此路徑在各比較範圍的固定證據：{path}")
        else:
            entry["reason"] = text(item.get("reason"), "未完整覆蓋原因")
        covered[path] = entry
    draft_require(set(covered) == set(expected), "coverage 未列出所有變更路徑及改名前的路徑。")
    result["coverage"] = list(covered.values())
    parents_complete = True
    if manifest.get("merge_parent_policy") is not None:
        ev.require(manifest["merge_parent_policy"] == merge_parent_coverage.POLICY, "unsupported merge-parent policy")
        views = merge_parent_coverage.required(manifest["release_changes"] if manifest["merge_preview"]["status"] == "clean"
                                              else manifest["changed_files"], manifest["merge_commits"],
            {entry["path"] for entry in manifest["repositories"][0]["snapshot"]["sources"][final_version(manifest)[1]]["entries"] if entry["kind"] == "blob"})
        ev.require(manifest.get("required_merge_views") == views, "necessary merge-parent views differ")
        parents_complete = merge_parent_coverage.validate(views, draft.get("merge_parent_review", []),
            lambda items: cached_citations(items, manifest, context), final_ref=final_version(manifest)[1],
            parent_repo="entry", require=draft_require)
    result["merge_parent_review"] = draft.get("merge_parent_review", [])
    reviewed = {p for p, item in covered.items() if item["status"] == "reviewed"}
    previous_priority = -1
    final_pair = final_version(manifest)
    risks = []
    for number, item in enumerate(draft["code_risks"], 1):
        draft_require(isinstance(item, dict) and item.get("id") == f"R-{number:03}", "程式風險編號須連續。")
        priority = item.get("priority")
        draft_require(priority in PRIORITIES, "只接受直接阻止上版的 P0／P1 程式風險。")
        order = PRIORITIES.index(priority)
        draft_require(order >= previous_priority, "程式風險須依 P0、P1 排序。")
        previous_priority = order
        risk = {k: text(item.get(k), k) for k in
                ("id", "priority", "title", "blocking_reason", "trigger", "impact", "recommendation", "scenario_source")}
        risk["scenario"] = text(item.get("scenario", "未提供"), "scenario")
        technical = item.get("technical_details", {})
        draft_require(isinstance(technical, dict), "technical_details 必須為物件。")
        risk["technical_details"] = {key: text(technical.get(key, "未提供"), f"technical_details.{key}")
                                     for key in ("explanation", "impact_scope", "engineering_fix")}
        for key in ("expected", "actual"):
            risk[key] = text(item.get(key, "未提供"), key)
        if layout in ("engineering-review/v1", "engineering-review/v2"):
            draft_require("scenario" in item and risk["scenario"] != "未提供" and
                          all(key in technical and value != "未提供" for key, value in risk["technical_details"].items()),
                          "新草稿須完整填 scenario 與 technical_details.explanation／impact_scope／engineering_fix；未提供僅供舊草稿相容。")
        if layout == "engineering-review/v2":
            draft_require(all(key in item and risk[key] != "未提供" for key in ("expected", "actual")),
                          "新草稿須完整填 expected／actual；未提供僅供舊草稿相容。")
            prose = [risk[key] for key in ("blocking_reason", "trigger", "impact", "recommendation", "scenario", "expected", "actual")]
            draft_require(all(risk_prose(value) for value in prose + list(risk["technical_details"].values())),
                          "新草稿的問題說明須含實質內容，不能只有未重現標記。")
        draft_require(risk["scenario_source"] in {"code-derived", "reproduced"}, "情境來源無效。")
        draft_require(risk["scenario_source"] != "reproduced" or bool(result["tests_executed"]),
                   "標示已重現時須記錄實際執行的檢查。")
        risk["evidence"] = cached_citations(item.get("evidence", []), manifest, context, all_sources=True,
                                            require_ranges=layout == "engineering-review/v2")
        draft_require(any(e["repo"] == "entry" and e["path"] in reviewed for e in risk["evidence"]),
                   "程式風險至少須引用一個已審查的變更路徑。")
        draft_require(any(e["repo"] == "entry" and (e["source"], e["ref"]) == final_pair for e in risk["evidence"]),
                   "程式風險須附最終受審版本的行為證據。")
        risks.append(risk)
    result["code_risks"] = risks
    searches = {}
    for field, prefix, kinds in (("config_changes", "C", CONFIG_KINDS),
                                 ("db_changes", "D", set(DB_KINDS)),
                                 ("external_changes", "E", EXTERNAL_KINDS)):
        changes = []
        identities = set()
        for number, item in enumerate(draft[field], 1):
            draft_require(isinstance(item, dict) and item.get("id") == f"{prefix}-{number:03}",
                       f"{field} 編號須連續。")
            change = {k: text(item.get(k), k) for k in
                      ("id", "kind", "name", "description", "baseline_reason", "recommendation")}
            draft_require(change["kind"] in kinds, f"{field} 類型無效。")
            change["introduction"] = item.get("introduction", "first-use")
            draft_require(isinstance(change["introduction"], str) and change["introduction"] in INTRODUCTIONS,
                          "新增依賴判定類型無效。")
            change["evidence"] = cached_citations(item.get("evidence", []), manifest, context)
            change["diff_roots"] = review_worklist.validate_diff_roots(item, manifest, change["evidence"], require=draft_require)
            change["diff_reason"] = item.get("diff_reason", "未提供")
            draft_require(any(e["repo"] == "entry" and (e["source"], e["ref"]) == final_pair for e in change["evidence"]),
                       "新增依賴須引用最終受審版本中的使用、目標或部署定義位置。")
            identifiers = item.get("baseline_searches")
            draft_require(isinstance(identifiers, list) and bool(identifiers),
                       "新增使用須附基礎版本整個 Repo 的搜尋紀錄。")
            for identifier in identifiers:
                draft_require(isinstance(identifier, str) and re.fullmatch(r"[a-f0-9]{64}", identifier),
                              "基礎搜尋紀錄 ID 無效。")
                if identifier not in searches:
                    searches[identifier] = ev.validate_baseline_search(context, manifest, identifier)
            change["baseline_searches"] = identifiers
            if field == "config_changes":
                status = item.get("definition_status")
                draft_require(isinstance(status, str) and status in CONFIG_STATUSES, "設定定義狀態無效。")
                change["definition_status"] = status
                change["definition_evidence"] = cached_citations(
                    item.get("definition_evidence", []), manifest, context)
                if status == "defined":
                    draft_require(any(e["repo"] == "entry" and (e["source"], e["ref"]) == final_pair
                                   for e in change["definition_evidence"]),
                               "已有定義須附最終版本中的設定定義證據。")
                identity = (change["kind"], change["name"])
            else:
                change["connection"] = text(item.get("connection"), "connection")
                change["resolution"] = item.get("resolution")
                draft_require(isinstance(change["resolution"], str) and change["resolution"] in {"identified", "unknown"},
                              "資源辨識狀態無效。")
                identity = (change["connection"], change["kind"], change["name"])
            draft_require(identity not in identities, "同一新增使用項目不應重複列出。")
            identities.add(identity)
            changes.append(change)
        result[field] = changes
    result["baseline_searches"] = [{"id": identifier, "query": record["query"],
                                   "repo": record["repo"], "ref": record["ref"],
                                   "ignore_case": record["ignore_case"],
                                   "match_count": len(record["matches"])}
                                  for identifier, record in searches.items()]
    result.update(chain_review.validate(draft, manifest, context, cached_citations, require=draft_require))
    unrelated = {c["repo"] for c in result["supporting_coverage"] if c["status"] == "unrelated"}
    draft_require(not any(e["repo"] in unrelated for risk in risks for e in risk["evidence"]), "風險不能引用已判定無關的 Repo。")
    categories, resource_complete = {}, True
    for item in draft["dependency_coverage"]:
        draft_require(isinstance(item, dict), "依賴覆蓋項目須為物件。")
        kind = item.get("kind")
        draft_require(isinstance(kind, str) and kind in {"config", "db", *EXTERNAL_KINDS} and kind not in categories,
                      "依賴覆蓋類型重複或無效。")
        status = item.get("status")
        draft_require(isinstance(status, str) and status in {"reviewed", "not-reviewed", "not-applicable"}, "依賴覆蓋狀態無效。")
        citations = cached_citations(item.get("evidence", []), manifest, context)
        if status == "not-applicable":
            draft_require(noop and not citations and not item.get("searches") and not item.get("baseline_searches"),
                          "本次不適用須有已驗證的祖先無差異範圍且不能附搜尋或內容證據。")
        if status == "reviewed" and expected:
            draft_require(any(e["repo"] == "entry" and (e["source"], e["ref"]) == final_pair for e in citations),
                       "依賴覆蓋須有最終受審版本證據。")
        categories[kind] = {"kind": kind, "status": status, "reason": text(item.get("reason"), "依賴覆蓋說明"), "evidence": citations}
        resource_complete = resource_complete and status in {"reviewed", "not-applicable"}
    draft_require(set(categories) == {"config", "db", *EXTERNAL_KINDS}, "缺少設定、DB 或外部資源類型覆蓋。")
    result["dependency_coverage"] = list(categories.values())
    result["checklist"] = resource_rules.build_checklist(result)
    for field in ("config_changes", "db_changes", "external_changes"):
        for dependency in result[field]:
            dependency["checklist_ids"] = [c["id"] for c in result["checklist"] if c["dependency_id"] == dependency["id"]]
    result["environment_status"] = "待人工核對" if result["checklist"] else "未驗證實際環境"
    # Complete means the documented static scope is covered, never that production is ready.
    unresolved = any(c["definition_status"] == "unknown" for c in result["config_changes"])
    unresolved |= any(d["resolution"] == "unknown" for d in result["db_changes"])
    unresolved |= any(d["resolution"] == "unknown" for d in result["external_changes"])
    complete = manifest["context_complete"] and all(v["status"] == "reviewed" for v in covered.values())
    complete = complete and not unresolved and result["chain_complete"] and resource_complete and parents_complete
    result["review_complete"] = complete
    result["status"] = "靜態檢查完成" if complete else "檢查未完成"
    if complete and not expected:
        result["status"] = "沒有差異"
    result["identity"] = {key: manifest.get(key) for key in
                          ("project", "repo", "base_input", "head_input", "base_resolved_ref", "head_resolved_ref", "base_sha", "head_sha",
                           "merge_base", "diff_base", "mode", "generated_at", "working_tree_unchanged", "report_root")}
    result["identity"].update({"final_source": final_pair[0], "final_ref": final_pair[1]})
    catalog = [{key: value for key, value in record.items() if key != "snapshot"}
               for record in manifest["repositories"]]
    for projected, record in zip(catalog, manifest["repositories"]):
        if record.get("snapshot"):
            projected["snapshot"] = {key: value for key, value in record["snapshot"].items() if key != "sources"}
    result["identity"].update({"scope": manifest["scope"], "entry_repo": "entry", "repositories": catalog})
    result["identity"].update({key: manifest.get(key) for key in
                               ("snapshot_policy", "capture_completed_at", "workspace_instructions", "diff_roots")})
    if "coverage_policy" in manifest:
        result["identity"]["coverage_policy"] = manifest["coverage_policy"]
    result["context_gaps"] = list(manifest["context_gaps"])
    if not parents_complete:
        result["context_gaps"].append("必要 merge-parent 尚未對照最終版本。")
    result["merge_status"] = (manifest.get("merge_preview") or {}).get("status")
    result["merge_detail"] = (manifest.get("merge_preview") or {}).get("detail", "")
    result["review_gaps"] = review_gaps(result)
    result["blocker_count"] = len(risks) + int(result["merge_status"] == "conflicts")
    result["pending_count"] = len(result["checklist"])
    result["gap_count"] = len(result["review_gaps"])
    result["release_assessment"] = ("blocked" if result["blocker_count"] else
                                    "incomplete" if not complete else
                                    "pending-confirmation" if result["pending_count"] else
                                    "no-blockers-found")
    return result


def redact(value):
    """Remove common credential formats; reviewers must also omit secret values."""
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if not isinstance(value, str):
        return value
    value = re.sub(r"(?is)-----BEGIN[^\r\n]*PRIVATE KEY-----.*?-----END[^\r\n]*PRIVATE KEY-----",
                   "[私鑰內容已省略]", value)
    value = re.sub(r"(?i)(\b(?:password|passwd|pwd|token|api[_-]?key|secret|access[_-]?key)\b"
                   r"\s*[=:]\s*)(?:\"[^\"]*\"|'[^']*'|[^;,\s&]+)",
                   r"\1[已遮蔽]", value)
    value = re.sub(r"(?i)([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@", r"\1[已遮蔽]@", value)
    return re.sub(r"(?i)(\bBearer\s+)\S+", r"\1[已遮蔽]", value)


def cell(value):
    return str(value).replace("|", r"\|").replace("\r", " ").replace("\n", " ")


def code_span(value):
    value = str(value).replace("\r", " ").replace("\n", " ")
    longest = max((len(match.group()) for match in re.finditer(r"`+", value)), default=0)
    fence = "`" * (longest + 1)
    padding = " " if value.startswith("`") or value.endswith("`") else ""
    return fence + padding + value + padding + fence


def risk_prose(value):
    """The renderer owns the single canonical inference note for each finding."""
    return value.replace("依程式推導，未實際重現。", "").strip()


def paragraphs(*values):
    lines, seen = [], set()
    for item in values:
        label, value = item if isinstance(item, tuple) else (None, item)
        for paragraph in re.split(r"\n\s*\n", risk_prose(value)):
            paragraph = paragraph.strip()
            if paragraph and paragraph not in seen:
                seen.add(paragraph)
                lines += [f"{label}：{paragraph}" if label else paragraph, ""]
    return lines


def citations(items, repo_names=None):
    lines = []
    for item in items:
        location = (repo_names or {}).get(item["repo"], item["repo"]) + "/" + item["path"]
        if "line_start" in item:
            location += f":{item['line_start']}-{item['line_end']}"
        lines += [f"{code_span(location)}（來源：{code_span(item['source'])}；固定版本：{code_span(item['ref'])}）", ""]
        if item.get("content_sha256"):
            lines += [f"內容 SHA-256：{code_span(item['content_sha256'])}", ""]
        if item.get("excerpt"):
            longest = max((len(m.group()) for line in item["excerpt"] for m in re.finditer(r"~+", line)), default=0)
            fence = "~" * max(3, longest + 1)
            lines += [fence + "text", *item["excerpt"], fence, ""]
        if "configuration_keys" in item:
            lines += ["設定鍵：" + ("、".join(item["configuration_keys"]) or "未取得可解析鍵名"), ""]
        if item.get("snapshot_note"):
            lines += [item["snapshot_note"], ""]
    return lines


def review_gaps(result):
    gaps = list(result["context_gaps"]) + list(result["chain_gaps"])
    gaps += [f"未覆蓋 {c['path']}：{c['reason']}" for c in result["coverage"] if c["status"] != "reviewed"]
    gaps += [f"關聯 Repo {c['repo']}：{c['reason']}" for c in result["supporting_coverage"]
             if c["status"] == "not-reviewed"]
    gaps += [f"功能鏈 {link['id']}：{link['reason']}" for link in result["functional_chain"]
             if link["status"] == "not-reviewed"]
    gaps += [f"依賴類型 {c['kind']}：{c['reason']}" for c in result["dependency_coverage"]
             if c["status"] == "not-reviewed"]
    gaps += [f"{c['id']} {c['name']}：設定來源尚未解析。" for c in result["config_changes"]
             if c["definition_status"] == "unknown"]
    gaps += [f"{c['id']} {c['name']}：資源目標尚未解析。"
             for c in result["db_changes"] + result["external_changes"] if c["resolution"] == "unknown"]
    return list(dict.fromkeys(gaps))


def static_body(body):
    """Hash fixed review text, excluding only the delimited human checklist."""
    ev.require(body.count(CHECKLIST_START) == body.count(CHECKLIST_END) == 1,
               "報告須包含唯一的人工 Checklist 區塊。")
    start, end = body.index(CHECKLIST_START), body.index(CHECKLIST_END)
    ev.require(start < end, "Checklist 邊界順序無效。")
    return body[:start] + body[end + len(CHECKLIST_END):]


def short_citations(items):
    return "；".join(cell(e["repo"] + "/" + e["path"] +
                    (f":{e['line_start']}-{e['line_end']}" if "line_start" in e else ""))
                    for e in items)


def render_checklist(result):
    lines = [CHECKLIST_START, "### 人工核對紀錄", ""]
    for dependency in result["config_changes"] + result["db_changes"] + result["external_changes"]:
        lines += [f"#### [{dependency['id']}｜{dependency['name']}](#{dependency['id']})", "",
                  "| 編號 | 核對事項 | 狀態 | 核對人 | 日期 | 工單／驗證證據 | 不適用理由 |",
                  "| --- | --- | --- | --- | --- | --- | --- |"]
        for item in result["checklist"]:
            if item["dependency_id"] == dependency["id"]:
                lines.append(f"| <a id=\"{item['id']}\"></a>{item['id']} | {cell(item['question'])} | 待確認 | | | | |")
        lines.append("")
    if not result["checklist"]:
        lines += ["本次沒有新增依賴的自動核對項。", ""]
    lines.append(CHECKLIST_END)
    return "\n".join(lines)


def render_markdown(result):
    identity = result["identity"]
    repo_names = {repo["id"]: repo["name"] for repo in identity["repositories"]}
    counts = {priority: sum(risk["priority"] == priority for risk in result["code_risks"])
              for priority in PRIORITIES}
    lines = [f"**{ASSESSMENTS[result['release_assessment']]}**｜{result['status']}。{result['summary']}", "",
             f"問題數量：P0 {counts['P0']}／P1 {counts['P1']}（僅列直接阻擋上版的程式風險）。", "",
             f"阻擋 {result['blocker_count']} 項｜合併衝突 {int(result['merge_status'] == 'conflicts')} 項｜待確認 {result['pending_count']} 項｜檢查缺口 {result['gap_count']} 項。",
             "判斷固定於產生時；人工填寫 Checklist 不會自動核准上版。",
             "", "## 比較資訊", "",
             f"- Repo：{code_span(identity['project'])}",
             f"- 本機路徑：{code_span(identity['repo'])}", "",
             "| 角色 | 輸入分支 | 實際 ref | Commit（前 8 碼） |", "| --- | --- | --- | --- |"]
    for role, prefix in (("基礎分支", "base"), ("比較分支", "head")):
        lines.append(f"| {role} | {cell(code_span(identity[prefix + '_input']))} | "
                     f"{cell(code_span(identity.get(prefix + '_resolved_ref') or '未提供'))} | "
                     f"{code_span(identity[prefix + '_sha'][:8])} |")
    diff_base = identity.get("diff_base") or identity.get("merge_base")
    lines += ["", f"- 合併方向：{code_span(identity['head_input'])} → {code_span(identity['base_input'])}",
              f"- 主要差異範圍（共同祖先 → 比較分支）：{code_span(diff_base[:8] if diff_base else '未取得')} → {code_span(identity['head_sha'][:8])}",
              f"- 最終受審版本：{code_span(identity['final_source'])}；{code_span(identity['final_ref'])}",
              f"- 快照擷取：{identity.get('generated_at')} → {identity.get('capture_completed_at')}（UTC）。",
              "- 適用版本：本報告僅適用上述固定 SHA 及本次擷取的相關 Repo；後續分支更新不改變本次結論。", "",
              "## 問題", ""]
    if result["merge_status"] == "conflicts":
        lines += ['<a id="M-001"></a>', "", "### M-001｜合併衝突", "",
                  "**問題描述**", "", "本次分支無法直接整合為乾淨的上版版本。", "",
                  "**重現原因**", "", "對本次固定 base/head 執行 merge-tree 時，Git 要求人工處理衝突。", "",
                  "**預期結果**", "", "指定分支可整合為乾淨、可審查的上版版本。", "",
                  "**實際結果**", "", "Git 回報衝突，無法取得乾淨的最終合併內容。", "",
                  "**證據**", "", "Git 原始結果與固定 SHA 見技術審查紀錄。", "",
                  "**建議改善方式**", "", "逐一解決固定版本紀錄中的衝突，提交後重新擷取與審查。", ""]
    for risk in result["code_risks"]:
        technical = risk["technical_details"]
        lines += [f'<a id="{risk["id"]}"></a>', "", f"### {risk['id']}｜[{risk['priority']}] {risk['title']}", "",
                  "**問題描述**", "", *paragraphs(risk["impact"], ("影響範圍", technical["impact_scope"]),
                                                       ("上版阻擋依據", risk["blocking_reason"])),
                  "**重現原因**", "", *paragraphs(risk["scenario"], ("觸發條件", risk["trigger"])),
                  ("依程式推導，未實際重現。" if risk["scenario_source"] == "code-derived"
                   else "已實際重現，執行紀錄見技術審查紀錄。"), "",
                  "**預期結果**", "", risk_prose(risk["expected"]), "",
                  "**實際結果**", "", risk_prose(risk["actual"]), "",
                  "**證據**", "", *paragraphs(technical["explanation"]), *citations(risk["evidence"], repo_names),
                  "**建議改善方式**", "", *paragraphs(risk["recommendation"], technical["engineering_fix"])]
    if not result["blocker_count"]:
        lines += ["已檢查範圍未發現有證據的直接阻擋；待確認條件與缺口見後文。", ""]
    lines += ["## 範圍與限制", "",
              "本次只審查指定 Repo 的已提交差異；其他 Repo 的擷取時 HEAD 是功能鏈佐證。",
              "結論只適用本次固定版本；擷取後分支移動不改寫本次結果。", ""]
    lines += [f"- {gap}" for gap in result["review_gaps"]]
    lines += [f"- {limit}" for limit in result["limitations"]]
    if not result["review_gaps"] and not result["limitations"]:
        lines += ["本次沒有另外列出的靜態缺口或限制。"]
    for title, values in (("已執行檢查", result["tests_executed"]),
                          ("未執行檢查", result["tests_not_executed"])):
        lines += ["", f"**{title}**", "", *([f"- {value}" for value in values] or ["- 無紀錄。"])]
    lines += ["", "### 新增部署依賴與人工核對", ""]
    dependencies = result["config_changes"] + result["db_changes"] + result["external_changes"]
    for dependency in dependencies:
        status = (CONFIG_STATUSES[dependency["definition_status"]] if "definition_status" in dependency
                  else "待解析目標" if dependency["resolution"] == "unknown" else "已辨識目標")
        lines += [f"<a id=\"{dependency['id']}\"></a>", "",
                  f"#### {dependency['id']}｜{dependency['name']}", "",
                  f"{dependency['description']}（{status}）",
                  f"連線／用途：{dependency.get('connection', '入口服務設定')}。",
                  f"**上版準備**：{dependency['recommendation']}", "",
                  "**核對項**：" + "、".join(f"[{kid}](#{kid})" for kid in dependency["checklist_ids"]), ""]
    if not dependencies:
        lines += ["本次未辨識到新增依賴。", ""]
    lines += ["核對狀態限「待確認／已確認／未通過／不適用」。已確認需核對人、日期及開通／套用後驗證證據；",
              "僅有申請工單仍為待確認，不適用需具體理由。必要項目待確認或未通過時，上版準備未完成。",
              "本段核對紀錄可人工更新；固定正文與產生時判斷保留原始快照。", "",
               render_checklist(result), ""]
    lines += ["## 下一步", "",
              *([f"- {step}" for step in result["next_steps"]] or ["本次沒有另外列出的下一步。"]), "",
              "## 技術審查紀錄", "", "<details>",
              "<summary>固定版本、搜尋、功能鏈與覆蓋紀錄</summary>", ""]
    for label, key in (("Repo", "repo"), ("報告目錄", "report_root"),
                       ("基礎分支", "base_input"), ("比較分支", "head_input"),
                       ("基礎 SHA", "base_sha"), ("比較 SHA", "head_sha"), ("共同起點", "merge_base"),
                       ("最終來源", "final_source"), ("最終 SHA", "final_ref"),
                       ("擷取開始（UTC）", "generated_at"), ("快照完成（UTC）", "capture_completed_at"),
                       ("快照政策", "snapshot_policy"), ("本機檔案與暫存區維持原狀", "working_tree_unchanged")):
        lines.append(f"- {label}：{identity.get(key)}")
    lines += ["", "版本比對只包含已提交內容。靜態範圍完整不代表目標環境已驗證。", ""]
    if result["merge_status"] != "clean":
        lines += ["**合併預覽紀錄**：", "",
                  *["> " + line for line in result["merge_detail"].splitlines()], ""]
    for item in identity.get("workspace_instructions") or []:
        lines.append(f"- 固定工作區指示：{cell(item['origin'])}；SHA-256 {item['sha256']}")
    for root in identity.get("diff_roots") or []:
        lines.append(f"- Diff 起點 {root['id']}：{cell(root['path'])}；{root['before']['ref']} → {root['after']['ref']}")
    records = {r["id"]: r for r in result["baseline_searches"]}
    for dependency in dependencies:
        lines += [f"### {dependency['id']} 新增判定與證據", "",
                  f"新增類型：{dependency['introduction']}；{dependency['baseline_reason']}", "",
                  f"Diff 起點：{'、'.join(dependency['diff_roots'])}；{dependency['diff_reason']}", "",
                  *citations(dependency["evidence"], repo_names)]
        if dependency.get("definition_evidence"):
            lines += ["設定定義：", "", *citations(dependency["definition_evidence"], repo_names)]
        for identifier in dependency["baseline_searches"]:
            record = records[identifier]
            lines.append(f"- 基礎全 Repo 搜尋：命中 {record['match_count']}；紀錄 {identifier}")
        lines.append("")
    lines += ["### 關聯 Repo 與功能鏈", "",
              "| Repo | 固定本機分支 | SHA | 狀態 |", "| --- | --- | --- | --- |"]
    for repo in identity["repositories"]:
        lines.append(f"| {cell(repo['id'] + ' / ' + repo['name'])} | {cell(repo.get('branch') or ('detached HEAD' if repo['role'] == 'supporting' else '入口指定分支'))} | {repo.get('ref') or '見入口版本／未取得'} | {repo['status']} |")
    for coverage in result["supporting_coverage"]:
        label = "本次不適用" if coverage["status"] == "not-applicable" else coverage["status"]
        lines += ["", f"**{coverage['repo']}：{label}**；{coverage['reason']}", "",
                  *citations(coverage["evidence"], repo_names)]
    for link in result["functional_chain"]:
        lines += ["", f"**{link['id']}｜{link['from_repo']} → {link['to_repo']}｜{link['kind']}**",
                  f"{link['status']}：{link['description']}",
                  f"Diff 起點：{'、'.join(link['diff_roots'])}；{link['diff_reason']}", "", *citations(link["evidence"], repo_names)]
        if link.get("reason"):
            lines += ["", link["reason"]]
    for record in result["supporting_searches"]:
        lines.append(f"- 關聯搜尋：{record['repo']}；版本 {record['ref']}；命中 {record['match_count']}；紀錄 {record['id']}")
    lines += ["", "### 靜態覆蓋", "", "| 路徑 | 狀態 | 未完成原因 |", "| --- | --- | --- |"]
    for coverage in result["coverage"]:
        lines.append(f"| {cell(coverage['path'])} | {coverage['status']} | {cell(coverage.get('reason', ''))} |")
    if not result["coverage"]:
        lines.append("| — | 沒有變更路徑 | — |")
    if result.get("merge_parent_review"):
        lines += ["", "**必要合併父版本對照**：", "",
                  *[f"- {row['id']}：{row['status']}；{row['reason']}" for row in result["merge_parent_review"]]]
    lines += ["", "**依賴類型覆蓋**：", "",
              *[f"- {c['kind']}：{'本次不適用' if c['status'] == 'not-applicable' else c['status']}；{c['reason']}" for c in result["dependency_coverage"]]]
    lines += ["", "</details>", ""]
    return "\n".join(line for index, line in enumerate(lines)
                     if line or not index or lines[index - 1])


def publish_report(context, draft_path, *, retain_on_validation_error=False):
    context = Path(context).expanduser().absolute()
    retain_context = False
    try:
        context = review_session.validate_session(context)
        manifest = ev.load_manifest(context)
        draft_path = Path(draft_path).resolve()
        try:
            draft_require(draft_path.is_relative_to(context), "草稿必須位於本次 context 內。")
            try:
                draft = json.loads(draft_path.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError as exc:
                raise DraftValidationError(f"草稿 JSON 無效：{exc}") from exc
            result = redact(validate_result(draft, manifest, context))
        except DraftValidationError:
            if retain_on_validation_error:
                coverage_policy.validate(manifest, context)
            retain_context = bool(retain_on_validation_error)
            raise
        destination = Path(manifest["report_root"])
        ev.require(not destination.is_relative_to(context), "報告不能寫入稍後會清理的 context。")
        if manifest.get("snapshot_policy") is not None:
            ev.require(Path(manifest["repo"]).is_dir(), "受審 Repo 目錄不存在，無法寫入報告。")
            destination.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        stem = f"release-review-{stamp}-{manifest['base_sha'][:8]}-{manifest['head_sha'][:8]}"
        body = render_markdown(result)
        metadata = {"schema": result["schema"], **result["identity"],
                    "review_complete": result["review_complete"],
                    "release_assessment": result["release_assessment"],
                    "blocker_count": result["blocker_count"], "pending_count": result["pending_count"],
                    "gap_count": result["gap_count"],
                    "context_sha256": hashlib.sha256((context / "manifest.json").read_bytes()).hexdigest(),
                    "static_body_sha256": hashlib.sha256(static_body(body).encode("utf-8")).hexdigest()}
        marker = base64.b64encode(ev.canonical(metadata)).decode("ascii")
        markdown = body + f"\n<!-- release-review-report:{marker} -->\n"
        for number in range(1000):
            suffix = "" if number == 0 else f"-{number:02}"
            md = destination / f"{stem}{suffix}.md"
            created = False
            try:
                with md.open("x", encoding="utf-8", newline="\n") as stream:
                    created = True
                    stream.write(markdown)
            except FileExistsError:
                continue
            except BaseException:
                if created:
                    md.unlink(missing_ok=True)
                raise
            return md
        raise ValueError("找不到可使用的新報告檔名。")
    finally:
        if not retain_context:
            review_session.cleanup_if_owned(context)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context-dir", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--retain-on-validation-error", action="store_true")
    args = parser.parse_args(argv)
    try:
        md = publish_report(args.context_dir, args.result,
                            retain_on_validation_error=args.retain_on_validation_error)
    except DraftValidationError as exc:
        if args.retain_on_validation_error:
            print(json.dumps({"context_dir": str(Path(args.context_dir).expanduser().absolute()),
                              "context_retained": True, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 3
        print(f"release-report: 未建立報告：{exc}", file=sys.stderr)
        return 2
    except (ValueError, OSError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        print(f"release-report: 未建立報告：{exc}", file=sys.stderr)
        return 2
    print(json.dumps({"markdown_report": str(md)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
