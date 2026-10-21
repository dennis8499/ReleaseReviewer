import os
from pathlib import Path
from unittest import mock

from support import *
import frozen_evidence


class CapturePreflightTests(RepoTest):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, {"GITLAB_WORKSPACE_RUNTIME_DIR": str(self.root / "runtime")})
        patcher.start()
        self.addCleanup(patcher.stop)
        remote = self.root / "origin.git"
        git(self.root, "init", "--bare", str(remote))
        git(self.repo, "remote", "add", "origin", str(remote))
        git(self.repo, "push", "origin", "main", "feature")

    def capture_remote(self, **kwargs):
        return self.capture(base="origin/main", head="origin/feature", **kwargs)

    def ancestor_branches(self):
        git(self.repo, "switch", "main")
        git(self.repo, "merge", "--ff-only", "feature")
        for number in range(2):
            write(self.repo, f"base-{number}.py", f"base_step = {number}\n")
            git(self.repo, "add", ".")
            git(self.repo, "commit", "-m", f"base step {number}")
        git(self.repo, "switch", "feature")
        git(self.repo, "push", "origin", "main", "feature")
        return git(self.repo, "rev-parse", "main"), git(self.repo, "rev-parse", "feature")

    def test_ancestor_graph_read_failure_stops_without_refetch_and_keeps_activity(self):
        self.ancestor_branches()
        original, original_create, created = context_helper.git.run_git, review_session.create_session, []

        def unreadable(repo, args, **kwargs):
            if args[:3] == ["rev-list", "--ancestry-path", "--parents"]:
                raise context_helper.git.GitCommandError(args, 128, "ancestor graph unavailable")
            return original(repo, args, **kwargs)

        def create(*args):
            session = original_create(*args)
            created.append(session)
            return session

        with mock.patch.object(context_helper.git, "run_git", side_effect=unreadable) as calls, \
                mock.patch.object(review_session, "create_session", side_effect=create):
            with self.assertRaisesRegex(context_helper.git.ReviewContextError, "ancestor graph unavailable") as raised:
                self.capture_remote()
        self.assertFalse(created[0].exists())
        self.assertEqual("ancestor-proof", raised.exception.phase)
        self.assertEqual(1, raised.exception.network_activity["fetch_attempt_count"])
        self.assertEqual(1, raised.exception.network_activity["fetch_success_count"])
        self.assertEqual(1, len([call for call in calls.call_args_list if call.args[1][0] == "fetch"]))

    def test_ancestor_graph_simplified_link_cannot_replace_original_commit_parent(self):
        base, head = self.ancestor_branches()
        original = context_helper.git.run_git

        def simplified(repo, args, **kwargs):
            if args[:3] == ["rev-list", "--ancestry-path", "--parents"]:
                return f"{base} {head}\n"
            return original(repo, args, **kwargs)

        with mock.patch.object(context_helper.git, "run_git", side_effect=simplified) as calls:
            with self.assertRaisesRegex(ValueError, "parent link"):
                context_helper.capture_ancestor_path(self.repo, base, head)
        self.assertFalse(any(call.args[1][0] == "fetch" for call in calls.call_args_list))

    def test_ancestor_path_uses_one_batch_process_as_the_commit_chain_grows(self):
        base, head = self.ancestor_branches()
        lengths = []
        for extra in (0, 3):
            if extra:
                git(self.repo, "switch", "main")
                for number in range(extra):
                    git(self.repo, "commit", "--allow-empty", "-m", f"additional ancestor {number}")
                base = git(self.repo, "rev-parse", "main")
            with mock.patch.object(context_helper.git, "run_git", wraps=context_helper.git.run_git) as calls:
                path = context_helper.capture_ancestor_path(self.repo, base, head)
            lengths.append(len(path))
            batch = [call for call in calls.call_args_list if call.args[1][0] == "cat-file"]
            self.assertEqual(1, len(batch))
            self.assertEqual(["cat-file", "--batch"], batch[0].args[1])
            self.assertEqual(("\n".join(path) + "\n").encode("ascii"), batch[0].kwargs["input_bytes"])
            self.assertFalse(any(call.args[1][0] == "fetch" for call in calls.call_args_list))
        self.assertEqual([3, 6], lengths)

    def test_catalog_source_output_instructions_and_fingerprint_checks_precede_fetch(self):
        peer = init_repo(self.root, "consumer")
        write(peer, "consumer.py", "consumer = True\n")
        git(peer, "add", ".")
        git(peer, "commit", "-m", "peer")
        write(self.root, "AGENTS.md", "Workspace instructions\n")
        calls, fingerprints = [], []
        original, fingerprint = context_helper.git.run_git, context_helper.worktree_fingerprint

        def trace(repo, args, **kwargs):
            if args[0] == "fetch":
                for candidate in (self.repo, peer):
                    self.assertIn((candidate, ("rev-parse", "--show-toplevel")), calls)
                    self.assertIn((candidate, ("rev-parse", "--show-object-format")), calls)
                self.assertTrue(any(path == peer and "HEAD^{commit}" in command for path, command in calls))
                self.assertEqual([self.repo], fingerprints)
                session = Path(active_args.context_dir)
                self.assertTrue((session / "objects").is_dir())
                self.assertTrue((session / "repos/entry/snapshot.git").is_dir())
                self.assertTrue((session / "repos/repo-001/snapshot.git").is_dir())
                self.assertTrue((session / "instructions/workspace-001.md").is_file())
            calls.append((Path(repo), tuple(args)))
            return original(repo, args, **kwargs)

        def trace_fingerprint(repo):
            fingerprints.append(repo)
            return fingerprint(repo)

        active_args = context_helper.parse_args(["--workspace", str(self.root), "--project", str(self.repo),
                                                 "--base", "origin/main", "--head", "origin/feature"])
        original_create = review_session.create_session

        def create(*args):
            session = original_create(*args)
            active_args.context_dir = str(session)
            return session

        with mock.patch.object(context_helper.git, "run_git", side_effect=trace), \
                mock.patch.object(context_helper, "worktree_fingerprint", side_effect=trace_fingerprint), \
                mock.patch.object(review_session, "create_session", side_effect=create):
            manifest = context_helper.build_manifest(active_args)
        self.contexts.append(Path(manifest["context_dir"]))
        self.assertEqual(1, manifest["network_activity"]["fetch_attempt_count"])

    def assert_zero_fetch_failure(self, patcher, error, message, phase):
        with patcher, mock.patch.object(context_helper.git, "fetch_inputs", wraps=context_helper.git.fetch_inputs) as fetch:
            with self.assertRaisesRegex(error, message) as raised:
                self.capture_remote()
        fetch.assert_not_called()
        self.assertEqual(phase, raised.exception.phase)
        self.assertEqual(context_helper.git.empty_network_activity(), raised.exception.network_activity)

    def test_instruction_access_failure_precedes_fetch(self):
        self.assert_zero_fetch_failure(mock.patch.object(frozen_evidence, "capture_workspace_instructions",
                                                        side_effect=PermissionError("instructions denied")),
                                       PermissionError, "instructions denied", "output-preflight")

    def test_fingerprint_failure_precedes_fetch(self):
        self.assert_zero_fetch_failure(mock.patch.object(context_helper, "worktree_fingerprint",
                                                        side_effect=PermissionError("index denied")),
                                       PermissionError, "index denied", "output-preflight")

    def test_session_failure_is_reported_with_zero_activity(self):
        self.assert_zero_fetch_failure(mock.patch.object(review_session, "create_session",
                                                        side_effect=PermissionError("temp denied")),
                                       PermissionError, "temp denied", "session-preflight")

    def test_unsupported_selected_object_format_precedes_fetch(self):
        original = context_helper.git.run_git

        def unsupported(repo, args, **kwargs):
            return "unknown\n" if args == ["rev-parse", "--show-object-format"] else original(repo, args, **kwargs)

        self.assert_zero_fetch_failure(mock.patch.object(context_helper.git, "run_git", side_effect=unsupported),
                                       ValueError, "物件格式", "output-preflight")

    def test_unreadable_source_objects_precede_fetch(self):
        original = os.scandir

        def inaccessible(path):
            if isinstance(path, (str, os.PathLike)) and Path(path) == self.repo / ".git/objects":
                raise PermissionError("objects denied")
            return original(path)

        self.assert_zero_fetch_failure(mock.patch.object(context_helper.os, "scandir", side_effect=inaccessible),
                                       PermissionError, "objects denied", "output-preflight")

    def test_output_directory_failure_precedes_fetch(self):
        original = frozen_evidence.owned_path

        def inaccessible(context, relative):
            if relative == "repos/entry/snapshot.git":
                raise PermissionError("output denied")
            return original(context, relative)

        self.assert_zero_fetch_failure(mock.patch.object(frozen_evidence, "owned_path", side_effect=inaccessible),
                                       PermissionError, "output denied", "output-preflight")

    def test_session_inside_repo_is_rejected_and_cleaned_before_fetch(self):
        target = self.repo / "owned-evidence"
        args = context_helper.parse_args(["--workspace", str(self.repo), "--project", str(self.repo),
                                          "--base", "origin/main", "--head", "origin/feature", "--context-dir", str(target)])
        with mock.patch.object(context_helper.git, "fetch_inputs", wraps=context_helper.git.fetch_inputs) as fetch:
            with self.assertRaisesRegex(ValueError, "repository 外") as raised:
                context_helper.build_manifest(args)
        fetch.assert_not_called()
        self.assertFalse(target.exists())
        self.assertEqual(context_helper.git.empty_network_activity(), raised.exception.network_activity)

    def test_peer_preflight_failure_remains_a_gap_without_peer_fetch_or_recapture(self):
        peer = init_repo(self.root, "consumer")
        write(peer, "consumer.py", "consumer = True\n")
        git(peer, "add", ".")
        git(peer, "commit", "-m", "peer")
        original = context_helper.git.run_git
        checked = []

        def unsupported_peer(repo, args, **kwargs):
            if Path(repo) == peer and args == ["rev-parse", "--show-object-format"]:
                checked.append(peer)
                return "unknown\n"
            if args[0] == "fetch":
                self.assertEqual([peer], checked)
                self.assertEqual(self.repo, Path(repo))
            return original(repo, args, **kwargs)

        with mock.patch.object(context_helper.git, "run_git", side_effect=unsupported_peer):
            _, manifest = self.capture_remote(workspace=self.root)
        record = next(row for row in manifest["repositories"] if row["path"] == str(peer))
        self.assertEqual("unavailable", record["status"])
        self.assertNotIn("snapshot", record)
        self.assertTrue(any("物件格式" in gap for gap in manifest["context_gaps"]))
        self.assertEqual([peer], checked)
        self.assertEqual(1, manifest["network_activity"]["fetch_attempt_count"])
