import hashlib
import json
from pathlib import Path

from support import *


class ContextTests(RepoTest):
    def test_merge_preview_and_actual_release_scope(self):
        context, manifest = self.capture()
        self.assertEqual(manifest["merge_preview"]["status"], "clean")
        self.assertTrue(manifest["context_complete"])
        self.assertEqual({c["path"] for c in manifest["release_changes"]},
                         {"src/service.py", "src/settings.json"})
        content = evidence.read_blob(manifest, context, "merge-preview", "src/service.py")
        self.assertIn(b"BillingDb.dbo.InvoiceTax", content)
        self.assertTrue((context / "release.patch").is_file())

    def test_dirty_worktree_and_index_do_not_enter_review(self):
        write(self.repo, "src/service.py", "STAGED_LOCAL_SECRET = 1\n")
        git(self.repo, "add", "src/service.py")
        write(self.repo, "src/service.py", "UNSTAGED_LOCAL_SECRET = 2\n")
        write(self.repo, "untracked.txt", "saved local file\n")
        index_before = (self.repo / ".git/index").read_bytes()
        status_before = git(self.repo, "status", "--porcelain=v1")
        context, manifest = self.capture()
        frozen = evidence.read_blob(manifest, context, "head", "src/service.py")
        self.assertNotIn(b"LOCAL_SECRET", frozen)
        self.assertEqual((self.repo / "src/service.py").read_text(), "UNSTAGED_LOCAL_SECRET = 2\n")
        self.assertEqual(index_before, (self.repo / ".git/index").read_bytes())
        self.assertEqual(status_before, git(self.repo, "status", "--porcelain=v1"))
        self.assertTrue(manifest["working_tree_unchanged"])

    def test_whole_repo_baseline_search_finds_use_in_other_file(self):
        context, manifest = self.capture()
        result = evidence.search(manifest, context, "base", "CoreDb.dbo.Orders")
        self.assertEqual([(m["path"], m["line"]) for m in result["matches"]], [("src/existing.py", 5)])
        identifier = evidence.record_search(context, result)
        self.assertEqual(evidence.validate_baseline_search(context, manifest, identifier), result)

    def test_definition_only_hit_is_available_for_semantic_review(self):
        context, manifest = self.capture()
        result = evidence.search(manifest, context, "base", "InvoiceTax")
        self.assertEqual(result["matches"][0]["path"], "schema.sql")
        self.assertNotIn("src/service.py", [m["path"] for m in result["matches"]])

    def test_baseline_is_target_tip_not_merge_base(self):
        git(self.repo, "switch", "main")
        write(self.repo, "target.py", 'DB = "BillingDb"\n')
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "target changed after feature branched")
        context, manifest = self.capture()
        self.assertNotEqual(manifest["base_sha"], manifest["merge_base"])
        self.assertEqual(evidence.search(manifest, context, "base", "BillingDb")["matches"][-1]["path"],
                         "target.py")
        self.assertNotIn("target.py", evidence.list_files(manifest, context, "merge-base"))
        self.assertIn("target.py", evidence.list_files(manifest, context, "merge-preview"))

    def test_invalid_ref_stops_and_removes_owned_context(self):
        context = self.root / "failed-session"
        args = context_helper.parse_args(["--workspace", str(self.repo), "--project", str(self.repo), "--base", "main",
                                         "--head", "does-not-exist", "--context-dir", str(context)])
        with self.assertRaisesRegex(Exception, "找不到比較版本"):
            context_helper.build_manifest(args)
        self.assertFalse(context.exists())

    def test_multiple_repos_are_not_guessed(self):
        second = init_repo(self.root, "second")
        write(second, "x", "x\n")
        git(second, "add", ".")
        git(second, "commit", "-m", "second")
        with self.assertRaises(SystemExit):
            context_helper.parse_args(["--workspace", str(self.root), "--base", "main", "--head", "feature"])

    def test_explicit_remote_fetch_and_stale_remote_failure(self):
        remote = self.root / "remote.git"
        git(self.root, "init", "--bare", str(remote))
        git(self.repo, "remote", "add", "origin", str(remote))
        git(self.repo, "push", "origin", "main", "feature")
        context, manifest = self.capture(base="origin/main", head="origin/feature")
        self.assertEqual(len(manifest["fetches"]), 2)
        self.assertEqual(manifest["head_sha"], git(self.repo, "rev-parse", "feature"))
        git(self.repo, "remote", "set-url", "origin", str(self.root / "missing-remote.git"))
        with self.assertRaisesRegex(Exception, "失敗"):
            self.capture(base="origin/main", head="origin/feature")

    def test_remote_ref_never_falls_back_to_same_named_local_branch(self):
        remote = self.root / "remote.git"
        git(self.root, "init", "--bare", str(remote))
        git(self.repo, "remote", "add", "origin", str(remote))
        git(self.repo, "branch", "origin/missing")
        self.assertIsNone(context_helper.git.resolve_commit_ref(self.repo, "origin/missing")[0])

    def test_merge_conflict_is_incomplete(self):
        git(self.repo, "switch", "main")
        write(self.repo, "src/service.py", "def average(total, count):\n    return total\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "conflicting main edit")
        context, manifest = self.capture()
        self.assertEqual(manifest["merge_preview"]["status"], "conflicts")
        self.assertFalse(manifest["context_complete"])
        with self.assertRaisesRegex(ValueError, "乾淨"):
            evidence.read_blob(manifest, context, "merge-preview", "src/service.py")

    def test_direct_mode_and_unrelated_history_are_rejected(self):
        git(self.repo, "checkout", "--orphan", "unrelated")
        git(self.repo, "rm", "-rf", ".")
        write(self.repo, "unrelated.py", "VALUE = 1\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "unrelated root")
        for legacy_option in (("--mode", "direct"), ("--format", "json"), ("--include-json",)):
            with self.subTest(option=legacy_option), self.assertRaises(SystemExit):
                context_helper.parse_args(["--project", str(self.repo), "--base", "main", "--head", "unrelated", *legacy_option])
        with self.assertRaisesRegex(Exception, "唯一共同起點"):
            self.capture(head="unrelated")

    def test_tags_and_commit_revisions_are_not_branch_inputs(self):
        git(self.repo, "tag", "v1.0")
        for ref in ("v1.0", "refs/tags/v1.0", git(self.repo, "rev-parse", "HEAD"), "HEAD", "main~1"):
            with self.subTest(ref=ref), self.assertRaises(Exception):
                self.capture(head=ref)

    def test_same_versions_have_no_changed_paths(self):
        context, manifest = self.capture(base="main", head="main")
        self.assertEqual(manifest["changed_files"], [])
        self.assertEqual(manifest["release_changes"], [])
        result = release_report.validate_result(draft_for(manifest), manifest, context)
        self.assertEqual(result["status"], "沒有差異")

    def test_cli_read_search_and_list_are_independent(self):
        context, manifest = self.capture()
        for action, extra in (("list", []), ("read", ["--path", "src/service.py"]),
                              ("search", ["--query", "INVOICE_API_URL"])):
            result = python(SCRIPTS / "evidence.py", action, "--context-dir", context,
                            "--source", "head", *extra)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["ref"], manifest["head_sha"])

    def test_path_escape_and_wrong_sha_are_rejected(self):
        context, manifest = self.capture()
        for path in ("../outside", "/absolute", "C:/outside", r"..\outside"):
            with self.assertRaises(ValueError):
                evidence.read_blob(manifest, context, "base", path)
        with self.assertRaises(ValueError):
            evidence.read_blob(manifest, context, "head", "src/service.py", manifest["base_sha"])

    def test_cleanup_refuses_unowned_directory(self):
        ordinary = self.root / "ordinary"
        ordinary.mkdir()
        write(ordinary, "keep.txt", "keep\n")
        with self.assertRaises(OSError):
            review_session.cleanup_session(ordinary)
        self.assertEqual((ordinary / "keep.txt").read_text(), "keep\n")
