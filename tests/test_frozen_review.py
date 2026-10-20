import copy
from unittest.mock import patch

from support import *
import frozen_evidence


class FrozenReviewTests(RepoTest):
    def test_owned_sources_survive_source_git_removal_and_never_read_source_repo(self):
        peer = init_repo(self.root, "consumer")
        write(peer, "consumer.py", "CONTRACT = 'current-head'\n")
        git(peer, "add", ".")
        git(peer, "commit", "-m", "peer")
        context, manifest = self.capture(workspace=self.root)
        selected = manifest["repositories"][1]
        expected = evidence.read_blob(manifest, context, "head", "src/service.py")
        (self.repo / ".git").rename(self.repo / "source-git-moved")
        (peer / ".git").rename(peer / "source-git-moved")
        write(self.repo, "src/service.py", "UNCOMMITTED_NEW_CONTENT\n")
        with patch.object(evidence.subprocess, "run", wraps=subprocess.run) as calls:
            loaded = evidence.load_manifest(context)
            self.assertEqual(expected, evidence.read_blob(loaded, context, "head", "src/service.py"))
            self.assertIn(b"current-head", evidence.read_blob(loaded, context, "supporting", "consumer.py", repo=selected["id"]))
            found = evidence.search(loaded, context, "base", "EXISTING_TIMEOUT")
            self.assertTrue(found["matches"])
            draft = draft_for(loaded)
            search = evidence.record_search(context, evidence.search(loaded, context, "supporting", "CONTRACT", repo=selected["id"]))
            draft["supporting_coverage"] = [{"repo": selected["id"], "status": "unrelated", "reason": "固定 CONTRACT 與本次平均金額變更無關。", "searches": [search], "evidence": []}]
            report = release_report.publish_report(context, save_draft(context, draft))
        self.assertEqual(self.repo / "review-reports", report.parent)
        self.assertFalse(context.exists())
        for call in calls.call_args_list:
            command = call.args[0]
            if command[0] == "git":
                self.assertTrue(Path(command[2]).is_relative_to(context))
                self.assertFalse({"fetch", "pull", "clone", "ls-remote"} & set(command))
                self.assertEqual("1", call.kwargs["env"]["GIT_NO_LAZY_FETCH"])
                self.assertEqual("", call.kwargs["env"]["GIT_ALLOW_PROTOCOL"])
                self.assertEqual("", call.kwargs["env"]["GIT_ALTERNATE_OBJECT_DIRECTORIES"])

    def test_workspace_and_tracked_instructions_are_frozen_and_verified(self):
        write(self.root, "AGENTS.md", "Workspace review instructions\n")
        write(self.repo, "AGENTS.md", "Tracked repository instructions\n")
        git(self.repo, "add", "AGENTS.md")
        git(self.repo, "commit", "-m", "review instructions")
        context, manifest = self.capture(workspace=self.root)
        self.assertLessEqual(manifest["generated_at"], manifest["capture_completed_at"])
        records = manifest["workspace_instructions"]
        workspace = next(item for item in records if Path(item["origin"]) == self.root / "AGENTS.md")
        write(self.root, "AGENTS.md", "Changed after capture\n")
        frozen = context / workspace["file"]
        self.assertEqual("Workspace review instructions\n", frozen.read_text(encoding="utf-8"))
        self.assertTrue(any(item["path"] == "AGENTS.md" for item in manifest["repositories"][0]["instructions"]))
        evidence.load_manifest(context)
        frozen.write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "指示摘要"):
            evidence.load_manifest(context)

    def test_missing_owned_pack_is_an_error_and_never_falls_back(self):
        context, manifest = self.capture()
        snapshot = frozen_evidence.repository(context, manifest["repositories"][0])
        packed = next((snapshot / "objects/pack").glob("*.pack"))
        packed.chmod(0o600)
        packed.unlink()
        with self.assertRaises(ValueError):
            evidence.load_manifest(context)

    def test_dependencies_require_real_diff_roots_and_causal_description(self):
        context, manifest = self.capture()
        draft = draft_for(manifest)
        item = config_item(context, manifest)
        draft["config_changes"] = [item]
        for bad in ({"diff_roots": []}, {"diff_roots": ["DIFF-999"]}, {"diff_reason": ""}):
            changed = copy.deepcopy(draft)
            changed["config_changes"][0].update(bad)
            with self.assertRaises(release_report.DraftValidationError):
                release_report.validate_result(changed, manifest, context)
        validated = release_report.validate_result(draft, manifest, context)
        self.assertEqual(item["diff_roots"], validated["config_changes"][0]["diff_roots"])

    def test_report_structure_legacy_fields_and_checklist_location(self):
        context, manifest = self.capture()
        draft = draft_for(manifest)
        risk = risk_item(manifest)
        risk.pop("scenario")
        risk.pop("technical_details")
        draft["code_risks"] = [risk]
        draft["config_changes"] = [config_item(context, manifest)]
        report = release_report.publish_report(context, save_draft(context, draft))
        body = report.read_text(encoding="utf-8")
        self.assertTrue(body.startswith(f"# 上版檢查: {self.repo.name}\n"))
        self.assertEqual([line for line in body.splitlines() if line.startswith("## ")], [
            "## 審查結論", "## 問題總攬", "## 問題詳情（工程師參考）", "## 範圍與限制", "## 技術審查紀錄（工程師參考）"])
        for label in ("問題名稱", "影響說明", "操作情境", "建議處理", "技術證據", "觸發條件", "證據說明", "影響範圍", "工程修正建議"):
            self.assertIn(label, body)
        self.assertIn("操作情境**：未提供", body)
        self.assertLess(body.index("## 範圍與限制"), body.index(release_report.CHECKLIST_START))
        self.assertLess(body.index(release_report.CHECKLIST_END), body.index("## 技術審查紀錄"))

    def test_explicit_remote_branches_share_one_atomic_fetch(self):
        remote = self.root / "remote.git"
        git(self.root, "init", "--bare", str(remote))
        git(self.repo, "remote", "add", "origin", str(remote))
        git(self.repo, "push", "origin", "main", "feature")
        original = context_helper.git.run_git
        with patch.object(context_helper.git, "run_git", wraps=original) as calls:
            _, manifest = self.capture(base="origin/main", head="origin/feature")
        fetched = [call.args[1] for call in calls.call_args_list if call.args[1][0] == "fetch"]
        self.assertEqual(1, len(fetched))
        self.assertIn("--atomic", fetched[0])
        self.assertIn("refs/heads/main:refs/remotes/origin/main", fetched[0])
        self.assertIn("refs/heads/feature:refs/remotes/origin/feature", fetched[0])
        self.assertEqual(2, len(manifest["fetches"]))

    def test_new_draft_retains_context_until_engineering_details_are_complete(self):
        context, manifest = self.capture()
        draft = draft_for(manifest)
        draft["report_layout"] = "engineering-review/v1"
        risk = risk_item(manifest)
        draft["code_risks"] = [risk]
        risk["technical_details"].pop("engineering_fix")
        with self.assertRaisesRegex(release_report.DraftValidationError, "新草稿"):
            release_report.publish_report(context, save_draft(context, draft), retain_on_validation_error=True)
        self.assertTrue(context.exists())
        self.assertEqual([], list((self.repo / "review-reports").glob("*.md")))
        risk["technical_details"]["engineering_fix"] = "在除法前恢復零數量防護。"
        report = release_report.publish_report(context, save_draft(context, draft), retain_on_validation_error=True)
        self.assertTrue(report.is_file())
        self.assertFalse(context.exists())
