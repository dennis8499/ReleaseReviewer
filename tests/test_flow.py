"""Request recovery, bounded rounds and publication integrity at the facade."""
import copy
from unittest.mock import patch

from support import *
import frozen_evidence
import git_support
import review_coordinator as coordination
import review_flow as flow
import review_runtime


class SessionFlowTests(RepoTest):
    def start(self, **options):
        values = ["start", "--workspace", str(self.root), "--project", str(self.repo),
                  "--base", "main", "--head", "feature"]
        for key, value in options.items():
            values += ["--" + key.replace("_", "-"), str(value)]
        return flow.start(flow.parser().parse_args(values))

    def authored(self, started, *, checklist=False):
        context = Path(started["context_dir"])
        manifest = evidence.load_manifest(context, full=False)
        draft = draft_for(manifest)
        if checklist:
            draft["config_changes"] = [config_item(context, manifest)]
        save_draft(context, draft)
        return context, manifest, draft

    def test_start_ownership_and_preflight_before_transport(self):
        started = self.start()
        root, identifier = Path(started["runtime_root"]), started["request_id"]
        self.assertEqual("ReleaseReviewRuntime/v1", coordination.read_json(root / ".release-reviewer-runtime.json")["schema"])
        self.assertEqual("release-reviewer-session/v2", coordination.read_json(Path(started["context_dir"]) / review_session.OWNER_FILE)["schema"])
        self.assertEqual("ready", flow.status(root, identifier)["state"])
        self.assertEqual(started["head_sha"], flow.status(root, identifier)["resume"]["head_sha"])
        with patch.object(flow, "preflight", side_effect=PermissionError("denied")), \
             patch.object(context_helper, "build_manifest", side_effect=AssertionError("capture before preflight")):
            with self.assertRaises(PermissionError):
                self.start()

    def test_step_preserves_prefix_and_allows_failed_operation_repair(self):
        started = self.start()
        root, identifier, context = Path(started["runtime_root"]), started["request_id"], Path(started["context_dir"])
        operations = [{"id": "read", "op": "read", "path": "src/service.py"},
                      {"id": "missing", "op": "read", "path": "absent.py"}]
        first = flow.step(root, identifier, operations)
        self.assertFalse(first["complete"])
        self.assertEqual(1, first["next_index"])
        generation = evidence.load_manifest(context, full=False)["generation"]
        operations[1]["path"] = "src/existing.py"
        second = flow.step(root, identifier, operations)
        self.assertTrue(second["complete"])
        self.assertTrue(second["operations"][0]["reused"])
        self.assertEqual(generation + 1, evidence.load_manifest(context, full=False)["generation"])
        operations[0]["path"] = "src/existing.py"
        self.assertIn("different input", flow.step(root, identifier, operations)["error"])

    def test_check_status_and_saved_view_are_zero_git(self):
        started = self.start()
        root, identifier, context = Path(started["runtime_root"]), started["request_id"], Path(started["context_dir"])
        _, manifest, draft = self.authored(started)
        draft["coverage"].pop()
        draft["dependency_coverage"] = []
        save_draft(context, draft)
        artifact = review_runtime.store_view(context, {"matches": ["saved"]})
        with patch.object(git_support, "run_git", side_effect=AssertionError("Git in metadata check")), \
             patch.object(evidence, "command", side_effect=AssertionError("Git in check")):
            result = flow.check(root, identifier)
            self.assertFalse(result["evidence_verified"])
            self.assertGreater(len(result["errors"]), 7)
            self.assertTrue(flow.status(root, identifier)["resume"]["remaining"]["changes"])
            self.assertEqual(0, evidence.main(["view", "--context-dir", str(context), "--artifact", artifact, "--page-size", "10"]))

    def test_round_validates_only_used_repositories_once(self):
        peer = init_repo(self.root, "consumer")
        write(peer, "peer.py", "unused = True\n")
        git(peer, "add", ".")
        git(peer, "commit", "-m", "peer")
        started = self.start()
        with patch.object(frozen_evidence, "validate_for_step", wraps=frozen_evidence.validate_for_step) as validate:
            result = flow.step(Path(started["runtime_root"]), started["request_id"], [
                {"id": "one", "op": "read", "path": "src/service.py"},
                {"id": "two", "op": "read", "path": "src/existing.py"}])
        self.assertTrue(result["complete"])
        self.assertEqual(["entry"], [call.args[1]["id"] for call in validate.call_args_list])

    def test_search_reuses_across_rounds_but_finish_replays_all_records(self):
        started = self.start()
        root, identifier = Path(started["runtime_root"]), started["request_id"]
        first = flow.step(root, identifier, [{"id": "q1", "op": "search", "source": "base", "query": "EXISTING_TIMEOUT"}])
        self.assertEqual(1, first["search_activity"]["executed"])
        second = flow.step(root, identifier, [{"id": "q2", "op": "search", "source": "base", "query": "EXISTING_TIMEOUT"}])
        self.assertEqual(0, second["search_activity"]["executed"])
        self.assertEqual(1, second["search_activity"]["cache_hits"])
        self.authored(started)
        with patch.object(evidence, "command", wraps=evidence.command) as calls, \
             patch.object(frozen_evidence, "validate", wraps=frozen_evidence.validate) as snapshots:
            report, code = flow.finish(root, identifier)
        self.assertEqual(0, code)
        self.assertEqual(1, sum(call.args[1][0] == "grep" for call in calls.call_args_list))
        self.assertEqual(1, snapshots.call_count)
        self.assertTrue(Path(report["report_path"]).is_file())

    def test_corrupt_optional_search_cache_recomputes_and_damaged_ledger_fails(self):
        started = self.start()
        root, identifier, context = Path(started["runtime_root"]), started["request_id"], Path(started["context_dir"])
        op = {"id": "q1", "op": "search", "source": "base", "query": "EXISTING_TIMEOUT"}
        flow.step(root, identifier, [op])
        cache_file = next((context / "search-cache").glob("*.json"))
        cache_file.write_text("{broken", encoding="utf-8")
        second = flow.step(root, identifier, [{**op, "id": "q2"}])
        self.assertEqual(1, second["search_activity"]["executed"])
        self.authored(started)
        record_file = next((context / "searches").glob("*.json"))
        record = coordination.read_json(record_file)
        record["matches"] = []
        record_file.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "摘要"):
            flow.finish(root, identifier)
        self.assertEqual("failed", flow.status(root, identifier)["state"])
        self.assertFalse(context.exists())

    def test_damaged_source_after_cached_search_is_terminal(self):
        started = self.start()
        root, identifier, context = Path(started["runtime_root"]), started["request_id"], Path(started["context_dir"])
        flow.step(root, identifier, [{"id": "q1", "op": "search", "query": "billing_timeout"}])
        manifest = evidence.load_manifest(context, full=False)
        pack = next((frozen_evidence.repository(context, manifest["repositories"][0]) / "objects/pack").glob("*.pack"))
        pack.chmod(0o600)
        pack.write_bytes(b"corrupt owned pack")
        with self.assertRaises(ValueError):
            flow.step(root, identifier, [{"id": "q2", "op": "search", "query": "billing_timeout"}])
        self.assertEqual("failed", flow.status(root, identifier)["state"])
        self.assertFalse(context.exists())

    def test_finish_receipt_survives_cleanup_failure_and_checklist_edit(self):
        started = self.start()
        root, identifier = Path(started["runtime_root"]), started["request_id"]
        self.authored(started, checklist=True)
        with patch.object(review_session, "cleanup_session", side_effect=OSError("cleanup interrupted")):
            pending, code = flow.finish(root, identifier)
        self.assertEqual(0, code)
        self.assertTrue(pending["cleanup_pending"])
        job = flow.status(root, identifier)
        self.assertEqual("published", job["state"])
        report = Path(job["report_path"])
        report.write_text(report.read_text(encoding="utf-8").replace("[ ]", "[x]"), encoding="utf-8")
        with patch.object(release_report, "validate_result", side_effect=AssertionError("revalidation on retry")):
            again, code = flow.finish(root, identifier)
        self.assertEqual(0, code)
        self.assertEqual(str(report), again["report_path"])
        self.assertFalse(again["cleanup_pending"])
        self.assertEqual(1, len(list(report.parent.glob("release-review-*.md"))))
        report.write_text(report.read_text(encoding="utf-8").replace("## 比較資訊", "## altered"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "static content"):
            flow.finish(root, identifier)

    def test_real_partial_cleanup_keeps_owner_and_receipt_for_retry(self):
        started = self.start()
        root, identifier, context = Path(started["runtime_root"]), started["request_id"], Path(started["context_dir"])
        self.authored(started)
        blocked = context / "blocked-pack"
        blocked.mkdir()
        (blocked / "snapshot.idx").write_bytes(b"synthetic blocked child")
        marker = context / review_session.OWNER_FILE
        original_marker = marker.read_bytes()
        real_rmtree = review_session.shutil.rmtree
        def partial(path, *args, **kwargs):
            if Path(path) == blocked:
                (context / "review-worklist.json").unlink(missing_ok=True)
                raise PermissionError("real partial cleanup denied")
            return real_rmtree(path, *args, **kwargs)
        with patch.object(review_session.shutil, "rmtree", side_effect=partial):
            published, code = flow.finish(root, identifier)
        self.assertEqual(0, code)
        self.assertTrue(published["cleanup_pending"])
        self.assertEqual(original_marker, marker.read_bytes())
        self.assertEqual(context, review_session.validate_session(context))
        self.assertFalse((context / "review-worklist.json").exists())
        report = Path(published["report_path"])
        again, code = flow.finish(root, identifier)
        self.assertEqual(0, code)
        self.assertEqual(str(report), again["report_path"])
        self.assertFalse(context.exists())
        self.assertEqual(1, len(list(report.parent.glob("release-review-*.md"))))

    def test_cleanup_restores_owner_if_final_root_removal_fails(self):
        started = self.start()
        root, identifier, context = Path(started["runtime_root"]), started["request_id"], Path(started["context_dir"])
        self.authored(started)
        marker = context / review_session.OWNER_FILE
        original = marker.read_bytes()
        real_rmdir = Path.rmdir
        def blocked(path):
            if path == context:
                raise PermissionError("final root removal denied")
            return real_rmdir(path)
        with patch.object(Path, "rmdir", blocked):
            published, code = flow.finish(root, identifier)
        self.assertEqual(0, code)
        self.assertTrue(published["cleanup_pending"])
        self.assertEqual(original, marker.read_bytes())
        self.assertEqual(context, review_session.validate_session(context))
        self.assertEqual(0, flow.finish(root, identifier)[1])
        self.assertFalse(context.exists())

    def test_published_file_recovers_after_receipt_callback_interruption(self):
        started = self.start()
        root, identifier = Path(started["runtime_root"]), started["request_id"]
        self.authored(started)
        original = flow.update
        def lost(*args, **kwargs):
            if kwargs.get("state") == "published":
                raise OSError("lost published receipt")
            return original(*args, **kwargs)
        with patch.object(flow, "update", side_effect=lost):
            with self.assertRaisesRegex(OSError, "lost published"):
                flow.finish(root, identifier)
        # The staged receipt still names the atomically linked report.
        recovered, code = flow.finish(root, identifier)
        self.assertEqual(0, code)
        self.assertEqual("published", recovered["state"])

    def test_status_and_finish_reject_missing_foreign_or_changed_reports(self):
        started = self.start()
        root, identifier = Path(started["runtime_root"]), started["request_id"]
        self.authored(started)
        published, _ = flow.finish(root, identifier)
        report = Path(published["report_path"])
        job_file = flow.job_path(root, identifier)
        original = coordination.read_json(job_file)
        foreign = self.root / "foreign.md"
        foreign.write_bytes(report.read_bytes())
        coordination.atomic(job_file, {**original, "report_path": str(foreign)})
        with self.assertRaisesRegex(ValueError, "reviewed Repo"):
            flow.status(root, identifier)
        with self.assertRaisesRegex(ValueError, "reviewed Repo"):
            flow.finish(root, identifier)
        coordination.atomic(job_file, original)
        body = report.read_bytes()
        report.unlink()
        with self.assertRaisesRegex(ValueError, "must exist"):
            flow.status(root, identifier)
        report.write_bytes(body)
        report.write_text(report.read_text(encoding="utf-8") + "changed static body", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "static content"):
            flow.status(root, identifier)

    def test_cancel_only_its_request_and_legacy_default_remains_full(self):
        first, second = self.start(), self.start()
        root = Path(first["runtime_root"])
        flow.cancel(root, first["request_id"])
        self.assertFalse(Path(first["context_dir"]).exists())
        self.assertTrue(Path(second["context_dir"]).exists())
        with patch.object(frozen_evidence, "validate", wraps=frozen_evidence.validate) as validate:
            evidence.load_manifest(Path(second["context_dir"]))
        self.assertEqual(1, validate.call_count)

    def test_check_reports_references_search_identity_chain_and_risk_order_without_git(self):
        started = self.start()
        context, manifest, draft = self.authored(started)
        draft["code_risks"] = [risk_item(manifest), {**risk_item(manifest), "id": "R-002", "priority": "P0"}]
        item = config_item(context, manifest)
        wrong = evidence.record_search(context, evidence.search(manifest, context, "head", "INVOICE_API_URL"))
        item["baseline_searches"] = [wrong, "bad"]
        draft["config_changes"] = [item]
        draft["coverage"][0]["evidence"][0]["ref"] = "a" * 40
        draft["functional_chain"] = [{"id": "F-999", "from_repo": [], "to_repo": "entry", "kind": "invented",
                                       "status": "reviewed", "description": "test", "evidence": [], "diff_roots": [[]]}]
        with patch.object(git_support, "run_git", side_effect=AssertionError("Git in diagnostics")), \
             patch.object(evidence, "command", side_effect=AssertionError("Git in diagnostics")):
            result = flow.diagnostics(draft, manifest)
        codes = {item["code"] for item in result["errors"]}
        self.assertTrue({"search_record", "search_id", "evidence_identity", "chain_endpoint", "chain_kind", "diff_roots", "priority_order"} <= codes)

    def test_check_treats_json_type_errors_as_repairable_diagnostics(self):
        started = self.start()
        _, manifest, draft = self.authored(started)
        mutations = [
            ("coverage", [{"path": [], "status": {}, "evidence": [{"repo": {}, "source": [], "ref": {}, "path": []}]}]),
            ("dependency_coverage", [{"kind": [], "status": {}, "reason": []}]),
            ("supporting_coverage", [{"repo": {}, "status": []}]),
            ("code_risks", [{"id": [], "priority": {}, "technical_details": []}]),
            ("config_changes", [{"id": {}, "diff_roots": [[]], "baseline_searches": [{}]}]),
            ("functional_chain", [{"id": [], "from_repo": {}, "to_repo": [], "kind": {}, "diff_roots": [[]]}]),
            ("merge_parent_review", [{"id": []}])]
        for field, value in mutations:
            with self.subTest(field=field):
                changed = copy.deepcopy(draft)
                changed[field] = value
                result = flow.diagnostics(changed, manifest)
                self.assertFalse(result["precheck_passed"])
                self.assertTrue(all(isinstance(row["pointer"], str) and row["code"] for row in result["errors"]))

    def test_search_identity_binds_effective_git_environment_and_flags_without_persisting_secrets(self):
        started = self.start()
        context, manifest, _ = self.authored(started)
        secret = "synthetic-cache-secret-not-for-persistence"
        with patch.object(evidence, "command", wraps=evidence.command) as calls:
            with evidence.operation_cache(manifest, context, persistent=True):
                with patch.dict(os.environ, {"GIT_TEST_CREDENTIAL": secret}):
                    evidence.search(manifest, context, "base", "EXISTING_TIMEOUT")
                with patch.dict(os.environ, {"GIT_TEST_CREDENTIAL": secret + "-changed"}):
                    evidence.search(manifest, context, "base", "EXISTING_TIMEOUT")
                    with patch.object(evidence, "SEARCH_FLAGS", (*evidence.SEARCH_FLAGS, "--no-column")):
                        evidence.search(manifest, context, "base", "EXISTING_TIMEOUT")
            self.assertEqual(3, calls.call_count)
            with patch.dict(os.environ, {"GIT_TEST_CREDENTIAL": secret}), evidence.operation_cache(manifest, context, persistent=True):
                evidence.search(manifest, context, "base", "EXISTING_TIMEOUT")
            self.assertEqual(3, calls.call_count)
        for directory in (context / "search-cache", context / "views"):
            for path in directory.glob("*.json"):
                self.assertNotIn(secret, path.read_text(encoding="utf-8"))

    def test_optional_cache_write_failures_preserve_fresh_result_and_search_ledger(self):
        started = self.start()
        root, identifier, context = Path(started["runtime_root"]), started["request_id"], Path(started["context_dir"])
        original = coordination.atomic
        def denied(path, value):
            if Path(path).parent.name == "search-cache":
                raise PermissionError("optional cache denied")
            return original(path, value)
        with patch.object(coordination, "atomic", side_effect=denied):
            result = flow.step(root, identifier, [{"id": "denied-cache", "op": "search", "source": "base", "query": "EXISTING_TIMEOUT"}])
        self.assertTrue(result["complete"])
        self.assertEqual(1, len(list((context / "searches").glob("*.json"))))
        self.authored(started)
        self.assertEqual(0, flow.finish(root, identifier)[1])

    def test_valid_hash_malformed_search_cache_is_recomputed_without_changing_existing_ledger(self):
        started = self.start()
        root, identifier, context = Path(started["runtime_root"]), started["request_id"], Path(started["context_dir"])
        op = {"op": "search", "source": "base", "query": "EXISTING_TIMEOUT"}
        flow.step(root, identifier, [{"id": "seed", **op}])
        ledger = {path.name: path.read_bytes() for path in (context / "searches").glob("*.json")}
        cache_file = next((context / "search-cache").glob("*.json"))
        for i, change in enumerate(({"query": "wrong"}, {"matches": ["bad"]})):
            envelope = coordination.read_json(cache_file)
            payload = review_runtime.load_view(context, envelope["artifact"])
            payload["result"].update(change)
            coordination.atomic(cache_file, {"artifact": review_runtime.store_view(context, payload)})
            result = flow.step(root, identifier, [{"id": f"retry-{i}", **op}])
            self.assertTrue(result["complete"])
            self.assertEqual(1, result["search_activity"]["executed"])
            self.assertEqual(ledger, {path.name: path.read_bytes() for path in (context / "searches").glob("*.json")})

    def test_v2_renders_five_sections_for_no_risk_and_blocker_with_dependencies(self):
        expected = ["審查結論", "問題總攬", "問題詳情（工程師參考）", "範圍與限制", "技術審查紀錄（工程師參考）"]
        for with_blocker in (False, True):
            with self.subTest(blocker=with_blocker):
                started = self.start()
                context, manifest, draft = self.authored(started, checklist=with_blocker)
                draft["report_layout"] = "engineering-review/v2"
                if with_blocker:
                    draft["code_risks"] = [risk_item(manifest)]
                report = release_report.publish_report(context, save_draft(context, draft))
                body = report.read_text(encoding="utf-8")
                import re
                self.assertEqual(expected, re.findall(r"^## (.+)$", body, re.M))
                self.assertTrue(body.startswith("# 上版檢查: repo\n"))
                conclusion = body.split("## 問題總攬", 1)[0]
                for value in (manifest["base_input"], manifest["head_input"], manifest["base_sha"][:8], manifest["head_sha"][:8]):
                    self.assertIn(value, conclusion)
                self.assertLess(body.index(release_report.CHECKLIST_START), body.index("<details>"))
                if with_blocker:
                    self.assertIn("[R-001](#R-001)", body)
                    self.assertIn("INVOICE_API_URL", body)
                    for label in ("**問題描述**", "**重現原因**", "**預期結果**", "**實際結果**", "**證據**", "**建議改善方式**"):
                        self.assertIn(label, body)


if __name__ == "__main__":
    unittest.main()
