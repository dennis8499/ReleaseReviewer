"""Published request receipts preserve the one authoritative semantic result."""
import copy
import io
from unittest.mock import patch

from support import *
import frozen_evidence
import git_support
import review_coordinator as coordination
import review_flow as flow


class PublishedOutcomeTests(RepoTest):
    def request(self, *, incomplete=False, pending=False):
        started = flow.start(flow.parser().parse_args([
            "start", "--workspace", str(self.root), "--project", str(self.repo),
            "--base", "main", "--head", "feature"]))
        context = Path(started["context_dir"])
        manifest = evidence.load_manifest(context, full=False)
        draft = draft_for(manifest)
        draft["report_layout"] = "engineering-review/v2"
        if incomplete:
            row = next(row for row in draft["coverage"] if row["path"] == "src/service.py")
            row.update(status="not-reviewed", reason="HEAD behavior evidence remains unreviewed.",
                       evidence=[citation(manifest, "base", "src/service.py")])
        if pending:
            draft["config_changes"] = [config_item(context, manifest)]
        save_draft(context, draft)
        return started, Path(started["runtime_root"]), started["request_id"], context

    def published_reads(self, root, identifier):
        with patch.object(git_support, "run_git", side_effect=AssertionError("Git during published receipt read")), \
             patch.object(evidence, "command", side_effect=AssertionError("Git during published receipt read")), \
             patch.object(release_report, "validate_result", side_effect=AssertionError("second semantic validation")):
            return flow.status(root, identifier), flow.check(root, identifier)

    def check_exit(self, root, identifier):
        with patch("sys.stdout", new=io.StringIO()):
            return flow.main(["check", "--runtime-root", str(root), "--request-id", identifier])

    def test_incomplete_head_gap_survives_cleanup_and_published_zero_git_check(self):
        _, root, identifier, context = self.request(incomplete=True)
        authoritative = []
        original = release_report.validate_result
        def validated(*args, **kwargs):
            result = original(*args, **kwargs)
            authoritative.append(copy.deepcopy(result))
            return result
        with patch.object(release_report, "validate_result", side_effect=validated) as validate, \
             patch.object(frozen_evidence, "validate", wraps=frozen_evidence.validate) as snapshots:
            published, code = flow.finish(root, identifier)
        self.assertEqual(0, code)
        self.assertEqual(1, validate.call_count)
        self.assertEqual(1, snapshots.call_count)
        self.assertFalse(context.exists())
        self.assertFalse(authoritative[0]["review_complete"])
        for value in (published, *self.published_reads(root, identifier)):
            self.assertFalse(value["review_complete"])
            self.assertEqual(authoritative[0]["review_gaps"], value["gaps"])
            self.assertIn("HEAD", " ".join(value["gaps"]))
            self.assertTrue(value["outcome_available"])
        check = flow.check(root, identifier)
        self.assertEqual(0, check["git_commands"])
        self.assertFalse(check["evidence_verified"])
        self.assertEqual(3, self.check_exit(root, identifier))

    def test_complete_outcome_is_preserved_after_cleanup(self):
        _, root, identifier, context = self.request()
        published, code = flow.finish(root, identifier)
        self.assertEqual(0, code)
        self.assertFalse(context.exists())
        for value in (published, *self.published_reads(root, identifier)):
            self.assertTrue(value["review_complete"])
            self.assertEqual([], value["gaps"])
            self.assertEqual("no-blockers-found", value["review_outcome"]["release_assessment"])
        self.assertEqual(0, self.check_exit(root, identifier))

    def test_pending_deployment_checklist_does_not_change_static_completeness(self):
        _, root, identifier, context = self.request(pending=True)
        published, code = flow.finish(root, identifier)
        self.assertEqual(0, code)
        self.assertFalse(context.exists())
        outcome = published["review_outcome"]
        self.assertTrue(outcome["review_complete"])
        self.assertGreater(outcome["pending_count"], 0)
        self.assertEqual("pending-confirmation", outcome["release_assessment"])
        self.assertEqual([], outcome["gaps"])
        self.assertEqual(0, self.check_exit(root, identifier))
        report = Path(published["report_path"])
        body = report.read_text(encoding="utf-8")
        left = body.index(release_report.CHECKLIST_START)
        right = body.index(release_report.CHECKLIST_END)
        manual = body[left:right].replace("待確認", "已確認")
        report.write_text(body[:left] + manual + body[right:], encoding="utf-8")
        for value in self.published_reads(root, identifier):
            self.assertEqual(outcome, value["review_outcome"])
        self.assertEqual(outcome, flow.finish(root, identifier)[0]["review_outcome"])

    def test_legacy_receipt_without_semantic_summary_returns_unknown(self):
        _, root, identifier, _ = self.request()
        flow.finish(root, identifier)
        path = flow.job_path(root, identifier)
        job = coordination.read_json(path)
        job.pop("review_outcome")
        coordination.atomic(path, job)
        for value in self.published_reads(root, identifier):
            self.assertFalse(value["outcome_available"])
            self.assertIsNone(value["review_complete"])
            self.assertEqual("unknown", value["review_status"])
            self.assertTrue(value["gaps"])
        self.assertEqual(3, self.check_exit(root, identifier))

    def test_cleanup_retry_keeps_original_incomplete_outcome(self):
        _, root, identifier, context = self.request(incomplete=True)
        with patch.object(review_session, "cleanup_session", side_effect=PermissionError("cleanup denied")):
            published, code = flow.finish(root, identifier)
        self.assertEqual(0, code)
        self.assertTrue(published["cleanup_pending"])
        original = published["review_outcome"]
        with patch.object(release_report, "validate_result", side_effect=AssertionError("retry must not validate again")):
            retried, code = flow.finish(root, identifier)
        self.assertEqual(0, code)
        self.assertFalse(context.exists())
        self.assertEqual(original, retried["review_outcome"])
        self.assertFalse(retried["review_complete"])

    def test_report_publication_falls_back_to_atomic_rename_when_hardlinks_are_denied(self):
        _, root, identifier, context = self.request(incomplete=True)
        with patch.object(release_report.os, "link", side_effect=PermissionError("hard links denied")):
            published, code = flow.finish(root, identifier)

        self.assertEqual(0, code)
        self.assertEqual("published", published["state"])
        self.assertTrue(Path(published["report_path"]).is_file())
        self.assertFalse(context.exists())
        self.assertFalse(list(Path(published["report_path"]).parent.glob(".release-report-*.tmp")))

    def test_staged_receipt_saves_outcome_before_report_link_and_recovers(self):
        _, root, identifier, _ = self.request(incomplete=True)
        real_link = release_report.os.link
        observed = []
        def linked(source, target):
            job = coordination.read_json(flow.job_path(root, identifier))
            observed.append(copy.deepcopy(job["review_outcome"]))
            self.assertEqual(str(target), job["planned_report"])
            self.assertFalse(job["review_outcome"]["review_complete"])
            return real_link(source, target)
        original_update = flow.update
        def lost(*args, **kwargs):
            if kwargs.get("state") == "published":
                raise OSError("lost published callback")
            return original_update(*args, **kwargs)
        with patch.object(release_report.os, "link", side_effect=linked), patch.object(flow, "update", side_effect=lost):
            with self.assertRaisesRegex(OSError, "lost published"):
                flow.finish(root, identifier)
        self.assertEqual(1, len(observed))
        for value in self.published_reads(root, identifier):
            self.assertEqual(observed[0], value["review_outcome"])
        recovered, code = flow.finish(root, identifier)
        self.assertEqual(0, code)
        self.assertEqual(observed[0], recovered["review_outcome"])


if __name__ == "__main__":
    unittest.main()
