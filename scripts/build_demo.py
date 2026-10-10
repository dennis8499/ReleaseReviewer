#!/usr/bin/env python3
"""Generate a source-preserved example from a manually assessed review fixture."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from support import (BASE_FILES, HEAD_FILES, baseline_search, capture, citation, config_item,
                     db_item, draft_for, evidence, git, init_repo, release_report,
                     review_session, risk_item, save_draft, write)

BASE_APP = '''from existing import read_orders
from service import average

def calculate_order_stats(db, total, count):
    read_orders(db)
    return average(total, count)
'''
HEAD_APP = BASE_APP + '''
from service import billing_timeout, discount, invoice_url, query_tax

def prepare_invoice(db):
    tax = query_tax(db)
    timeout = billing_timeout()
    return {"tax": tax, "timeout": timeout, "discount": discount(), "url": invoice_url()}
'''


def main():
    root = Path(tempfile.mkdtemp(prefix="release-reviewer-demo-")).resolve()
    context = None
    destination = ROOT / "docs/demo"
    try:
        repo = init_repo(root)
        for path, value in {**BASE_FILES, "src/application.py": BASE_APP}.items():
            write(repo, path, value)
        git(repo, "add", ".")
        git(repo, "commit", "-m", "baseline demo application")
        git(repo, "switch", "-c", "feature")
        for path, value in {**HEAD_FILES, "src/application.py": HEAD_APP}.items():
            write(repo, path, value)
        git(repo, "add", ".")
        git(repo, "commit", "-m", "invoice release demo")
        context, manifest = capture(repo)
        # Preserve exactly the fixed source that was inspected, not a working-tree copy.
        for source in ("base", "head"):
            for path in evidence.list_files(manifest, context, source):
                write(destination / source, path, evidence.read_blob(manifest, context, source, path))
        base_namespace = {}
        exec(compile(BASE_FILES["src/service.py"], "fixture-base.py", "exec"), base_namespace)
        head_namespace = {"__file__": str(repo / "src/service.py")}
        head_source = evidence.read_blob(manifest, context, "merge-preview", "src/service.py").decode()
        exec(compile(head_source, str(repo / "src/service.py"), "exec"), head_namespace)
        assert base_namespace["average"](10, 0) == 0
        try:
            head_namespace["average"](10, 0)
        except ZeroDivisionError:
            pass
        else:
            raise AssertionError("fixture regression was not reproduced")
        try:
            head_namespace["billing_timeout"]()
        except KeyError as exc:
            assert exc.args == ("billing_timeout_seconds",)
        else:
            raise AssertionError("missing-config fixture was not reproduced")
        draft = draft_for(manifest)
        draft["summary"] = "發現兩項會讓流程失敗的程式風險；另有三項新增設定及三項 DB 使用資訊需核對上板準備。"
        first = risk_item(manifest)
        first["scenario_source"] = "reproduced"
        second = {
            "id": "R-002", "priority": "P1", "title": "新增發票流程讀取未定義設定而失敗",
            "trigger": "發票準備流程讀取檔案內的必要逾時設定。",
            "impact": "設定檔沒有該鍵，讀取時發生例外，發票準備流程中斷。",
            "recommendation": "開發補齊必要設定，或依產品需求提供可用的預設行為。",
            "scenario_source": "reproduced",
            "evidence": [citation(manifest, "merge-preview", "src/service.py", 5, 11),
                         citation(manifest, "merge-preview", "src/settings.json", 1),
                         citation(manifest, "merge-preview", "src/application.py", 10, 12)]}
        draft["code_risks"] = [first, second]
        missing = config_item(context, manifest, "billing_timeout_seconds", "missing-definition")
        missing.update({"kind": "config-key", "description": "新增發票流程必要的逾時設定，讀取時沒有預設值。",
                        "recommendation": "開發補齊版本內設定；對應的實際失敗另見 R-002。",
                        "evidence": [citation(manifest, "merge-preview", "src/service.py", 10, 11)]})
        external = config_item(context, manifest)
        external["id"] = "C-002"
        defined = {
            "id": "C-003", "kind": "config-key", "name": "discount_rate",
            "description": "新增折扣設定；比較版本的設定檔已提供定義。",
            "baseline_reason": "基礎版本整個 Repo 未找到此設定的程式使用或定義。",
            "baseline_searches": [baseline_search(context, manifest, "discount_rate")],
            "recommendation": "維運依本次版本核對折扣設定；已有版本內定義。",
            "evidence": [citation(manifest, "merge-preview", "src/service.py", 16, 17)],
            "definition_status": "defined",
            "definition_evidence": [citation(manifest, "merge-preview", "src/settings.json", 1)]}
        draft["config_changes"] = [missing, external, defined]
        table = db_item(context, manifest)
        table["id"] = "D-002"
        database = copy.deepcopy(table)
        database.update({"id": "D-001", "kind": "database", "name": "BillingDb",
                         "description": "新增發票流程開始查詢 BillingDb 內的稅率資料。",
                         "baseline_searches": [baseline_search(context, manifest, "BillingDb")]})
        column = copy.deepcopy(table)
        column.update({"id": "D-003", "kind": "column", "name": "BillingDb.dbo.InvoiceTax.tax_rate",
                       "description": "新增讀取稅率欄位。",
                       "baseline_searches": [baseline_search(context, manifest, "tax_rate")]})
        draft["db_changes"] = [database, table, column]
        draft["tests_executed"] = [
            "僅對本示範 fixture 重現：base 的 average(10, 0) 回傳 0；最終版本發生 ZeroDivisionError。",
            "僅對本示範 fixture 重現：billing_timeout() 發生 KeyError('billing_timeout_seconds')。"]
        draft["tests_not_executed"] = ["未查詢 DB、未連線外部服務、未執行部署或實際環境檢查。"]
        draft["next_steps"] = [
            "開發先修正零數量計算及未定義設定造成的失敗。",
            "維運確認發票服務位址是否需覆寫預設值，以及 BillingDb 稅率物件的上板準備。",
            "QA 在修正後驗證零數量與發票準備流程。"]
        draft["limitations"] = [
            "本報告是人工判斷的測試案例示範；不代表已審查使用者的實際 Repo。",
            "DB 物件名稱可由固定 SQL 辨識；實際連線由呼叫端傳入，環境權限與物件建置狀態未查證。",
            "CoreDb.dbo.Orders 已在 base 的 src/existing.py 使用；本次新增呼叫不重複列為新 DB 使用。",
            "BillingDb 的 base 命中只有 schema.sql 定義；本次才新增執行程式查詢，因此保留首次使用提醒。"]
        expected = release_report.validate_result(draft, manifest, context)
        assert len(expected["code_risks"]) == 2
        assert len(expected["config_changes"]) == 3
        assert len(expected["db_changes"]) == 3
        assert not any("CoreDb" in d["name"] for d in expected["db_changes"])
        md, js = release_report.publish_report(context, save_draft(context, draft),
                                               destination, include_json=True)
        md.replace(destination / "report.md")
        js.replace(destination / "report.json")
        print(json.dumps({"report": str(destination / "report.md"),
                          "code_risks": 2, "config_changes": 3, "db_changes": 3}, ensure_ascii=False))
    finally:
        if context:
            review_session.cleanup_if_owned(context)
        temp = Path(tempfile.gettempdir()).resolve()
        if root == temp or not root.is_relative_to(temp):
            raise ValueError("refusing cleanup outside the temporary root")
        review_session._remove_tree(root)


if __name__ == "__main__":
    main()
