import base64
import copy
import hashlib
import json
import re

from support import *


class ReportTests(RepoTest):
    def valid_draft(self):
        context, manifest = self.capture()
        draft = draft_for(manifest)
        draft["code_risks"] = [risk_item(manifest)]
        draft["config_changes"] = [config_item(context, manifest)]
        draft["db_changes"] = [db_item(context, manifest)]
        return context, manifest, draft

    def test_three_sections_consistent_json_markdown_and_cleanup(self):
        context, manifest, draft = self.valid_draft()
        md, js = release_report.publish_report(context, save_draft(context, draft),
                                               self.root / "reports", include_json=True)
        result = json.loads(js.read_text(encoding="utf-8"))
        self.assertEqual(result["schema"], "ReleaseReviewReport/v1")
        self.assertEqual([len(result[k]) for k in ("code_risks", "config_changes", "db_changes")], [1, 1, 1])
        self.assertNotIn("CoreDb.dbo.Orders", [d["name"] for d in result["db_changes"]])
        self.assertTrue(result["review_complete"])
        self.assertFalse(context.exists())
        document = md.read_text(encoding="utf-8")
        marker = re.search(r"<!-- release-review-report:([A-Za-z0-9+/=]+) -->", document).group(1)
        metadata = json.loads(base64.b64decode(marker))
        self.assertEqual(metadata["body_sha256"], hashlib.sha256(result["report_body"].encode()).hexdigest())
        self.assertEqual(document.split("\n<!-- release-review-report:")[0], result["report_body"])
        self.assertEqual(metadata["head_sha"], manifest["head_sha"])

    def test_default_markdown_only(self):
        context, manifest = self.capture()
        md, js = release_report.publish_report(context, save_draft(context, draft_for(manifest)),
                                               self.root / "reports")
        self.assertTrue(md.is_file())
        self.assertIsNone(js)
        self.assertEqual(list(md.parent.glob("*.json")), [])

    def test_missing_coverage_rejected_and_session_cleaned(self):
        context, manifest, draft = self.valid_draft()
        draft["coverage"] = draft["coverage"][:1]
        with self.assertRaisesRegex(ValueError, "所有變更路徑"):
            release_report.publish_report(context, save_draft(context, draft), self.root / "reports")
        self.assertFalse(context.exists())
        self.assertFalse((self.root / "reports").exists())

    def test_wrong_citation_sha_and_line_rejected(self):
        context, manifest, draft = self.valid_draft()
        for bad in ({"ref": "0" * 40}, {"line_end": 10000}, {"line_start": True}):
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

    def test_secret_values_redacted_from_both_outputs(self):
        context, manifest, draft = self.valid_draft()
        draft["summary"] = "Password=ExampleSecret; token=ExampleToken; https://user:ExamplePass@host/path"
        md, js = release_report.publish_report(context, save_draft(context, draft),
                                               self.root / "reports", include_json=True)
        for path in (md, js):
            output = path.read_text(encoding="utf-8")
            for secret in ("ExampleSecret", "ExampleToken", "ExamplePass"):
                self.assertNotIn(secret, output)
            self.assertIn("已遮蔽", output)

    def test_output_failure_still_cleans_session_and_preserves_other_files(self):
        context, manifest = self.capture()
        obstruction = self.root / "not-a-directory"
        obstruction.write_text("keep", encoding="utf-8")
        with self.assertRaises(OSError):
            release_report.publish_report(context, save_draft(context, draft_for(manifest)), obstruction)
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
