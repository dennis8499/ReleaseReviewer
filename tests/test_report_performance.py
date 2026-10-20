import copy
import json
from unittest.mock import patch

from support import *


class ReportPerformanceTests(RepoTest):
    def full_draft(self):
        context, manifest = self.capture()
        draft = draft_for(manifest)
        draft["code_risks"] = [risk_item(manifest)]
        draft["config_changes"] = [config_item(context, manifest)]
        draft["db_changes"] = [db_item(context, manifest)]
        return context, manifest, draft

    def test_repeated_citations_share_blob_metadata_and_preview_snapshot_per_validation(self):
        context, manifest, draft = self.full_draft()
        with patch.object(evidence, "command", wraps=evidence.command) as commands, \
                patch.object(release_report, "preview_snapshot", wraps=release_report.preview_snapshot) as snapshots, \
                patch.object(evidence.hashlib, "sha256", wraps=evidence.hashlib.sha256) as hashes:
            first = release_report.validate_result(draft, manifest, context)
            reads = [(call.args[0]["repo"], tuple(call.args[1])) for call in commands.call_args_list
                     if call.args[1][0] == "cat-file"]
            self.assertEqual(5, len(reads))
            self.assertEqual(len(reads), len(set(reads)))
            self.assertEqual(5, snapshots.call_count)
            self.assertEqual(2, sum(call.args[0] == HEAD_FILES["src/service.py"].encode()
                                    for call in hashes.call_args_list))
            commands.reset_mock()
            snapshots.reset_mock()
            hashes.reset_mock()
            second = release_report.validate_result(draft, manifest, context)
            self.assertEqual(first, second)
            self.assertEqual(5, sum(call.args[1][0] == "cat-file" for call in commands.call_args_list))
            self.assertEqual(5, snapshots.call_count)
            self.assertEqual(2, sum(call.args[0] == HEAD_FILES["src/service.py"].encode()
                                    for call in hashes.call_args_list))

    def test_cached_content_still_checks_each_citation_identity_and_line_range(self):
        context, manifest = self.capture()
        draft = draft_for(manifest)
        for change in ({"line_start": 1, "line_end": 10000}, {"ref": "0" * 40},
                       {"source": "base"}, {"repo": "missing"}, {"path": "../service.py"}):
            with self.subTest(change=change):
                changed = copy.deepcopy(draft)
                changed["dependency_coverage"][-1]["evidence"][0].update(change)
                with self.assertRaises(release_report.DraftValidationError):
                    release_report.validate_result(changed, manifest, context)

    def test_retryable_draft_error_retains_sources_then_success_validates_once_and_cleans(self):
        context, manifest = self.capture()
        draft = draft_for(manifest)
        broken = copy.deepcopy(draft)
        broken["coverage"].pop()
        with self.assertRaises(release_report.DraftValidationError):
            release_report.publish_report(context, save_draft(context, broken), retain_on_validation_error=True)
        self.assertTrue(context.exists())
        self.assertEqual([], list(self.repo.glob("release-review-*.md")))
        with patch.object(release_report, "validate_result", wraps=release_report.validate_result) as validate:
            report = release_report.publish_report(context, save_draft(context, draft), retain_on_validation_error=True)
        self.assertEqual(1, validate.call_count)
        self.assertTrue(report.is_file())
        self.assertFalse(context.exists())

    def test_legacy_draft_failure_still_cleans(self):
        context, manifest = self.capture()
        draft = draft_for(manifest)
        draft["coverage"].pop()
        with self.assertRaises(release_report.DraftValidationError):
            release_report.publish_report(context, save_draft(context, draft))
        self.assertFalse(context.exists())

    def test_group_missing_reason_and_link_text_can_be_repaired_without_recapture(self):
        context, manifest = self.capture()
        manifest["scope"] = "group"
        manifest["context_complete"] = False
        manifest["context_gaps"] = ["Consumer fixed version is unavailable."]
        manifest["repositories"].append({"id": "repo-001", "name": "consumer", "role": "supporting",
                                         "path": str(self.root / "consumer"), "status": "unavailable",
                                         "reason": "Consumer fixed version is unavailable."})
        (context / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        draft = draft_for(manifest)
        draft["supporting_coverage"] = [{"repo": "repo-001", "status": "not-reviewed",
                                          "evidence": [], "searches": []}]
        with self.assertRaises(release_report.DraftValidationError):
            release_report.publish_report(context, save_draft(context, draft), retain_on_validation_error=True)
        self.assertTrue(context.exists())
        draft["supporting_coverage"][0]["reason"] = "Consumer fixed version is unavailable."
        link = {"id": "F-001", "from_repo": "entry", "to_repo": "repo-001", "kind": "event",
                "status": "not-reviewed", "evidence": [], **diff_anchor(manifest)}
        draft["functional_chain"] = [link]
        with self.assertRaises(release_report.DraftValidationError):
            release_report.publish_report(context, save_draft(context, draft), retain_on_validation_error=True)
        self.assertTrue(context.exists())
        link["description"] = "Entry publishes an event consumed by consumer."
        with self.assertRaises(release_report.DraftValidationError):
            release_report.publish_report(context, save_draft(context, draft), retain_on_validation_error=True)
        self.assertTrue(context.exists())
        link["reason"] = "Consumer fixed version is unavailable."
        report = release_report.publish_report(context, save_draft(context, draft), retain_on_validation_error=True)
        self.assertTrue(report.is_file())
        self.assertFalse(context.exists())

    def test_retryable_cli_diagnostic_and_malformed_json_can_be_repaired(self):
        context, manifest = self.capture()
        draft_path = context / "review-result-draft.json"
        draft_path.write_text('{"schema":', encoding="utf-8")
        failed = python(SCRIPTS / "release_report.py", "--context-dir", context, "--result", draft_path,
                        "--retain-on-validation-error")
        self.assertEqual(3, failed.returncode, failed.stderr)
        self.assertEqual("", failed.stdout)
        diagnostic = json.loads(failed.stderr)
        self.assertTrue(diagnostic["context_retained"])
        self.assertEqual(str(context), diagnostic["context_dir"])
        self.assertIn("JSON", diagnostic["error"])
        self.assertTrue(context.exists())
        succeeded = python(SCRIPTS / "release_report.py", "--context-dir", context,
                           "--result", save_draft(context, draft_for(manifest)), "--retain-on-validation-error")
        self.assertEqual(0, succeeded.returncode, succeeded.stderr)
        self.assertEqual({"markdown_report"}, set(json.loads(succeeded.stdout)))
        self.assertFalse(context.exists())

    def test_retryable_mode_does_not_retain_invalid_manifest(self):
        context, manifest = self.capture()
        draft_path = save_draft(context, draft_for(manifest))
        manifest["schema"] = "invalid"
        (context / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "context schema"):
            release_report.publish_report(context, draft_path, retain_on_validation_error=True)
        self.assertFalse(context.exists())

    def test_retryable_mode_does_not_retain_tampered_search(self):
        context, manifest, draft = self.full_draft()
        identifier = draft["config_changes"][0]["baseline_searches"][0]
        search_path = context / "searches" / f"{identifier}.json"
        record = json.loads(search_path.read_text(encoding="utf-8"))
        record["query"] += "tampered"
        search_path.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "摘要") as raised:
            release_report.publish_report(context, save_draft(context, draft), retain_on_validation_error=True)
        self.assertNotIsInstance(raised.exception, release_report.DraftValidationError)
        self.assertFalse(context.exists())

    def test_retryable_mode_does_not_retain_invalid_preview_object_store(self):
        context, manifest = self.capture()
        draft_path = save_draft(context, draft_for(manifest))
        manifest["merge_preview"]["object_directory"] = "other"
        (context / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "預覽物件目錄") as raised:
            release_report.publish_report(context, draft_path, retain_on_validation_error=True)
        self.assertNotIsInstance(raised.exception, release_report.DraftValidationError)
        self.assertFalse(context.exists())

    def test_retryable_mode_cleans_after_git_timeout(self):
        context, manifest = self.capture()
        draft_path = save_draft(context, draft_for(manifest))
        with patch.object(evidence, "command", side_effect=subprocess.TimeoutExpired("git", 1)):
            with self.assertRaises(subprocess.TimeoutExpired):
                release_report.publish_report(context, draft_path, retain_on_validation_error=True)
        self.assertFalse(context.exists())

    def test_retryable_mode_cleans_after_output_failure_or_interruption(self):
        context, manifest = self.capture()
        draft_path = save_draft(context, draft_for(manifest))
        original_open = Path.open

        def fail_report_open(path, mode="r", *args, **kwargs):
            if mode == "x" and path.suffix == ".md":
                raise OSError("report write failed")
            return original_open(path, mode, *args, **kwargs)

        with patch.object(Path, "open", new=fail_report_open):
            with self.assertRaisesRegex(OSError, "report write failed"):
                release_report.publish_report(context, draft_path, retain_on_validation_error=True)
        self.assertFalse(context.exists())
        self.assertEqual([], list(self.repo.glob("release-review-*.md")))

        context, manifest = self.capture()
        draft_path = save_draft(context, draft_for(manifest))
        with patch.object(release_report, "validate_result", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                release_report.publish_report(context, draft_path, retain_on_validation_error=True)
        self.assertFalse(context.exists())
