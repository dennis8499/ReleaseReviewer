"""Ancestor no-op coverage never approves environments or bypasses frozen proof."""
import copy
from unittest.mock import patch

from support import *
import coverage_policy
import review_runtime
import review_worklist


class AncestorNoopTests(RepoTest):
    def setUp(self):
        super().setUp()
        isolated = patch.dict(os.environ, {"GITLAB_WORKSPACE_RUNTIME_DIR": str(self.root / "runtime")})
        isolated.start()
        self.addCleanup(isolated.stop)

    def ancestor_with_base_changes(self):
        for path in ("gone.py", "old.py", "mode.py"):
            write(self.repo, path, f"{path.split('.')[0]} = True\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "common files before base advances")
        git(self.repo, "branch", "-f", "main", "feature")
        git(self.repo, "switch", "main")
        write(self.repo, "intermediate-only.py", "historical_tree_only = True\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "intermediate commit proof without its historical tree")
        git(self.repo, "rm", "intermediate-only.py")
        write(self.repo, "new.py", "new = True\n")
        git(self.repo, "rm", "gone.py")
        git(self.repo, "mv", "old.py", "renamed.py")
        mode_file = self.repo / "mode.py"
        mode_file.chmod(mode_file.stat().st_mode | 0o111)
        git(self.repo, "add", "-A")
        git(self.repo, "update-index", "--chmod=+x", "mode.py")
        git(self.repo, "commit", "-m", "advance base with file changes")
        git(self.repo, "switch", "feature")

    def peer(self):
        peer = init_repo(self.root, "consumer")
        write(peer, "consumer.py", "EXISTING_CONTRACT = True\n")
        git(peer, "add", ".")
        git(peer, "commit", "-m", "peer baseline")
        git(peer, "switch", "-c", "local-feature")
        write(peer, "consumer.py", "CURRENT_LOCAL_CONTRACT = True\n")
        git(peer, "add", ".")
        git(peer, "commit", "-m", "peer local HEAD")
        return peer

    def noop_capture(self, **kwargs):
        context, manifest = self.capture(**kwargs)
        self.assertEqual(coverage_policy.COVERAGE_POLICY, manifest["coverage_policy"])
        return context, manifest

    def confirmed_draft(self, manifest):
        _, draft = review_worklist.prepare(manifest)
        draft["summary"] = "比較分支已包含於基礎分支，本次合併沒有新增程式碼或部署依賴。"
        draft["next_steps"] = []
        for row in draft["dependency_coverage"] + draft["supporting_coverage"]:
            row.update(status="not-applicable", reason="固定祖先及乾淨合併預覽證明本次沒有新增差異。")
        return draft

    def test_base_only_changes_keep_raws_and_snapshots_but_do_not_seed_review_work(self):
        self.ancestor_with_base_changes()
        peer = self.peer()
        with patch.object(context_helper.git, "run_git", wraps=context_helper.git.run_git) as calls:
            context, manifest = self.noop_capture(workspace=self.root)
            raw = copy.deepcopy(manifest["merge_preview"])
            self.assertTrue(coverage_policy.validate(manifest, context))
            worklist, pending = review_worklist.prepare(manifest)
            self.assertEqual([], worklist["requests"])
            self.assertEqual([], worklist["required_coverage"])
            self.assertEqual([], manifest["diff_roots"])
            self.assertEqual([], manifest["changed_files"])
            self.assertEqual([], manifest["release_changes"])
            self.assertEqual([], manifest["required_merge_views"])
            self.assertTrue(all(row["status"] == "not-reviewed" for row in pending["dependency_coverage"] + pending["supporting_coverage"]))
            self.assertFalse(release_report.validate_result(pending, manifest, context)["review_complete"])
            result = release_report.validate_result(self.confirmed_draft(manifest), manifest, context)
        self.assertEqual(raw, manifest["merge_preview"])
        paths = {path for row in raw["changed_files"] for path in (row["path"], row.get("old_path"))}
        self.assertTrue({"new.py", "gone.py", "old.py", "renamed.py", "mode.py"} <= paths)
        self.assertTrue(all(record.get("snapshot") for record in manifest["repositories"]))
        self.assertEqual(git(peer, "rev-parse", "HEAD"), manifest["repositories"][1]["ref"])
        self.assertGreaterEqual(len(manifest["ancestor_path"]), 3)
        historical = manifest["ancestor_path"][1]
        historical_tree = git(self.repo, "rev-parse", historical + "^{tree}")
        entry = manifest["repositories"][0]
        self.assertNotIn(historical, entry["snapshot"]["sources"])
        owned = coverage_policy.frozen_evidence.repository(context, entry)
        env = coverage_policy.frozen_evidence.environment(context, entry)
        code, _, _ = context_helper.git.run_git_capture(owned, ["cat-file", "-e", historical_tree], env=env)
        self.assertNotEqual(0, code, "proof commits must not expand all historical trees")
        self.assertEqual("沒有差異", result["status"])
        self.assertTrue(result["review_complete"])
        self.assertEqual([], result["checklist"])
        self.assertEqual([], result["review_gaps"])
        self.assertEqual("未驗證實際環境", result["environment_status"])
        rendered = release_report.render_markdown(result)
        self.assertIn("本次不適用", rendered)
        self.assertIn("release-checklist:start", rendered)
        self.assertNotIn("not-applicable", rendered)
        self.assertFalse(any(call.args[1][0] in {"fetch", "ls-remote"} for call in calls.call_args_list))

    def test_same_sha_is_eligible_but_requires_authored_dependency_decisions(self):
        context, manifest = self.noop_capture(head="main")
        self.assertTrue(coverage_policy.validate(manifest, context))
        marker = context / review_session.OWNER_FILE
        ownership = marker.read_bytes()
        marker.unlink()
        try:
            with self.assertRaises(review_session.SessionError):
                release_report.validate_result(self.confirmed_draft(manifest), manifest, context)
        finally:
            marker.write_bytes(ownership)
        self.assertTrue(release_report.validate_result(self.confirmed_draft(manifest), manifest, context)["review_complete"])

    def test_base_contains_head_through_a_merge_second_parent(self):
        git(self.repo, "switch", "main")
        write(self.repo, "base-only.py", "base_change = True\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "base diverges before including head")
        git(self.repo, "merge", "--no-ff", "feature", "-m", "include feature as second parent")
        git(self.repo, "switch", "feature")
        context, manifest = self.noop_capture()
        self.assertEqual([manifest["base_sha"], manifest["head_sha"]], manifest["ancestor_path"])
        self.assertTrue(release_report.validate_result(self.confirmed_draft(manifest), manifest, context)["review_complete"])

    def test_nonancestor_equal_tree_keeps_normal_scopes_and_rejects_not_applicable(self):
        write(self.repo, "src/service.py", "same = True\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "feature final content")
        git(self.repo, "switch", "main")
        for path, value in HEAD_FILES.items():
            write(self.repo, path, value)
        write(self.repo, "src/service.py", "same = True\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "same tree with separate history")
        git(self.repo, "switch", "feature")
        context, manifest = self.noop_capture()
        self.assertFalse(coverage_policy.validate(manifest, context))
        self.assertEqual([], manifest["release_changes"])
        self.assertTrue(manifest["diff_roots"])
        draft = draft_for(manifest)
        draft["dependency_coverage"][0].update(status="not-applicable", evidence=[])
        with self.assertRaises(release_report.DraftValidationError):
            release_report.validate_result(draft, manifest, context)
        forged = copy.deepcopy(manifest)
        forged.update(merge_base=forged["head_sha"], diff_base=forged["head_sha"], changed_files=[],
                      merge_commits=[], diff_roots=[], required_merge_views=[],
                      ancestor_path=[forged["base_sha"], forged["head_sha"]])
        self.assertTrue(coverage_policy.ancestor_noop(forged))
        with self.assertRaisesRegex(ValueError, "parent link"):
            release_report.validate_result(self.confirmed_draft(forged), forged, context)

    def test_context_gap_worktree_change_and_unavailable_peer_never_grant_exemption(self):
        self.ancestor_with_base_changes()
        self.peer()
        context, manifest = self.noop_capture(workspace=self.root)
        for kind in ("gap", "worktree", "peer"):
            with self.subTest(kind=kind):
                changed = copy.deepcopy(manifest)
                changed["context_gaps"] = ["required captured evidence unavailable"]
                changed["context_complete"] = False
                if kind == "worktree":
                    changed["working_tree_unchanged"] = False
                elif kind == "peer":
                    changed["repositories"][1].update(status="unavailable", reason="required peer unavailable", ref=None)
                self.assertFalse(coverage_policy.ancestor_noop(changed))
                self.assertTrue(review_worklist.expected_coverage(changed))
                with self.assertRaises(release_report.DraftValidationError):
                    release_report.validate_result(self.confirmed_draft(manifest), changed, context)

    def test_real_conflict_and_unavailable_preview_remain_incomplete(self):
        git(self.repo, "switch", "main")
        write(self.repo, "src/service.py", "base_conflict = True\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "conflicting base")
        git(self.repo, "switch", "feature")
        context, manifest = self.noop_capture()
        self.assertFalse(coverage_policy.ancestor_noop(manifest))
        self.assertFalse(release_report.validate_result(draft_for(manifest), manifest, context)["review_complete"])
        unavailable = {"status": "unavailable", "exit_code": 128, "detail": "fixture cannot create preview",
                       "tree_sha": None, "changed_files": []}
        with patch.object(context_helper.git, "create_merge_preview", return_value=(unavailable, {})):
            context, manifest = self.noop_capture(head="main")
        self.assertFalse(coverage_policy.ancestor_noop(manifest))
        self.assertFalse(release_report.validate_result(draft_for(manifest), manifest, context)["review_complete"])

    def test_policy_and_proof_tampering_are_fatal_even_for_direct_validator(self):
        self.ancestor_with_base_changes()
        context, manifest = self.noop_capture()
        draft = self.confirmed_draft(manifest)
        for kind in ("unknown", "null", "baseline", "exit", "preview", "changes", "release", "roots", "missing-source"):
            with self.subTest(kind=kind):
                changed = copy.deepcopy(manifest)
                if kind == "unknown":
                    changed["coverage_policy"] = "ancestor-noop/future"
                elif kind == "null":
                    changed["coverage_policy"] = None
                elif kind == "baseline":
                    changed["diff_base"] = changed["base_sha"]
                elif kind == "exit":
                    changed["merge_preview"]["exit_code"] = 1
                elif kind == "preview":
                    changed["merge_preview"]["tree_sha"] = changed["head_sha"]
                elif kind == "changes":
                    changed["changed_files"] = copy.deepcopy(changed["merge_preview"]["changed_files"])
                elif kind == "release":
                    changed["release_changes"] = copy.deepcopy(changed["merge_preview"]["changed_files"])
                elif kind == "roots":
                    changed["diff_roots"] = [{"id": "DIFF-forged"}]
                else:
                    changed["repositories"][0]["snapshot"]["sources"].pop(changed["base_sha"])
                with self.assertRaises(ValueError) as caught:
                    release_report.validate_result(draft, changed, context)
                self.assertNotIsInstance(caught.exception, release_report.DraftValidationError)

    def test_consistently_forged_base_preview_tree_is_checked_against_owned_objects(self):
        self.ancestor_with_base_changes()
        context, manifest = self.noop_capture()
        draft = self.confirmed_draft(manifest)
        sources = manifest["repositories"][0]["snapshot"]["sources"]
        tree = sources[manifest["head_sha"]]["tree"]
        sources[manifest["base_sha"]]["tree"] = tree
        sources[tree] = copy.deepcopy(sources[manifest["head_sha"]])
        manifest["merge_preview"]["tree_sha"] = tree
        self.assertTrue(coverage_policy.ancestor_noop(manifest))
        with self.assertRaisesRegex(ValueError, "owned tree identity"):
            release_report.validate_result(draft, manifest, context)

    def test_owned_parent_path_rejects_missing_links_and_commit_body_checksum_damage(self):
        self.ancestor_with_base_changes()
        context, manifest = self.noop_capture()
        for path in (None, [manifest["base_sha"], manifest["head_sha"]],
                     [manifest["base_sha"], "0" * 40, manifest["head_sha"]]):
            with self.subTest(path=path):
                changed = copy.deepcopy(manifest)
                changed["ancestor_path"] = path
                with self.assertRaises(ValueError):
                    release_report.validate_result(self.confirmed_draft(manifest), changed, context)
        original = context_helper.git.run_git

        def corrupt_commit(repo, args, **kwargs):
            value = original(repo, args, **kwargs)
            if args == ["cat-file", "--batch"]:
                boundary = value.index(b"\n") + 1
                value = value[:boundary] + bytes([value[boundary] ^ 1]) + value[boundary + 1:]
            return value

        with patch.object(context_helper.git, "run_git", side_effect=corrupt_commit):
            with self.assertRaisesRegex(ValueError, "checksum"):
                release_report.validate_result(self.confirmed_draft(manifest), manifest, context)

    def test_noop_rejects_invented_findings_dependencies_and_functional_chain(self):
        context, manifest = self.noop_capture(head="main")
        for field in ("code_risks", "config_changes", "db_changes", "external_changes", "functional_chain"):
            with self.subTest(field=field):
                draft = self.confirmed_draft(manifest)
                draft[field] = [{"id": "invented"}]
                with self.assertRaisesRegex(release_report.DraftValidationError, "祖先無差異"):
                    release_report.validate_result(draft, manifest, context)

    def test_not_applicable_needs_reason_and_empty_evidence_and_searches(self):
        self.peer()
        context, manifest = self.noop_capture(head="main", workspace=self.root)
        for kind in ("dependency-reason", "dependency-evidence", "dependency-search", "supporting-reason", "supporting-search"):
            with self.subTest(kind=kind):
                draft = self.confirmed_draft(manifest)
                row = draft["dependency_coverage"][0] if kind.startswith("dependency") else draft["supporting_coverage"][0]
                if kind.endswith("reason"):
                    row["reason"] = " "
                elif kind.endswith("evidence"):
                    row["evidence"] = [citation(manifest, "base", "src/service.py")]
                else:
                    row["searches"] = ["a" * 64]
                with self.assertRaises(release_report.DraftValidationError):
                    release_report.validate_result(draft, manifest, context)

    def test_legacy_v3_preserves_preview_work_and_rejects_new_status(self):
        self.ancestor_with_base_changes()
        context, manifest = self.noop_capture()
        manifest.pop("coverage_policy")
        manifest["diff_roots"] = review_worklist.diff_roots(manifest)
        self.assertFalse(coverage_policy.validate(manifest, context))
        self.assertTrue(review_worklist.expected_coverage(manifest))
        draft = draft_for(manifest)
        for row in draft["dependency_coverage"]:
            row["evidence"] = [citation(manifest, "merge-preview", "src/service.py")]
        self.assertTrue(release_report.validate_result(draft, manifest, context)["review_complete"])
        draft["dependency_coverage"][0].update(status="not-applicable", evidence=[])
        with self.assertRaises(release_report.DraftValidationError):
            release_report.validate_result(draft, manifest, context)

    def test_repairable_draft_retains_then_publishes_without_fetch_and_bad_proof_is_fatal(self):
        self.ancestor_with_base_changes()
        context, manifest = self.noop_capture()
        path = context / "review-result-draft.json"
        path.write_text('{"schema":', encoding="utf-8")
        with self.assertRaises(release_report.DraftValidationError):
            release_report.publish_report(context, path, retain_on_validation_error=True)
        self.assertTrue(context.is_dir())
        with patch.object(context_helper.git, "run_git", wraps=context_helper.git.run_git) as calls:
            report = release_report.publish_report(context, save_draft(context, self.confirmed_draft(manifest)),
                                                    retain_on_validation_error=True)
        self.assertTrue(report.is_file())
        self.assertFalse(context.exists())
        self.assertFalse(any(call.args[1][0] in {"fetch", "ls-remote"} for call in calls.call_args_list))
        context, manifest = self.noop_capture()
        manifest["coverage_policy"] = "ancestor-noop/future"
        review_runtime.save_manifest(context, manifest)
        path = context / "review-result-draft.json"
        path.write_text('{"schema":', encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            release_report.publish_report(context, path, retain_on_validation_error=True)
        self.assertNotIsInstance(caught.exception, release_report.DraftValidationError)
        self.assertFalse(context.exists())
