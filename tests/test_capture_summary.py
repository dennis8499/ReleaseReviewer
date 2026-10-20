import copy
from unittest.mock import patch

from support import *


class CaptureSummaryTests(RepoTest):
    def test_projection_preserves_identity_counts_all_files_and_full_manifest_without_git(self):
        write(self.repo, "asset.bin", b"\0binary\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "metadata-only scope")
        context, manifest = self.capture()
        original = copy.deepcopy(manifest)
        files = {p.relative_to(context): p.read_bytes() for p in context.rglob("*") if p.is_file()}
        with patch.object(context_helper.git, "run_git", side_effect=AssertionError("unexpected Git")), \
                patch.object(context_helper.git, "run_git_capture", side_effect=AssertionError("unexpected Git")):
            summary = context_helper.capture_summary(manifest)
        self.assertEqual(original, manifest)
        self.assertEqual(files, {p.relative_to(context): p.read_bytes() for p in context.rglob("*") if p.is_file()})
        self.assertEqual("ReleaseReviewCaptureSummary/v1", summary["schema"])
        self.assertEqual(manifest["schema"], summary["context_schema"])
        self.assertEqual((SKILL / "VERSION").read_text(encoding="utf-8").strip(), summary["skill_version"])
        for key in ("repo", "project", "base_input", "head_input", "base_resolved_ref", "head_resolved_ref",
                    "base_sha", "head_sha", "merge_base", "diff_base", "repositories", "context_gaps",
                    "context_complete", "report_root", "working_tree_unchanged", "scope", "context_dir"):
            self.assertEqual(manifest[key], summary[key], key)
        worklist = json.loads((context / "review-worklist.json").read_text(encoding="utf-8"))
        self.assertEqual(len(worklist["requests"]), summary["counts"]["read_requests"])
        self.assertEqual(len(worklist["required_coverage"]), summary["counts"]["required_coverage"])
        self.assertGreater(summary["counts"]["metadata_only"], 0)
        self.assertEqual(len(manifest["release_changes"]), summary["counts"]["release_changes"])
        self.assertEqual(manifest["merge_preview"]["tree_sha"], summary["merge_preview"]["tree_sha"])
        for path in summary["files"].values():
            self.assertTrue(Path(path).is_absolute())
            self.assertTrue(Path(path).is_file())
        self.assertEqual(manifest, json.loads(Path(summary["files"]["manifest"]).read_text(encoding="utf-8")))
        self.assertNotIn("changed_files", summary)

    def test_cli_summary_pretty_and_legacy_manifest_have_independent_shapes(self):
        for flags in ((), ("--pretty",), ("--summary",), ("--summary", "--pretty")):
            with self.subTest(flags=flags):
                result = python(SCRIPTS / "git_review_context.py", "--workspace", self.repo,
                                "--project", self.repo, "--base", "main", "--head", "feature", *flags)
                self.assertEqual(0, result.returncode, result.stderr)
                output = json.loads(result.stdout)
                context = Path(output["context_dir"])
                self.contexts.append(context)
                manifest = json.loads((context / "manifest.json").read_text(encoding="utf-8"))
                if "--summary" in flags:
                    self.assertEqual(context_helper.capture_summary(manifest), output)
                    self.assertLess(len(result.stdout.encode("utf-8")),
                                    len(json.dumps(manifest, ensure_ascii=False,
                                                   indent=2 if "--pretty" in flags else None).encode("utf-8")))
                else:
                    self.assertEqual(manifest, output)
                    self.assertNotIn("files", output)
                    self.assertNotIn("counts", output)

    def test_conflicts_and_missing_catalog_versions_retain_complete_gaps(self):
        peer = init_repo(self.root, "missing-default")
        write(peer, "consumer.py", "def consume(payload):\n    return payload['amount']\n")
        git(peer, "add", ".")
        git(peer, "commit", "-m", "consumer without explicit default hint")
        git(peer, "symbolic-ref", "HEAD", "refs/heads/unborn")
        git(self.repo, "switch", "main")
        write(self.repo, "src/service.py", "def average(total, count):\n    return -1\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "conflicting base")
        context, manifest = self.capture(workspace=self.root)
        self.assertFalse(manifest["context_complete"])
        summary = context_helper.capture_summary(manifest)
        self.assertEqual("conflicts", summary["merge_preview"]["status"])
        self.assertEqual(manifest["context_gaps"], summary["context_gaps"])
        self.assertEqual(manifest["repositories"], summary["repositories"])
        self.assertTrue(any(repo["status"] == "unavailable" for repo in summary["repositories"]))
        self.assertNotIn("release_diff", summary["files"])
        self.assertTrue(context.is_dir())

    def test_summary_cannot_bypass_normal_publisher_validation(self):
        context, manifest = self.capture()
        context_helper.capture_summary(manifest)
        draft = draft_for(manifest)
        draft["coverage"][0]["evidence"][0]["ref"] = "0" * 40
        result = python(SCRIPTS / "release_report.py", "--context-dir", context,
                        "--result", save_draft(context, draft))
        self.assertEqual(2, result.returncode)
        self.assertFalse(context.exists())
        self.assertEqual([], list(self.repo.glob("release-review-*.md")))


if __name__ == "__main__":
    unittest.main()
