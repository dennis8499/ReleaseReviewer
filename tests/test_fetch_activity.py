import contextlib
import io
import os
import subprocess
from unittest import mock

from support import *
import frozen_evidence


class FetchActivityTests(RepoTest):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, {"GITLAB_WORKSPACE_RUNTIME_DIR": str(self.root / "runtime")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def remote(self, name="origin"):
        remote = self.root / f"{name}.git"
        git(self.root, "init", "--bare", str(remote))
        git(self.repo, "remote", "add", name, str(remote))
        git(self.repo, "push", name, "main", "feature")
        return remote

    def arguments(self, base="main", head="feature", *extra):
        return ["--workspace", str(self.repo), "--project", str(self.repo), "--base", base, "--head", head, *extra]

    def test_local_capture_and_summary_versions_have_zero_activity_and_policy(self):
        _, manifest = self.capture()
        expected = context_helper.git.empty_network_activity()
        self.assertEqual(expected, manifest["network_activity"])
        self.assertEqual("ancestor-noop/v1", manifest["coverage_policy"])
        for projector in (context_helper.capture_summary, context_helper.capture_summary_v2):
            summary = projector(manifest)
            self.assertEqual(expected, summary["network_activity"])
            self.assertEqual(manifest["coverage_policy"], summary["coverage_policy"])
        for flag in ("--summary", "--summary-v2"):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(0, context_helper.main(self.arguments("main", "feature", flag)))
            compact = json.loads(out.getvalue())
            self.contexts.append(Path(compact["context_dir"]))
            self.assertEqual(expected, compact["network_activity"])

    def test_two_refs_share_one_fetch_and_counts_reset_on_each_capture(self):
        self.remote()
        expected = {"fetch_attempt_count": 1, "fetch_success_count": 1,
                    "requested_ref_count": 2, "synced_ref_count": 2}
        for _ in range(2):
            with mock.patch.object(context_helper.git, "run_git", wraps=context_helper.git.run_git) as calls:
                _, manifest = self.capture(base="origin/main", head="origin/feature")
            self.assertEqual(1, len([call for call in calls.call_args_list if call.args[1][0] == "fetch"]))
            self.assertEqual(expected, manifest["network_activity"])
            self.assertEqual(2, len(manifest["fetches"]))
        _, local = self.capture()
        self.assertEqual(context_helper.git.empty_network_activity(), local["network_activity"])

    def test_different_remotes_each_fetch_once(self):
        self.remote()
        self.remote("upstream")
        with mock.patch.object(context_helper.git, "run_git", wraps=context_helper.git.run_git) as calls:
            _, manifest = self.capture(base="origin/main", head="upstream/feature")
        self.assertEqual(2, len([call for call in calls.call_args_list if call.args[1][0] == "fetch"]))
        self.assertEqual({"fetch_attempt_count": 2, "fetch_success_count": 2,
                          "requested_ref_count": 2, "synced_ref_count": 2}, manifest["network_activity"])

    def test_duplicate_remote_ref_is_requested_once(self):
        self.remote()
        _, manifest = self.capture(base="origin/main", head="origin/main")
        self.assertEqual(1, len(manifest["fetches"]))
        self.assertEqual({"fetch_attempt_count": 1, "fetch_success_count": 1,
                          "requested_ref_count": 1, "synced_ref_count": 1}, manifest["network_activity"])

    def test_fetch_failure_does_not_use_stale_refs_or_retry(self):
        self.remote()
        previous = git(self.repo, "rev-parse", "refs/remotes/origin/main")
        git(self.repo, "remote", "set-url", "origin", str(self.root / "missing.git"))
        with mock.patch.object(context_helper.git, "run_git", wraps=context_helper.git.run_git) as calls:
            with self.assertRaisesRegex(context_helper.git.ReviewContextError, "fetch origin 失敗") as raised:
                self.capture(base="origin/main", head="origin/feature")
        self.assertEqual(previous, git(self.repo, "rev-parse", "refs/remotes/origin/main"))
        self.assertEqual(1, len([call for call in calls.call_args_list if call.args[1][0] == "fetch"]))
        self.assertEqual("fetch", raised.exception.phase)
        self.assertEqual({"fetch_attempt_count": 1, "fetch_success_count": 0,
                          "requested_ref_count": 2, "synced_ref_count": 0}, raised.exception.network_activity)
        self.assertIsNotNone(raised.exception.hint)

    def test_partial_remote_failure_preserves_successful_fetch_counts(self):
        self.remote()
        self.remote("upstream")
        git(self.repo, "remote", "set-url", "upstream", str(self.root / "missing.git"))
        with self.assertRaisesRegex(context_helper.git.ReviewContextError, "fetch upstream 失敗") as raised:
            self.capture(base="origin/main", head="upstream/feature")
        self.assertEqual({"fetch_attempt_count": 2, "fetch_success_count": 1,
                          "requested_ref_count": 2, "synced_ref_count": 1}, raised.exception.network_activity)

    def test_timeout_diagnostic_preserves_message_exit_code_and_empty_stdout(self):
        self.remote()
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(context_helper.git.git_network_lease, "run", side_effect=subprocess.TimeoutExpired("git", 1)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = context_helper.main(self.arguments("origin/main", "origin/feature"))
        self.assertEqual(2, result)
        self.assertEqual("", out.getvalue())
        self.assertIn("逾時", err.getvalue())
        diagnostic = json.loads(next(line.split("diagnostic: ", 1)[1] for line in err.getvalue().splitlines()
                                     if "diagnostic: " in line))
        self.assertEqual("fetch", diagnostic["phase"])
        self.assertEqual({"fetch_attempt_count": 1, "fetch_success_count": 0,
                          "requested_ref_count": 2, "synced_ref_count": 0}, diagnostic["network_activity"])

    def test_post_fetch_failure_keeps_activity_after_context_cleanup(self):
        self.remote()
        original_create, created = review_session.create_session, []

        def track(*args):
            session = original_create(*args)
            created.append(session)
            return session

        with mock.patch.object(review_session, "create_session", side_effect=track), \
                mock.patch.object(frozen_evidence, "seal", side_effect=PermissionError("pack denied")):
            with self.assertRaisesRegex(PermissionError, "pack denied") as raised:
                self.capture(base="origin/main", head="origin/feature")
        self.assertFalse(created[0].exists())
        self.assertEqual("snapshot-capture", raised.exception.phase)
        self.assertEqual({"fetch_attempt_count": 1, "fetch_success_count": 1,
                          "requested_ref_count": 2, "synced_ref_count": 2}, raised.exception.network_activity)
        self.assertEqual(context_helper.git.empty_network_activity(), context_helper.git.network_activity())

    def test_frozen_reads_search_and_publication_issue_zero_additional_fetches(self):
        self.remote()
        context, manifest = self.capture(base="origin/main", head="origin/feature")
        with mock.patch.object(context_helper.git, "fetch_inputs", side_effect=AssertionError("unexpected fetch")), \
                mock.patch.object(evidence.subprocess, "run", wraps=subprocess.run) as calls:
            loaded = evidence.load_manifest(context)
            self.assertIn(b"average", evidence.read_blob(loaded, context, "head", "src/service.py"))
            self.assertTrue(evidence.search(loaded, context, "base", "EXISTING_TIMEOUT")["matches"])
            report = release_report.publish_report(context, save_draft(context, draft_for(loaded)))
        self.assertTrue(report.is_file())
        self.assertFalse(context.exists())
        for call in calls.call_args_list:
            command = call.args[0]
            if command[0] == "git":
                self.assertFalse({"fetch", "pull", "clone", "ls-remote"} & set(command))
                self.assertEqual("", call.kwargs["env"]["GIT_ALLOW_PROTOCOL"])

    def test_summary_failure_reports_completed_fetch_activity(self):
        self.remote()
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(context_helper, "capture_summary", side_effect=ValueError("summary denied")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = context_helper.main(self.arguments("origin/main", "origin/feature", "--summary"))
        self.assertEqual(2, result)
        self.assertEqual("", out.getvalue())
        self.assertIn("summary denied", err.getvalue())
        diagnostic = json.loads(next(line.split("diagnostic: ", 1)[1] for line in err.getvalue().splitlines()
                                     if "diagnostic: " in line))
        self.assertEqual("capture-summary", diagnostic["phase"])
        self.assertEqual(1, diagnostic["network_activity"]["fetch_success_count"])
