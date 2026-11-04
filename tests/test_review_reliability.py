"""Exercise frozen-review recovery, cheap authoring hints and read-only batches."""
import contextlib
import io
from unittest.mock import patch

from support import *
import git_support
import review_coordinator as coordination
import review_flow as flow
import review_runtime
import review_worklist


class ReviewReliabilityTests(RepoTest):
    def start(self, *extra):
        return flow.start(flow.parser().parse_args([
            "start", "--workspace", str(self.root), "--project", str(self.repo),
            "--base", "main", "--head", "feature", *extra]))

    def authored(self, started):
        context = Path(started["context_dir"])
        manifest = evidence.load_manifest(context, full=False)
        draft = draft_for(manifest)
        save_draft(context, draft)
        return context, manifest, draft

    def cli(self, *arguments):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = flow.main(list(arguments))
        return code, json.loads(out.getvalue() or err.getvalue())

    def test_capabilities_resolve_both_runtimes_without_creating_them(self):
        session, network = self.root / "session", self.root / "shared-git"
        with patch.dict(os.environ, {"GITLAB_WORKSPACE_RUNTIME_DIR": str(network)}), \
             patch.object(git_support, "run_git", side_effect=AssertionError("Git in capabilities")):
            result = flow.capabilities(str(self.root), runtime_root=str(session))
        self.assertEqual(str(session), result["runtime_paths"]["session"]["path"])
        self.assertEqual("--runtime-root", result["runtime_paths"]["session"]["setting_source"])
        self.assertEqual(str(network), result["runtime_paths"]["git_network"]["path"])
        self.assertEqual("GITLAB_WORKSPACE_RUNTIME_DIR", result["runtime_paths"]["git_network"]["setting_source"])
        self.assertFalse(session.exists())
        self.assertFalse(network.exists())

    def test_network_preflight_failure_reports_exact_path_before_transport(self):
        network = self.root / "shared-git"
        git(self.repo, "remote", "add", "origin", str(self.root / "uncontacted.git"))
        original = flow.preflight
        def denied(path):
            if Path(path) == network:
                raise PermissionError("synthetic network runtime ACL")
            return original(path)
        with patch.dict(os.environ, {"GITLAB_WORKSPACE_RUNTIME_DIR": str(network)}), \
             patch.object(flow, "preflight", side_effect=denied), \
             patch.object(context_helper, "build_manifest", side_effect=AssertionError("capture before preflight")):
            code, result = self.cli("start", "--workspace", str(self.root), "--project", str(self.repo),
                                    "--base", "main", "--head", "origin/feature")
        self.assertEqual(2, code)
        self.assertEqual("git-network-runtime", result["preflight"]["kind"])
        self.assertEqual(str(network), result["preflight"]["path"])
        self.assertFalse(result["preflight"]["fetch_started"])
        self.assertEqual(0, result["preflight"]["fetch_attempt_count"])

    def test_publication_acl_failure_retains_same_request_and_publishes_once(self):
        started = self.start()
        root, identifier = Path(started["runtime_root"]), started["request_id"]
        context, manifest, _draft = self.authored(started)
        report_root = self.repo / "review-reports"
        human = report_root / "prior-report.md"
        human.write_text("human checklist survives", encoding="utf-8")
        original = Path.write_text
        def denied(path, *args, **kwargs):
            if path.parent == report_root and path.name.startswith(".release-report-"):
                raise PermissionError("synthetic report ACL")
            return original(path, *args, **kwargs)
        with patch.object(Path, "write_text", denied):
            failed, code = flow.finish(root, identifier)
        self.assertEqual(3, code)
        self.assertEqual("publication.io", failed["errors"][0]["code"])
        self.assertTrue(failed["recoverable"])
        self.assertTrue(context.is_dir())
        self.assertEqual(started["head_sha"], flow.status(root, identifier)["resume"]["head_sha"])
        with patch.object(git_support, "fetch_inputs", side_effect=AssertionError("fetch during retry")):
            published, code = flow.finish(root, identifier)
        self.assertEqual(0, code)
        self.assertFalse(context.exists())
        self.assertEqual(published["report_path"], flow.finish(root, identifier)[0]["report_path"])
        self.assertEqual(1, len(list(report_root.glob("release-review-*.md"))))
        self.assertEqual("human checklist survives", human.read_text(encoding="utf-8"))

    def test_source_permission_error_is_terminal_not_publication_recovery(self):
        started = self.start()
        root, identifier = Path(started["runtime_root"]), started["request_id"]
        context, _manifest, _draft = self.authored(started)
        with patch.object(release_report, "validate_result", side_effect=PermissionError("snapshot denied")):
            with self.assertRaises(PermissionError):
                flow.finish(root, identifier)
        self.assertFalse(context.exists())
        self.assertEqual("failed", flow.status(root, identifier)["state"])

    def test_atomic_commit_sharing_failure_can_be_cancelled(self):
        started = self.start()
        root, identifier = Path(started["runtime_root"]), started["request_id"]
        context, _manifest, _draft = self.authored(started)
        with patch.object(release_report.os, "link", side_effect=PermissionError("link sharing denied")), \
             patch.object(release_report.os, "rename", side_effect=PermissionError("rename sharing denied")):
            result, code = flow.finish(root, identifier)
        self.assertEqual(3, code)
        self.assertIn(result["errors"][0]["stage"], ("link-report", "rename-report"))
        self.assertTrue(context.exists())
        self.assertEqual("cancelled", flow.cancel(root, identifier)["state"])
        self.assertFalse(context.exists())
        self.assertFalse(list((self.repo / "review-reports").glob("release-review-*.md")))

    def test_cancel_requested_during_failed_write_still_cleans_context(self):
        started = self.start()
        root, identifier = Path(started["runtime_root"]), started["request_id"]
        context, _manifest, _draft = self.authored(started)
        original = Path.write_text
        def denied(path, *args, **kwargs):
            if path.name.startswith(".release-report-"):
                coordination.atomic(root / "jobs" / (identifier + ".cancel"), {})
                raise PermissionError("write interrupted by cancellation")
            return original(path, *args, **kwargs)
        with patch.object(Path, "write_text", denied):
            with self.assertRaises(coordination.Cancelled):
                flow.finish(root, identifier)
        self.assertFalse(context.exists())
        self.assertEqual("cancelled", flow.status(root, identifier)["state"])

    def test_baseline_policy_cannot_be_removed_from_an_owned_request(self):
        started = self.start()
        root, identifier = Path(started["runtime_root"]), started["request_id"]
        context, manifest, _draft = self.authored(started)
        manifest.pop("risk_baseline_policy")
        (context / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Risk baseline policy"):
            flow.check(root, identifier)
        with self.assertRaisesRegex(ValueError, "Risk baseline policy"):
            flow.finish(root, identifier)
        self.assertFalse(context.exists())

    def test_completed_default_summary_repairs_same_frozen_context(self):
        started = self.start()
        root, identifier = Path(started["runtime_root"]), started["request_id"]
        context, _manifest, draft = self.authored(started)
        draft["summary"] = review_worklist.DEFAULT_SUMMARY
        save_draft(context, draft)
        result, code = flow.finish(root, identifier)
        self.assertEqual(3, code)
        self.assertTrue(context.exists())
        self.assertEqual("summary.placeholder", result["errors"][0]["code"])
        self.assertEqual("/summary", result["errors"][0]["pointer"])
        draft["summary"] = "Fixed versions reviewed; no new release blockers."
        save_draft(context, draft)
        self.assertEqual(0, flow.finish(root, identifier)[1])

    def test_unfinished_scaffold_can_still_publish_an_incomplete_report(self):
        started = self.start()
        published, code = flow.finish(Path(started["runtime_root"]), started["request_id"])
        self.assertEqual(0, code)
        self.assertFalse(published["review_complete"])

    def test_summary_preview_redacts_secrets_without_git(self):
        started = self.start()
        context, _manifest, draft = self.authored(started)
        draft["summary"] = "password=SYNTHETIC-CREDENTIAL token=SYNTHETIC-TOKEN"
        save_draft(context, draft)
        with patch.object(git_support, "run_git", side_effect=AssertionError("Git in check")), \
             patch.object(evidence, "command", side_effect=AssertionError("Git in check")):
            result = flow.check(Path(started["runtime_root"]), started["request_id"])
        self.assertNotIn("SYNTHETIC", result["summary_preview"])
        self.assertFalse(result["evidence_verified"])
        flow.cancel(Path(started["runtime_root"]), started["request_id"])

    def test_saved_window_line_count_rejects_out_of_range_citation_without_git(self):
        started = self.start()
        root, identifier = Path(started["runtime_root"]), started["request_id"]
        context, _manifest, draft = self.authored(started)
        citation = draft["coverage"][0]["evidence"][0]
        read = flow.step(root, identifier, [{"id": "known-lines", "op": "read-window",
            **{key: citation[key] for key in ("repo", "source", "ref", "path")},
            "line_start": 1, "line_count": 1}])
        total = read["operations"][0]["result"]["total_lines"]
        citation.update(line_start=1, line_end=total + 1)
        save_draft(context, draft)
        before = (context / "manifest.json").read_bytes()
        with patch.object(git_support, "run_git", side_effect=AssertionError("Git in check")), \
             patch.object(evidence, "command", side_effect=AssertionError("Git in check")):
            result = flow.check(root, identifier)
        self.assertFalse(result["precheck_passed"])
        self.assertTrue(any(row["pointer"] == "/coverage/0/evidence/0/line_end" for row in result["errors"]))
        self.assertEqual(before, (context / "manifest.json").read_bytes())
        flow.cancel(root, identifier)

    def test_batch_views_are_bounded_read_only_and_resume_every_request(self):
        started = self.start()
        root, identifier, context = Path(started["runtime_root"]), started["request_id"], Path(started["context_dir"])
        artifact = review_runtime.store_view(context, ["中文字" * 200] * 100)
        requests = self.root / "views.json"
        requests.write_text(json.dumps([{"id": f"page-{n}", "op": "view", "artifact": artifact}
                                       for n in range(4)]), encoding="utf-8")
        manifest = (context / "manifest.json").read_bytes()
        paths = sorted(str(p) for p in context.rglob("*"))
        offset, ids = 0, []
        with patch.object(evidence, "command", side_effect=AssertionError("Git in view")), \
             patch.object(review_runtime, "store_view", side_effect=AssertionError("write in view")):
            while True:
                code, result = self.cli("view", "--runtime-root", str(root), "--request-id", identifier,
                                        "--requests-file", str(requests), "--offset", str(offset))
                self.assertEqual(0, code)
                self.assertLessEqual(len(review_runtime.canonical(result)) + 1, flow.LIMIT)
                ids.extend(row["id"] for row in result["results"])
                for row in result["results"]:
                    self.assertEqual(100, row["result"]["total"])
                    self.assertIn("continue", row["result"])
                if result["complete"]:
                    break
                self.assertGreater(result["next_index"], offset)
                offset = result["next_index"]
        self.assertEqual([f"page-{n}" for n in range(4)], ids)
        self.assertEqual(manifest, (context / "manifest.json").read_bytes())
        self.assertEqual(paths, sorted(str(p) for p in context.rglob("*")))
        flow.cancel(root, identifier)

    def test_step_view_cannot_inject_private_preloaded_content(self):
        started = self.start()
        root, identifier, context = Path(started["runtime_root"]), started["request_id"], Path(started["context_dir"])
        artifact = review_runtime.store_view(context, {"lines": ["checksum-verified"]})
        with patch.object(evidence, "command", side_effect=AssertionError("Git in saved view")):
            rejected = flow.step(root, identifier, [{"id": "saved-view", "op": "view", "artifact": artifact,
                                                    "_value": {"lines": ["injected"]}}])
            self.assertFalse(rejected["complete"])
            self.assertIn("_value", rejected["error"])
            repaired = flow.step(root, identifier, [{"id": "saved-view", "op": "view", "artifact": artifact}])
        self.assertTrue(repaired["complete"])
        self.assertIn("checksum-verified", json.dumps(repaired))
        self.assertNotIn("injected", json.dumps(repaired))
        flow.cancel(root, identifier)


if __name__ == "__main__":
    unittest.main()
