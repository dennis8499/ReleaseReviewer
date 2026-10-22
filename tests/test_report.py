import base64
import copy
import hashlib
import json
import re
from unittest.mock import patch

from support import *


class ReportTests(RepoTest):
    def valid_draft(self):
        context, manifest = self.capture()
        draft = draft_for(manifest)
        draft["code_risks"] = [risk_item(manifest)]
        draft["config_changes"] = [config_item(context, manifest)]
        draft["db_changes"] = [db_item(context, manifest)]
        return context, manifest, draft

    def test_report_checklist_metadata_and_cleanup(self):
        context, manifest, draft = self.valid_draft()
        result = release_report.validate_result(draft, manifest, context)
        md = release_report.publish_report(context, save_draft(context, draft))
        self.assertEqual(result["schema"], "ReleaseReviewReport/v3")
        self.assertEqual([len(result[k]) for k in ("code_risks", "config_changes", "db_changes")], [1, 1, 1])
        self.assertNotIn("CoreDb.dbo.Orders", [d["name"] for d in result["db_changes"]])
        self.assertTrue(result["review_complete"])
        self.assertFalse(context.exists())
        document = md.read_text(encoding="utf-8")
        marker = re.search(r"<!-- release-review-report:([A-Za-z0-9+/=]+) -->", document).group(1)
        metadata = json.loads(base64.b64decode(marker))
        body = document.split("\n<!-- release-review-report:")[0]
        self.assertEqual(metadata["static_body_sha256"], hashlib.sha256(release_report.static_body(body).encode()).hexdigest())
        self.assertNotIn("checklist_file", metadata)
        self.assertEqual("blocked", metadata["release_assessment"])
        self.assertEqual(metadata["head_sha"], manifest["head_sha"])
        self.assertIn(manifest["head_sha"], document)
        edited = body.replace("| 待確認 |", "| 已確認 |", 1)
        self.assertNotEqual(body, edited)
        self.assertEqual(release_report.static_body(body), release_report.static_body(edited))
        md.write_text(document.replace(body, edited), encoding="utf-8")
        self.assertEqual(metadata["static_body_sha256"], hashlib.sha256(release_report.static_body(edited).encode()).hexdigest())
        self.assertNotEqual(metadata["static_body_sha256"],
                            hashlib.sha256(release_report.static_body(edited.replace("原本回傳零", "篡改原本回傳零")).encode()).hexdigest())

    def test_default_markdown_only(self):
        context, manifest = self.capture()
        md = release_report.publish_report(context, save_draft(context, draft_for(manifest)))
        self.assertTrue(md.is_file())
        self.assertEqual(self.repo / "review-reports", md.parent)
        self.assertEqual([md], list((self.repo / "review-reports").glob("release-review-*.md")))
        self.assertEqual(list(md.parent.glob("*.json")), [])

    def test_reference_layout_keeps_authored_results_and_deduplicates_merged_prose(self):
        context, manifest, draft = self.valid_draft()
        draft["report_layout"] = "engineering-review/v2"
        draft["next_steps"] = []
        draft["tests_executed"] = ["執行固定案例檢查並通過。"]
        risk = draft["code_risks"][0]
        note = "依程式推導，未實際重現。"
        risk["scenario"] = risk["trigger"] + "\n\n" + note
        risk["technical_details"]["explanation"] += note
        risk["technical_details"]["impact_scope"] = risk["impact"] + "\n\n零數量統計呼叫。"
        risk["recommendation"] = "恢復零數量處理。\n\n補回傳值驗證。"
        risk["technical_details"]["engineering_fix"] = "恢復零數量處理。\n\n保留既有函式契約。"
        result = release_report.validate_result(draft, manifest, context)
        body = release_report.render_markdown(result)
        problem = body.split("## 問題詳情（工程師參考）\n", 1)[1].split("## 範圍與限制", 1)[0]
        self.assertEqual(1, problem.count(note))
        for repeated in (risk["trigger"], risk["impact"], "恢復零數量處理。"):
            self.assertEqual(1, problem.count(repeated))
        for unique in ("影響範圍：零數量統計呼叫。", "補回傳值驗證。", "保留既有函式契約。", risk["expected"], risk["actual"]):
            self.assertIn(unique, problem)
        self.assertLess(problem.index("**證據**"), problem.index("移除 count == 0 防護"))
        self.assertIn(f"`{self.repo.name}/src/service.py:7-8`", problem)
        self.assertNotIn("entry/src/service.py", problem)
        self.assertIn("1: def average(total, count):", problem)
        self.assertIn("8:     return total / count", problem)
        self.assertIn("問題數量：P0 0／P1 1", body)
        self.assertIn("合併衝突 0 項", body)
        self.assertIn("### 下一步\n\n本次沒有另外列出的下一步。", body)
        self.assertLess(body.index(draft["tests_executed"][0]), body.index("<details>"))
        self.assertLess(body.index(draft["tests_not_executed"][0]), body.index("<details>"))
        self.assertNotIn("\n\n\n", body)

    def test_missing_coverage_rejected_and_session_cleaned(self):
        context, manifest, draft = self.valid_draft()
        draft["coverage"] = draft["coverage"][:1]
        with self.assertRaisesRegex(ValueError, "所有變更路徑"):
            release_report.publish_report(context, save_draft(context, draft))
        self.assertFalse(context.exists())
        self.assertEqual([], list(self.repo.glob("release-review-*.md")))

    def test_wrong_citation_sha_and_line_rejected(self):
        context, manifest, draft = self.valid_draft()
        for bad in ({"ref": "0" * 40}, {"line_end": 10000}, {"line_start": True}, {"repo": ""}, {"repo": "unknown-repo"}):
            changed = copy.deepcopy(draft)
            changed["code_risks"][0]["evidence"][0].update(bad)
            with self.assertRaises(ValueError):
                release_report.validate_result(changed, manifest, context)

    def test_required_scopes_include_head_and_final_preview(self):
        context, manifest = self.capture()
        draft = draft_for(manifest)
        paths = {entry["path"] for entry in draft["coverage"]}
        self.assertEqual(paths, {"src/service.py", "src/settings.json"})
        service = next(c for c in draft["coverage"] if c["path"] == "src/service.py")
        service["evidence"] = [citation(manifest, "head", "src/service.py")]
        with self.assertRaisesRegex(ValueError, "各比較範圍"):
            release_report.validate_result(draft, manifest, context)

    def test_defined_config_requires_final_definition_evidence(self):
        context, manifest, draft = self.valid_draft()
        item = draft["config_changes"][0]
        item["definition_status"] = "defined"
        with self.assertRaisesRegex(ValueError, "定義證據"):
            release_report.validate_result(draft, manifest, context)
        item["definition_evidence"] = [citation(manifest, "merge-preview", "src/settings.json", 1)]
        self.assertTrue(release_report.validate_result(draft, manifest, context)["review_complete"])

    def test_baseline_search_required_and_tampering_rejected(self):
        context, manifest, draft = self.valid_draft()
        changed = copy.deepcopy(draft)
        changed["db_changes"][0]["baseline_searches"] = []
        with self.assertRaisesRegex(ValueError, "搜尋紀錄"):
            release_report.validate_result(changed, manifest, context)
        identifier = draft["db_changes"][0]["baseline_searches"][0]
        record_path = context / "searches" / f"{identifier}.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["matches"] = []
        record_path.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "摘要"):
            release_report.validate_result(draft, manifest, context)

    def test_head_search_cannot_replace_whole_baseline_search(self):
        context, manifest, draft = self.valid_draft()
        identifier = evidence.record_search(context, evidence.search(manifest, context, "head", "InvoiceTax"))
        draft["db_changes"][0]["baseline_searches"] = [identifier]
        with self.assertRaisesRegex(ValueError, "基礎版本整個"):
            release_report.validate_result(draft, manifest, context)

    def test_unknown_database_or_config_keeps_incomplete(self):
        for field in ("config_changes", "db_changes"):
            context, manifest, draft = self.valid_draft()
            if field == "config_changes":
                draft[field][0]["definition_status"] = "unknown"
            else:
                draft[field][0]["resolution"] = "unknown"
            result = release_report.validate_result(draft, manifest, context)
            self.assertFalse(result["review_complete"])
            self.assertEqual(result["status"], "檢查未完成")

    def test_external_config_does_not_claim_missing(self):
        context, manifest, draft = self.valid_draft()
        result = release_report.validate_result(draft, manifest, context)
        self.assertEqual(result["config_changes"][0]["definition_status"], "external")
        self.assertIn("需確認外部注入", release_report.render_markdown(result))
        self.assertTrue(result["review_complete"])

    def test_metadata_only_or_unreviewed_paths_keep_incomplete(self):
        context, manifest = self.capture()
        for status in ("metadata-only", "not-reviewed"):
            draft = draft_for(manifest)
            draft["coverage"][0].update({"status": status, "reason": "本案例保留未覆蓋路徑。", "evidence": []})
            self.assertFalse(release_report.validate_result(draft, manifest, context)["review_complete"])

    def test_duplicate_database_reminders_rejected(self):
        context, manifest, draft = self.valid_draft()
        repeated = copy.deepcopy(draft["db_changes"][0])
        repeated["id"] = "D-002"
        draft["db_changes"].append(repeated)
        with self.assertRaisesRegex(ValueError, "重複"):
            release_report.validate_result(draft, manifest, context)

    def test_reproduced_claim_requires_execution_record(self):
        context, manifest, draft = self.valid_draft()
        draft["code_risks"][0]["scenario_source"] = "reproduced"
        with self.assertRaisesRegex(ValueError, "實際執行"):
            release_report.validate_result(draft, manifest, context)

    def test_secret_values_redacted_from_fixed_text_and_checklist(self):
        context, manifest, draft = self.valid_draft()
        draft["summary"] = "Password=ExampleSecret; token=ExampleToken; https://user:ExamplePass@host/path"
        draft["config_changes"][0]["name"] = "token=ExampleToken"
        md = release_report.publish_report(context, save_draft(context, draft))
        for path in (md,):
            output = path.read_text(encoding="utf-8")
            for secret in ("ExampleSecret", "ExampleToken", "ExamplePass"):
                self.assertNotIn(secret, output)
            self.assertIn("已遮蔽", output)

    def test_no_json_cli_or_prefilled_checklist_completion(self):
        context, manifest, draft = self.valid_draft()
        draft["checklist"] = [{"status": "已確認"}]
        with self.assertRaisesRegex(ValueError, "不能預填"):
            release_report.validate_result(draft, manifest, context)
        result = python(SCRIPTS / "release_report.py", "--context-dir", context, "--result", save_draft(context, draft), "--include-json")
        self.assertNotEqual(0, result.returncode)

    def test_resource_category_omission_is_rejected_and_unreviewed_is_incomplete(self):
        context, manifest = self.capture()
        draft = draft_for(manifest)
        draft["dependency_coverage"].pop()
        with self.assertRaisesRegex(ValueError, "缺少設定"):
            release_report.validate_result(draft, manifest, context)
        draft = draft_for(manifest)
        draft["dependency_coverage"][0].update(status="not-reviewed", reason="動態載入無法解析")
        self.assertFalse(release_report.validate_result(draft, manifest, context)["review_complete"])

    def test_output_failure_still_cleans_session_and_preserves_other_files(self):
        context, manifest = self.capture()
        obstruction = self.root / "not-a-directory"
        obstruction.write_text("keep", encoding="utf-8")
        manifest["report_root"] = str(obstruction)
        (context / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "報告目錄"):
            release_report.publish_report(context, save_draft(context, draft_for(manifest)))
        self.assertFalse(context.exists())
        self.assertEqual(obstruction.read_text(), "keep")

    def test_rename_and_delete_include_old_paths_with_fixed_sources(self):
        git(self.repo, "mv", "schema.sql", "schema-renamed.sql")
        git(self.repo, "rm", "src/existing.py")
        git(self.repo, "commit", "-m", "rename and delete")
        context, manifest = self.capture()
        draft = draft_for(manifest)
        result = release_report.validate_result(draft, manifest, context)
        self.assertIn("schema.sql", [c["path"] for c in result["coverage"]])
        self.assertIn("schema-renamed.sql", [c["path"] for c in result["coverage"]])
        self.assertIn("src/existing.py", [c["path"] for c in result["coverage"]])
        self.assertTrue(result["review_complete"])

    def test_binary_path_is_honestly_marked_as_metadata_only(self):
        write(self.repo, "asset.bin", b"\0\1\2\3")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "binary asset")
        context, manifest = self.capture()
        draft = draft_for(manifest)
        item = next(c for c in draft["coverage"] if c["path"] == "asset.bin")
        item.update({"status": "metadata-only", "reason": "二進位內容未做語意審查。", "evidence": []})
        self.assertFalse(release_report.validate_result(draft, manifest, context)["review_complete"])

    def test_no_difference_report_has_no_reminders(self):
        context, manifest = self.capture(base="main", head="main")
        result = release_report.validate_result(draft_for(manifest), manifest, context)
        self.assertEqual(result["status"], "沒有差異")
        self.assertEqual(result["config_changes"] + result["db_changes"], [])


    def test_only_direct_blockers_are_accepted_and_reason_is_required(self):
        context, manifest, draft = self.valid_draft()
        for priority in ("P2", "P3"):
            changed = copy.deepcopy(draft)
            changed["code_risks"][0]["priority"] = priority
            with self.assertRaisesRegex(ValueError, "P0／P1"):
                release_report.validate_result(changed, manifest, context)
        del draft["code_risks"][0]["blocking_reason"]
        with self.assertRaisesRegex(ValueError, "blocking_reason"):
            release_report.validate_result(draft, manifest, context)

    def test_assessment_separates_blockers_gaps_and_pending_conditions(self):
        context, manifest, draft = self.valid_draft()
        result = release_report.validate_result(draft, manifest, context)
        self.assertEqual("blocked", result["release_assessment"])
        self.assertEqual(1, result["blocker_count"])
        draft["code_risks"] = []
        result = release_report.validate_result(draft, manifest, context)
        self.assertEqual("pending-confirmation", result["release_assessment"])
        self.assertGreater(result["pending_count"], 0)
        draft["config_changes"][0]["definition_status"] = "unknown"
        result = release_report.validate_result(draft, manifest, context)
        self.assertEqual("incomplete", result["release_assessment"])
        self.assertGreater(result["gap_count"], 0)
        draft["code_risks"] = [risk_item(manifest)]
        self.assertEqual("blocked", release_report.validate_result(draft, manifest, context)["release_assessment"])
        clean = release_report.validate_result(draft_for(manifest), manifest, context)
        self.assertEqual("no-blockers-found", clean["release_assessment"])
        self.assertEqual(0, clean["pending_count"])

    def test_conflict_is_a_blocker_even_without_code_risks(self):
        git(self.repo, "switch", "main")
        write(self.repo, "src/service.py", "def average(total, count):\n    return -1\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "conflicting base")
        git(self.repo, "switch", "feature")
        context, manifest = self.capture()
        self.assertEqual("conflicts", manifest["merge_preview"]["status"])
        draft = draft_for(manifest)
        result = release_report.validate_result(draft, manifest, context)
        self.assertEqual("blocked", result["release_assessment"])
        self.assertEqual(1, result["blocker_count"])
        self.assertFalse(result["review_complete"])
        report = release_report.publish_report(context, save_draft(context, draft))
        self.assertIn("M-001", report.read_text(encoding="utf-8"))

    def test_folder_and_group_reports_stay_in_reviewed_repo(self):
        for workspace in (self.repo, self.root):
            with self.subTest(workspace=workspace):
                context, manifest = self.capture(workspace=workspace)
                self.assertEqual(str(self.repo / "review-reports"), manifest["report_root"])
                report = release_report.publish_report(context, save_draft(context, draft_for(manifest)))
                self.assertEqual(self.repo / "review-reports", report.parent)

    def test_saved_workspace_does_not_override_reviewed_repo_destination(self):
        saved = self.root / "saved workspace"
        saved.mkdir()
        workspace_file = saved / "review.code-workspace"
        workspace_file.write_text(json.dumps({"folders": [{"path": str(self.repo)}]}), encoding="utf-8-sig")
        context, manifest = self.capture(workspace=self.root, workspace_file=workspace_file)
        self.assertEqual(str(self.repo / "review-reports"), manifest["report_root"])
        report = release_report.publish_report(context, save_draft(context, draft_for(manifest)))
        self.assertEqual(self.repo / "review-reports", report.parent)
        self.assertEqual([], list(saved.glob("*.md")))
        self.assertEqual([], list(self.repo.glob("release-review-*.md")))

    def test_repeated_runs_do_not_overwrite_human_records(self):
        with patch.object(release_report, "datetime") as clock:
            clock.now.return_value.strftime.return_value = "20261014T000000Z"
            context, manifest, draft = self.valid_draft()
            first = release_report.publish_report(context, save_draft(context, draft))
            manual = first.read_text(encoding="utf-8").replace("| 待確認 |", "| 已確認 |", 1)
            first.write_text(manual, encoding="utf-8")
            context, manifest, draft = self.valid_draft()
            second = release_report.publish_report(context, save_draft(context, draft))
        self.assertNotEqual(first, second)
        self.assertTrue(second.stem.endswith("-01"))
        self.assertEqual(manual, first.read_text(encoding="utf-8"))
        self.assertEqual({first, second}, set((self.repo / "review-reports").glob("release-review-*.md")))
        self.assertNotIn("已確認 |", second.read_text(encoding="utf-8"))

    def test_report_destination_and_json_overrides_are_rejected(self):
        context, manifest = self.capture()
        draft = save_draft(context, draft_for(manifest))
        for option in (("--report-dir", str(self.root)), ("--include-json",)):
            result = python(SCRIPTS / "release_report.py", "--context-dir", context, "--result", draft, *option)
            self.assertNotEqual(0, result.returncode)
        self.assertEqual([], list(self.repo.glob("release-review-*.md")))

    def test_ambiguous_checklist_boundaries_are_rejected(self):
        context, manifest = self.capture()
        body = release_report.render_markdown(release_report.validate_result(draft_for(manifest), manifest, context))
        for bad in (body.replace(release_report.CHECKLIST_END, ""),
                    body + release_report.CHECKLIST_START,
                    body.replace(release_report.CHECKLIST_START, release_report.CHECKLIST_END, 1)):
            with self.assertRaises(ValueError):
                release_report.static_body(bad)


    def test_final_cited_code_survives_divergent_merge_context_cleanup(self):
        git(self.repo, "switch", "main")
        base = git(self.repo, "show", "main:src/existing.py")
        write(self.repo, "src/existing.py", base + "\nBASE_ONLY = True\n")
        git(self.repo, "add", "src/existing.py")
        git(self.repo, "commit", "-m", "base-only update")
        git(self.repo, "switch", "feature")
        context, manifest = self.capture()
        self.assertEqual("clean", manifest["merge_preview"]["status"])
        self.assertNotEqual(manifest["head_sha"], manifest["merge_preview"]["tree_sha"])
        draft = draft_for(manifest)
        draft["code_risks"] = [risk_item(manifest)]
        result = release_report.validate_result(draft, manifest, context)
        proof = result["code_risks"][0]["evidence"][0]
        self.assertIn("8:     return total / count", proof["excerpt"])
        report = release_report.publish_report(context, save_draft(context, draft))
        self.assertFalse(context.exists())
        self.assertEqual("", git(self.repo, "cat-file", "-t", manifest["merge_preview"]["tree_sha"], check=False))
        document = report.read_text(encoding="utf-8")
        self.assertIn("8:     return total / count", document)
        self.assertIn(proof["content_sha256"], document)

    def test_preview_evidence_omits_credentials_and_configuration_values(self):
        write(self.repo, "src/credentials.py",
              'API_TOKEN = "HiddenTokenValue"\nconnection = "Server=db;Password=HiddenPassword"\nsql = "Data Source=HiddenServer;Database=HiddenDatabase"\n')
        write(self.repo, "src/settings.json", '{"ordinary_key": "HiddenConfigValue"}\n')
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "snapshot privacy fixture")
        context, manifest = self.capture()
        draft = draft_for(manifest)
        draft["code_risks"] = [risk_item(manifest)]
        draft["code_risks"][0]["evidence"] += [
            citation(manifest, "merge-preview", "src/credentials.py", 1, 3),
            citation(manifest, "merge-preview", "src/settings.json", 1)]
        report = release_report.publish_report(context, save_draft(context, draft))
        document = report.read_text(encoding="utf-8")
        for value in ("HiddenTokenValue", "HiddenPassword", "HiddenConfigValue", "HiddenServer", "HiddenDatabase"):
            self.assertNotIn(value, document)
        self.assertIn("ordinary_key", document)
        self.assertIn("含認證或連線設定的行已省略", document)
