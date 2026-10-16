import copy

from support import *
import review_worklist


class WorklistTests(RepoTest):
    def test_capture_seeds_deduplicated_old_and_final_reads_and_unfinished_draft(self):
        context, manifest = self.capture()
        worklist = json.loads((context / "review-worklist.json").read_text(encoding="utf-8"))
        draft = json.loads((context / "review-result-draft.json").read_text(encoding="utf-8"))
        keys = [(r["repo"], r["source"], r["ref"], r["path"]) for r in worklist["requests"]]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertIn(("entry", "base", manifest["base_sha"], "src/service.py"), keys)
        self.assertIn(("entry", "head", manifest["head_sha"], "src/service.py"), keys)
        self.assertIn(("entry", "merge-preview", manifest["merge_preview"]["tree_sha"], "src/service.py"), keys)
        self.assertEqual({r["path"] for r in draft["coverage"]}, {"src/service.py", "src/settings.json"})
        self.assertTrue(all(c["status"] == "not-reviewed" for c in draft["coverage"] + draft["dependency_coverage"]))
        self.assertEqual({c["kind"] for c in draft["dependency_coverage"]}, {"config", "db", "ftp", "sftp", "redis", "kafka", "other"})
        result = release_report.validate_result(draft, manifest, context)
        self.assertFalse(result["review_complete"])
        self.assertEqual("incomplete", result["release_assessment"])

    def test_rename_delete_binary_and_gitlink_keep_honest_worklist(self):
        git(self.repo, "mv", "src/service.py", "src/renamed.py")
        git(self.repo, "rm", "src/settings.json")
        write(self.repo, "src/payload.bin", b"\0binary")
        commit = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "add", ".")
        git(self.repo, "update-index", "--add", "--cacheinfo", f"160000,{commit},linked-repo")
        git(self.repo, "commit", "-m", "rename delete binary gitlink")
        context, manifest = self.capture()
        worklist, draft = review_worklist.prepare(manifest)
        self.assertIn("src/settings.json", {r["path"] for r in worklist["requests"]})
        self.assertEqual({"src/payload.bin", "linked-repo"}, {r["path"] for r in worklist["metadata_only"]})
        self.assertFalse(any(r["path"] in {"src/payload.bin", "linked-repo"} for r in worklist["requests"]))
        self.assertFalse(release_report.validate_result(draft, manifest, context)["review_complete"])

    def test_all_parent_views_and_supporting_unavailable_are_preserved(self):
        context, original = self.capture()
        manifest = copy.deepcopy(original)
        parent1, parent2 = manifest["base_sha"], manifest["head_sha"]
        change = {"path": "src/service.py", "status": "M", "old_path": None, "old_mode": "100644", "new_mode": "100644"}
        manifest["merge_commits"] = [{"sha": parent2, "parents": [parent1, parent2], "parent_views": [
            {"position": n, "sha": parent, "changes": [change]} for n, parent in enumerate((parent1, parent2), 1)]}]
        manifest["repositories"].append({"id": "repo-001", "role": "supporting", "status": "unavailable", "reason": "missing branch"})
        worklist, draft = review_worklist.prepare(manifest)
        self.assertEqual([v["parent"] for v in worklist["merge_parent_views"]], [parent1, parent2])
        self.assertEqual({r["ref"] for r in worklist["requests"] if r["source"] == "merge-parent"}, {parent1, parent2})
        self.assertEqual(draft["supporting_coverage"], [{"repo": "repo-001", "status": "not-reviewed", "reason": "missing branch", "searches": [], "evidence": []}])

    def test_no_difference_does_not_invent_reads(self):
        context, manifest = self.capture(head="main")
        worklist, draft = review_worklist.prepare(manifest)
        self.assertEqual([], worklist["requests"])
        self.assertEqual([], draft["coverage"])
        self.assertTrue(all(r["status"] == "not-reviewed" for r in draft["dependency_coverage"]))

    def test_powershell_utf8_bom_requests_and_draft_are_accepted(self):
        context, manifest = self.capture()
        requests = [{"action": "search", "source": "base", "query": "BillingDb"}]
        request_file = context / "requests.json"
        request_file.write_text(json.dumps(requests), encoding="utf-8-sig")
        self.assertEqual(requests, evidence.load_requests(str(request_file)))
        self.assertEqual(requests, evidence.load_requests("\ufeff" + json.dumps(requests)))
        draft_path = context / "review-result-draft.json"
        draft_path.write_text(json.dumps(draft_for(manifest)), encoding="utf-8-sig")
        report = release_report.publish_report(context, draft_path, retain_on_validation_error=True)
        self.assertTrue(report.is_file())
        self.assertFalse(context.exists())


if __name__ == "__main__":
    unittest.main()
