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

PRIORITIES = ("P0", "P1", "P2", "P3")
CONFIG_KINDS = {"config-key", "environment-variable", "config-file"}
CONFIG_STATUSES = {"defined": "已有定義", "missing-definition": "未找到定義",
                   "external": "需確認外部注入", "unknown": "待確認"}
DB_KINDS = {"connection": "連線", "database": "資料庫", "table": "資料表",
            "column": "欄位", "view": "View", "procedure": "預存程序"}
EXTERNAL_KINDS = {"ftp", "sftp", "redis", "kafka", "other"}
INTRODUCTIONS = {"first-use", "resource-definition", "target-change", "access-change"}
LABELS = {"P0": "立即處理", "P1": "上板前優先修正", "P2": "排程修正", "P3": "後續改善"}


def text(value, label):
    ev.require(isinstance(value, str) and bool(value.strip()), f"{label} 必須是非空白文字。")
    return value.strip()


def strings(value, label):
    ev.require(isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value),
               f"{label} 必須是文字陣列。")
    return value


def validate_citations(items, manifest, context):
    ev.require(isinstance(items, list), "evidence 必須是陣列。")
    cleaned = []
    for item in items:
        ev.require(isinstance(item, dict), "證據必須是物件。")
        source = item.get("source")
        repo = item.get("repo")
        ev.require(isinstance(repo, str) and bool(repo.strip()), "v2 證據必須含非空白 Repo 身分。")
        ref = item.get("ref")
        path = item.get("path")
        ev.require(isinstance(ref, str) and ev.SHA.fullmatch(ref), "證據必須含完整 SHA。")
        content = ev.read_blob(manifest, context, source, path, ref, repo)
        start, end = item.get("line_start"), item.get("line_end")
        if start is not None or end is not None:
            ev.require(type(start) is int and type(end) is int and 1 <= start <= end,
                       "證據行號必須為有效的正整數區間。")
            ev.require(b"\0" not in content, "二進位證據不能引用文字行號。")
            ev.require(end <= len(content.splitlines()), f"證據行號超出固定檔案：{path}")
        cleaned.append({"repo": repo, "source": source, "ref": ref, "path": path,
                        **({"line_start": start, "line_end": end} if start is not None else {})})
    return cleaned


def expected_coverage(manifest):
    expected = {}
    preview = manifest.get("merge_preview") or {}
    head_pair = ("head", manifest["head_sha"])
    left_pair = ("merge-base", manifest["diff_base"])
    scopes = [(manifest["changed_files"], left_pair, head_pair)]
    if preview.get("status") == "clean":
        merged = ("merge-preview", preview["tree_sha"])
        scopes += [(preview["changed_files"], head_pair, merged),
                   (manifest["release_changes"], ("base", manifest["base_sha"]), merged)]
    # Conflict trees cannot be treated as a final executable version.
    for changes, before, after in scopes:
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


def final_version(manifest):
    preview = manifest.get("merge_preview") or {}
    if preview.get("status") == "clean":
        return "merge-preview", preview["tree_sha"]
    return "head", manifest["head_sha"]


def validate_result(draft, manifest, context):
    ev.require(isinstance(draft, dict) and draft.get("schema") == "ReleaseReviewDraft/v2",
               "結果須使用 ReleaseReviewDraft/v2。")
    ev.require(not draft.get("checklist"), "初始 Checklist 由 helper 產生，不能預填已確認。")
    result = {"schema": "ReleaseReviewReport/v2", "summary": text(draft.get("summary"), "summary")}
    for field in ("coverage", "code_risks", "config_changes", "db_changes", "external_changes", "dependency_coverage"):
        ev.require(isinstance(draft.get(field), list), f"{field} 必須是陣列。")
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
        ev.require(isinstance(item, dict), "coverage 項目須為物件。")
        path = item.get("path")
        ev.require(path in expected and path not in covered, f"覆蓋路徑重複或不在變更範圍：{path}")
        status = item.get("status")
        ev.require(status in {"reviewed", "metadata-only", "not-reviewed"}, "覆蓋狀態無效。")
        citations = validate_citations(item.get("evidence", []), manifest, context)
        ev.require(all(e["repo"] == "entry" and e["path"] == path for e in citations), "覆蓋證據必須指向入口 Repo 該路徑。")
        entry = {"path": path, "status": status, "evidence": citations}
        if status == "reviewed":
            ev.require(path not in metadata_paths, "二進位或 submodule 路徑須保留內容檢查限制。")
            actual = {(e["source"], e["ref"]) for e in citations}
            ev.require(expected[path] <= actual, f"缺少此路徑在各比較範圍的固定證據：{path}")
        else:
            entry["reason"] = text(item.get("reason"), "未完整覆蓋原因")
        covered[path] = entry
    ev.require(set(covered) == set(expected), "coverage 未列出所有變更路徑及改名前的路徑。")
    result["coverage"] = list(covered.values())
    reviewed = {p for p, item in covered.items() if item["status"] == "reviewed"}
    previous_priority = -1
    final_pair = final_version(manifest)
    risks = []
    for number, item in enumerate(draft["code_risks"], 1):
        ev.require(isinstance(item, dict) and item.get("id") == f"R-{number:03}", "程式風險編號須連續。")
        priority = item.get("priority")
        ev.require(priority in PRIORITIES, "程式風險等級無效。")
        order = PRIORITIES.index(priority)
        ev.require(order >= previous_priority, "程式風險須依 P0 至 P3 排序。")
        previous_priority = order
        risk = {k: text(item.get(k), k) for k in
                ("id", "priority", "title", "trigger", "impact", "recommendation", "scenario_source")}
        ev.require(risk["scenario_source"] in {"code-derived", "reproduced"}, "情境來源無效。")
        ev.require(risk["scenario_source"] != "reproduced" or bool(result["tests_executed"]),
                   "標示已重現時須記錄實際執行的檢查。")
        risk["evidence"] = validate_citations(item.get("evidence", []), manifest, context)
        ev.require(any(e["repo"] == "entry" and e["path"] in reviewed for e in risk["evidence"]),
                   "程式風險至少須引用一個已審查的變更路徑。")
        ev.require(any(e["repo"] == "entry" and (e["source"], e["ref"]) == final_pair for e in risk["evidence"]),
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
            ev.require(isinstance(item, dict) and item.get("id") == f"{prefix}-{number:03}",
                       f"{field} 編號須連續。")
            change = {k: text(item.get(k), k) for k in
                      ("id", "kind", "name", "description", "baseline_reason", "recommendation")}
            ev.require(change["kind"] in kinds, f"{field} 類型無效。")
            change["introduction"] = item.get("introduction", "first-use")
            ev.require(change["introduction"] in INTRODUCTIONS, "新增依賴判定類型無效。")
            change["evidence"] = validate_citations(item.get("evidence", []), manifest, context)
            ev.require(any(e["repo"] == "entry" and (e["source"], e["ref"]) == final_pair for e in change["evidence"]),
                       "新增依賴須引用最終受審版本中的使用、目標或部署定義位置。")
            identifiers = item.get("baseline_searches")
            ev.require(isinstance(identifiers, list) and bool(identifiers),
                       "新增使用須附基礎版本整個 Repo 的搜尋紀錄。")
            for identifier in identifiers:
                if identifier not in searches:
                    searches[identifier] = ev.validate_baseline_search(context, manifest, identifier)
            change["baseline_searches"] = identifiers
            if field == "config_changes":
                status = item.get("definition_status")
                ev.require(status in CONFIG_STATUSES, "設定定義狀態無效。")
                change["definition_status"] = status
                change["definition_evidence"] = validate_citations(
                    item.get("definition_evidence", []), manifest, context)
                if status == "defined":
                    ev.require(any(e["repo"] == "entry" and (e["source"], e["ref"]) == final_pair
                                   for e in change["definition_evidence"]),
                               "已有定義須附最終版本中的設定定義證據。")
                identity = (change["kind"], change["name"])
            else:
                change["connection"] = text(item.get("connection"), "connection")
                change["resolution"] = item.get("resolution")
                ev.require(change["resolution"] in {"identified", "unknown"}, "資源辨識狀態無效。")
                identity = (change["connection"], change["kind"], change["name"])
            ev.require(identity not in identities, "同一新增使用項目不應重複列出。")
            identities.add(identity)
            changes.append(change)
        result[field] = changes
    result["baseline_searches"] = [{"id": identifier, "query": record["query"],
                                   "repo": record["repo"], "ref": record["ref"],
                                   "ignore_case": record["ignore_case"],
                                   "match_count": len(record["matches"])}
                                  for identifier, record in searches.items()]
    result.update(chain_review.validate(draft, manifest, context, validate_citations))
    unrelated = {c["repo"] for c in result["supporting_coverage"] if c["status"] == "unrelated"}
    ev.require(not any(e["repo"] in unrelated for risk in risks for e in risk["evidence"]), "風險不能引用已判定無關的 Repo。")
    categories, resource_complete = {}, True
    for item in draft["dependency_coverage"]:
        ev.require(isinstance(item, dict), "依賴覆蓋項目須為物件。")
        kind = item.get("kind")
        ev.require(kind in {"config", "db", *EXTERNAL_KINDS} and kind not in categories, "依賴覆蓋類型重複或無效。")
        status = item.get("status")
        ev.require(status in {"reviewed", "not-reviewed"}, "依賴覆蓋狀態無效。")
        citations = validate_citations(item.get("evidence", []), manifest, context)
        if status == "reviewed" and expected:
            ev.require(any(e["repo"] == "entry" and (e["source"], e["ref"]) == final_pair for e in citations),
                       "依賴覆蓋須有最終受審版本證據。")
        categories[kind] = {"kind": kind, "status": status, "reason": text(item.get("reason"), "依賴覆蓋說明"), "evidence": citations}
        resource_complete = resource_complete and status == "reviewed"
    ev.require(set(categories) == {"config", "db", *EXTERNAL_KINDS}, "缺少設定、DB 或外部資源類型覆蓋。")
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
    complete = complete and not unresolved and result["chain_complete"] and resource_complete
    result["review_complete"] = complete
    result["status"] = "靜態檢查完成" if complete else "檢查未完成"
    if complete and not expected:
        result["status"] = "沒有差異"
    result["identity"] = {key: manifest.get(key) for key in
                          ("project", "repo", "base_input", "head_input", "base_sha", "head_sha",
                           "merge_base", "diff_base", "mode", "generated_at", "working_tree_unchanged")}
    result["identity"].update({"final_source": final_pair[0], "final_ref": final_pair[1]})
    result["identity"].update({"scope": manifest["scope"], "entry_repo": "entry", "repositories": manifest["repositories"]})
    result["context_gaps"] = manifest["context_gaps"]
    return result


def redact(value):
    """Remove common credential formats; reviewers must also omit secret values."""
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if not isinstance(value, str):
        return value
    value = re.sub(r"(?i)(\b(?:password|pwd|token|api[_-]?key|secret|access[_-]?key)\b"
                   r"\s*[=:]\s*)(?:\"[^\"]*\"|'[^']*'|[^;,\s&]+)",
                   r"\1[已遮蔽]", value)
    value = re.sub(r"(?i)([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@", r"\1[已遮蔽]@", value)
    return re.sub(r"(?i)(\bBearer\s+)\S+", r"\1[已遮蔽]", value)


def cell(value):
    return str(value).replace("|", r"\|").replace("\r", " ").replace("\n", " ")


def citations(items):
    lines = []
    for item in items:
        location = item["repo"] + "/" + item["path"]
        if "line_start" in item:
            location += f":{item['line_start']}-{item['line_end']}"
        lines.append(f"- {location}；來源 {item['source']}；版本 {item['ref']}")
    return lines


def render_markdown(result):
    lines = ["# 上板檢查報告", "", f"**檢查狀態：{result['status']}**", "", result["summary"], "",
             f"- 程式風險：{len(result['code_risks'])} 項",
             f"- 新增設定提醒：{len(result['config_changes'])} 項",
             f"- 新增 DB 使用提醒：{len(result['db_changes'])} 項", "",
              f"- 新增外部資源提醒：{len(result['external_changes'])} 項",
              f"- 環境核對：{result['environment_status']}；{len(result['checklist'])} 項初始待確認", "",
              "本報告依固定版本做靜態檢查；靜態檢查完成不等於上板準備完成。實際環境未查證。",
              "", "## 上板前優先事項", ""]
    lines += [f"- {r['id']} {r['priority']}：{r['title']}" for r in result["code_risks"] if r["priority"] in {"P0", "P1"}]
    lines += [f"- 檢查缺口：{g}" for g in result["context_gaps"] + result["chain_gaps"]]
    lines += [f"- 未完成關聯審查：{c['repo']}；{c['reason']}" for c in result["supporting_coverage"] if c["status"] == "not-reviewed"]
    lines += [f"- 未完成依賴檢查：{c['kind']}；{c['reason']}" for c in result["dependency_coverage"] if c["status"] == "not-reviewed"]
    if result["checklist"]:
        destination = result.get("checklist_filename", "同次審查的 Checklist Markdown")
        lines += [f"- 人工核對清單：[{destination}]({destination})；申請工單不等於已開通，完成須保存核對證據。"]
    lines += ["", "## 程式風險", ""]
    if not result["code_risks"]:
        lines += ["已檢查範圍未發現有證據的程式缺陷；檢查缺口見後文。", ""]
    for risk in result["code_risks"]:
        lines += [f"### {risk['id']}｜{risk['priority']} {LABELS[risk['priority']]}｜{risk['title']}", "",
                  f"**觸發條件**：{risk['trigger']}", "", f"**影響**：{risk['impact']}", "",
                  f"**建議處理**：{risk['recommendation']}", "",
                  "**情境來源**：" + ("依程式推導，未實際執行" if risk["scenario_source"] == "code-derived"
                                  else "已實際重現，執行紀錄見後文"), "", "**技術證據**：", "",
                  *citations(risk["evidence"]), ""]
    lines += ["## 新增設定提醒", "",
              "| 編號 | 設定項目 | 狀態 | 使用說明 |", "| --- | --- | --- | --- |"]
    for c in result["config_changes"]:
        lines += [f"| {c['id']} | {cell(c['name'])} | {CONFIG_STATUSES[c['definition_status']]} | {cell(c['description'])} |"]
    if not result["config_changes"]:
        lines += ["| — | 未辨識到新增使用項目 | — | 已知限制見後文 |"]
    lines += ["", "## 新增 DB 使用提醒", "",
              "| 編號 | 類型 | DB／物件 | 連線識別 | 使用說明 |", "| --- | --- | --- | --- | --- |"]
    for d in result["db_changes"]:
        status = "（待確認）" if d["resolution"] == "unknown" else ""
        lines += [f"| {d['id']} | {DB_KINDS[d['kind']]} | {cell(d['name'])}{status} | {cell(d['connection'])} | {cell(d['description'])} |"]
    if not result["db_changes"]:
        lines += ["| — | — | 未辨識到新增使用項目 | — | 已知限制見後文 |"]
    lines += ["", "## 新增外部資源提醒", "",
              "| 編號 | 類型 | 資源 | 連線識別 | 使用說明 |", "| --- | --- | --- | --- | --- |"]
    for dependency in result["external_changes"]:
        lines += [f"| {dependency['id']} | {dependency['kind']} | {cell(dependency['name'])} | {cell(dependency['connection'])} | {cell(dependency['description'])} |"]
    if not result["external_changes"]:
        lines += ["| — | — | 未辨識到新增使用項目 | — | 已知限制見後文 |"]
    lines += ["", "## 上板提醒明細", ""]
    if not result["config_changes"] and not result["db_changes"] and not result["external_changes"]:
        lines += ["本次沒有新增使用提醒。", ""]
    for c in result["config_changes"] + result["db_changes"] + result["external_changes"]:
        lines += [f"<a id=\"{c['id']}\"></a>", "", f"### {c['id']}｜{c['name']}", "",
                  f"**新增類型**：{c['introduction']}", "", f"**新增判定**：{c['baseline_reason']}", "",
                  f"**上板前提醒**：{c['recommendation']}", "", "**程式使用證據**：", "",
                  *citations(c["evidence"]), ""]
        lines += ["**Checklist**：" + "、".join(c["checklist_ids"]), ""]
        if c.get("definition_evidence"):
            lines += ["**設定定義證據**：", "", *citations(c["definition_evidence"]), ""]
        records = {record["id"]: record for record in result["baseline_searches"]}
        lines += ["**基礎版本全 Repo 搜尋**：", ""]
        for identifier in c["baseline_searches"]:
            record = records[identifier]
            lines += [f"- 搜尋 {cell(record['query'])}；符合 {record['match_count']} 處；紀錄 {identifier}"]
        lines.append("")
    lines += ["## Group 功能鏈", "",
              "| Repo | 本機預設分支 | 固定版本 | 狀態 |", "| --- | --- | --- | --- |"]
    for repo in result["identity"]["repositories"]:
        lines += [f"| {cell(repo['id'] + ' / ' + repo['name'])} | {cell(repo.get('default_branch') or '入口指定分支')} | {repo.get('ref') or '見入口版本／未取得'} | {repo['status']} |"]
    for coverage in result["supporting_coverage"]:
        lines += ["", f"**{coverage['repo']}：{coverage['status']}**；{coverage['reason']}", "", *citations(coverage["evidence"])]
    for link in result["functional_chain"]:
        lines += ["", f"### {link['id']}｜{link['from_repo']} → {link['to_repo']}｜{link['kind']}", "",
                  f"{link['status']}：{link['description']}", "", *citations(link["evidence"])]
        if link.get("reason"):
            lines += ["", link["reason"]]
    for record in result["supporting_searches"]:
        lines += [f"- 關聯搜尋：{record['repo']}；版本 {record['ref']}；{cell(record['query'])}；命中 {record['match_count']}；紀錄 {record['id']}"]
    lines += ["", "## 下一步", ""]
    lines += [f"- {s}" for s in result["next_steps"]] or ["- 依本次提醒核對上板準備事項。"]
    lines += ["", "## 覆蓋、驗證與限制", "",
              "| 路徑 | 覆蓋狀態 | 未完成原因 |", "| --- | --- | --- |"]
    for c in result["coverage"]:
        lines += [f"| {cell(c['path'])} | {c['status']} | {cell(c.get('reason', ''))} |"]
    if not result["coverage"]:
        lines += ["| — | 沒有變更路徑 | — |"]
    lines += ["", "**依賴類型覆蓋**：", ""]
    lines += [f"- {c['kind']}：{c['status']}；{c['reason']}" for c in result["dependency_coverage"]]
    for title, values in (("已執行檢查", result["tests_executed"]),
                          ("未執行檢查", result["tests_not_executed"]),
                          ("證據缺口", result["context_gaps"]),
                          ("限制", result["limitations"])):
        lines += ["", f"**{title}**：", "", *([f"- {v}" for v in values] or ["- 無紀錄。"])]
    lines += ["", "## 固定版本紀錄", ""]
    identity = result["identity"]
    for label, key in (("專案", "project"), ("Repo", "repo"), ("基礎輸入", "base_input"),
                       ("比較輸入", "head_input"), ("基礎完整 SHA", "base_sha"),
                       ("比較完整 SHA", "head_sha"), ("共同起點", "merge_base"),
                       ("變更起點", "diff_base"), ("比較方式", "mode"),
                       ("最終內容來源", "final_source"), ("最終內容完整 SHA", "final_ref"),
                       ("擷取時間（UTC）", "generated_at"), ("本機檔案與暫存區維持原狀", "working_tree_unchanged")):
        lines += [f"- {label}：{identity.get(key)}"]
    lines += ["", "版本比對只包含已提交內容；本機未提交修改不在本次範圍內。", ""]
    return "\n".join(lines)


def render_checklist(result, report_filename):
    identity = result["identity"]
    lines = ["# 上板人工核對 Checklist", "", f"審查報告：[{report_filename}]({report_filename})", "",
             f"Repo：{identity['repo']}", f"基礎分支／SHA：{identity['base_input']} / {identity['base_sha']}",
             f"比較分支／SHA：{identity['head_input']} / {identity['head_sha']}",
             f"最終受審版本：{identity['final_ref']}", "",
             "本清單可人工更新，主報告保留產生時的靜態審查結果。版本變更時重新核對受影響項目。",
             "狀態限待確認／已確認／未通過／不適用。已確認須填核對人、日期與開通／套用後驗證證據；",
             "僅已申請或已送工單仍為待確認。不適用須填具體理由。必要項目未確認或未通過時，上板準備未完成。", ""]
    for repo in identity["repositories"]:
        if repo["role"] == "supporting":
            lines += [f"- 關聯版本：{repo['id']} / {repo['name']}；本機預設分支 {repo.get('default_branch')}；SHA {repo.get('ref') or '未取得'}"]
    for item in result["checklist"]:
        lines += ["", f"## {item['id']}｜{item['dependency_id']}｜{item['resource']}", "",
                  f"來源提醒：[{item['dependency_id']}]({report_filename}#{item['dependency_id']})",
                  f"連線／用途識別：{item['connection']}", "", item["question"], "",
                  "| 欄位 | 核對紀錄 |", "| --- | --- |", "| 狀態 | 待確認 |", "| 核對人 | |", "| 日期 | |",
                  "| 工單／驗證證據 | |", "| 不適用理由 | |"]
    if not result["checklist"]:
        lines += ["未辨識到新增依賴，沒有自動產生的核對項；仍不代表實際環境已驗證。"]
    return "\n".join(lines) + "\n"


def publish_report(context, draft_path, report_dir=None):
    context = Path(context).expanduser().absolute()
    try:
        context = review_session.validate_session(context)
        manifest = ev.load_manifest(context)
        draft_path = Path(draft_path).resolve()
        ev.require(draft_path.is_relative_to(context), "草稿必須位於本次 context 內。")
        draft = json.loads(draft_path.read_text(encoding="utf-8"))
        result = redact(validate_result(draft, manifest, context))
        identity = result["identity"]
        destination = Path(report_dir or Path(manifest["repo"]) / "review-reports").expanduser().resolve()
        ev.require(not destination.is_relative_to(context), "報告不能寫入稍後會清理的 context。")
        destination.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        stem = f"release-review-{stamp}-{manifest['base_sha'][:8]}-{manifest['head_sha'][:8]}"
        for number in range(1000):
            suffix = "" if number == 0 else f"-{number:02}"
            md = destination / f"{stem}{suffix}.md"
            checklist = destination / f"{stem}{suffix}-checklist.md"
            if md.exists() or checklist.exists():
                continue
            result["checklist_filename"] = checklist.name
            body = render_markdown(result)
            metadata = {"schema": result["schema"], **identity, "review_complete": result["review_complete"],
                        "checklist_file": checklist.name,
                        "context_sha256": hashlib.sha256((context / "manifest.json").read_bytes()).hexdigest(),
                        "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest()}
            marker = base64.b64encode(ev.canonical(metadata)).decode("ascii")
            markdown = body + f"\n<!-- release-review-report:{marker} -->\n"
            created = []
            try:
                with md.open("x", encoding="utf-8", newline="\n") as stream:
                    created.append(md)
                    stream.write(markdown)
                with checklist.open("x", encoding="utf-8", newline="\n") as stream:
                    created.append(checklist)
                    stream.write(render_checklist(result, md.name))
            except FileExistsError:
                for path in created:
                    path.unlink(missing_ok=True)
                continue
            except BaseException:
                for path in created:
                    path.unlink(missing_ok=True)
                raise
            return md, checklist
        raise ValueError("找不到可使用的新報告檔名。")
    finally:
        review_session.cleanup_if_owned(context)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context-dir", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--report-dir")
    args = parser.parse_args(argv)
    try:
        md, checklist = publish_report(args.context_dir, args.result, args.report_dir)
    except (ValueError, OSError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        print(f"release-report: 未建立報告：{exc}", file=sys.stderr)
        return 2
    print(json.dumps({"markdown_report": str(md), "checklist_report": str(checklist)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
