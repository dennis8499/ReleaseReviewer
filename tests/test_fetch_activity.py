import contextlib
import io
import os
import subprocess
import time
from unittest import mock

from support import *
import frozen_evidence
import review_coordinator as coordination
import review_flow as flow
import review_sync_refs as sync_refs
from review_sync_refs import PrivateRefs


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

    def bundle_remote(self):
        bundle = self.root / "origin.bundle"
        git(self.repo, "bundle", "create", str(bundle), "main", "feature")
        git(self.repo, "remote", "add", "origin", bundle.as_posix())

    def arguments(self, base="main", head="feature", *extra):
        return ["--workspace", str(self.repo), "--project", str(self.repo), "--base", base, "--head", head, *extra]

    def test_local_capture_and_summary_versions_have_zero_activity_and_policy(self):
        _, manifest = self.capture()
        expected = context_helper.git.empty_network_activity()
        self.assertEqual(expected, manifest["network_activity"])
        self.assertEqual("local-branches", manifest["network_log"]["reason"])
        self.assertEqual([], manifest["network_log"]["fetches"])
        self.assertIsNone(manifest["network_log"]["http_requests"])
        self.assertIsNone(manifest["network_log"]["ssh_requests"])
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
            event, = manifest["network_log"]["fetches"]
            self.assertEqual(("explicit-remote-branches", "fetch", "origin", 1, 1),
                             (event["reason"], event["phase"], event["remote"],
                              event["fetch_attempt_count"], event["fetch_success_count"]))
            self.assertEqual({"origin/main", "origin/feature"}, set(event["requested_refs"]))
            self.assertEqual(2, len(manifest["fetches"]))
        _, local = self.capture()
        self.assertEqual(context_helper.git.empty_network_activity(), local["network_activity"])

    def test_capture_keeps_fetched_sha_when_tracking_branch_advances(self):
        self.bundle_remote()
        head = git(self.repo, "rev-parse", "feature")
        git(self.repo, "commit", "--allow-empty", "-m", "A newer observed tip")
        newer = git(self.repo, "rev-parse", "feature")
        fetch = context_helper.git.fetch_inputs

        def fetch_then_advance_tracking(*args, **kwargs):
            activity = fetch(*args, **kwargs)
            git(self.repo, "update-ref", "refs/remotes/origin/feature", newer)
            return activity

        with mock.patch.object(context_helper.git, "fetch_inputs", side_effect=fetch_then_advance_tracking):
            _, manifest = self.capture(base="origin/main", head="origin/feature")
        self.assertEqual(head, manifest["head_sha"])
        self.assertEqual(newer, git(self.repo, "rev-parse", "refs/remotes/origin/feature"))
        self.assertEqual("", git(self.repo, "for-each-ref", "--format=%(refname)", "refs/release-reviewer-sync"))
        self.assertEqual(1, manifest["network_activity"]["fetch_attempt_count"])

    def test_cancel_after_fetch_cleans_request_refs(self):
        self.bundle_remote()
        run_git = context_helper.git.run_git
        head = git(self.repo, "rev-parse", "feature")
        args = flow.parser().parse_args(["start", *self.arguments("origin/main", "origin/feature"),
                                        "--runtime-root", str(self.root / "review-runtime")])

        def cancel_after_fetch(repo, command, **kwargs):
            if command[0] == "fetch":
                active = flow.coordination.ACTIVE.get()
                job, _ = flow.metadata(active.root, active.identifier)
                lookup = PrivateRefs(self.repo)
                registered = job["private_ref_journal"]
                self.assertEqual(lookup.scope_sha256, registered["scope_sha256"])
                journal = coordination.read_json(lookup.directory / (registered["id"] + ".json"))
                self.assertEqual(active.identifier, journal["request_id"])
                self.assertTrue(journal["fetching"])
            result = run_git(repo, command, **kwargs)
            if command[0] == "fetch":
                refs = git(self.repo, "for-each-ref", "--format=%(refname)", "refs/release-reviewer-sync")
                self.assertEqual(2, len(refs.splitlines()))
                active = flow.coordination.ACTIVE.get()
                (active.root / "jobs" / (active.identifier + ".cancel")).touch()
            return result

        with mock.patch.object(context_helper.git, "run_git", side_effect=cancel_after_fetch):
            with self.assertRaises(flow.coordination.Cancelled):
                flow.start(args)
        self.assertEqual("", git(self.repo, "for-each-ref", "--format=%(refname)", "refs/release-reviewer-sync"))
        self.assertEqual(head, git(self.repo, "rev-parse", "refs/remotes/origin/feature"))

    def assert_request_ref_retry(self, runtime, identifier, state):
        job = flow.status(runtime, identifier)
        self.assertEqual(state, job["state"])
        self.assertTrue(flow.status_summary(runtime, identifier)["private_ref_cleanup_pending"])
        self.assertEqual(1, job["network_activity"]["fetch_attempt_count"])
        self.assertEqual(1, job["network_log"]["fetches"][0]["fetch_attempt_count"])
        journal_id = job["private_ref_journal"]["id"]
        journal = PrivateRefs(self.repo).directory / (journal_id + ".json")
        receipt = coordination.read_json(journal)
        self.assertEqual(identifier, receipt["request_id"])
        self.assertEqual(2, len(receipt["refs"]))
        for ref, sha in receipt["refs"].items():
            self.assertEqual(sha, git(self.repo, "rev-parse", ref))
        retry = flow.cancel(runtime, identifier)
        self.assertEqual(state, retry["state"])
        self.assertEqual(job["network_activity"], retry["network_activity"])
        self.assertEqual({key: value for key, value in job["network_log"].items() if key != "private_ref_cleanup_pending"},
                         retry["network_log"])
        self.assertFalse(journal.exists())
        self.assertEqual("", git(self.repo, "for-each-ref", "--format=%(refname)",
                                 "refs/release-reviewer-sync/" + journal_id))

    def test_failed_evidence_writes_preserve_request_ref_retry(self):
        self.bundle_remote()
        runtime = self.root / "native"
        cases = (
            ("manifest", context_helper.review_runtime, "save_manifest", "a" * 32),
            ("worklist", context_helper.review_worklist, "write_worklist", "b" * 32),
        )
        for label, module, operation, identifier in cases:
            with self.subTest(label=label):
                args = flow.parser().parse_args(["start", *self.arguments("origin/main", "origin/feature"),
                                                 "--runtime-root", str(runtime), "--request-id", identifier])
                with mock.patch.object(PrivateRefs, "_clean", return_value=False), \
                        mock.patch.object(module, operation, side_effect=PermissionError("evidence write denied")):
                    with self.assertRaisesRegex(PermissionError, "evidence write denied"):
                        flow.start(args)
                self.assertIn("evidence write denied", flow.status(runtime, identifier)["error"])
                self.assert_request_ref_retry(runtime, identifier, "failed")

    def test_cancellation_after_capture_preserves_request_ref_retry(self):
        self.bundle_remote()
        runtime, identifier = self.root / "native", "c" * 32
        args = flow.parser().parse_args(["start", *self.arguments("origin/main", "origin/feature"),
                                         "--runtime-root", str(runtime), "--request-id", identifier])
        capture = context_helper.build_manifest

        def cancel_before_ready(options):
            manifest = capture(options)
            (runtime / "jobs" / (identifier + ".cancel")).touch()
            return manifest

        with mock.patch.object(PrivateRefs, "_clean", return_value=False), \
                mock.patch.object(context_helper, "build_manifest", side_effect=cancel_before_ready):
            with self.assertRaises(coordination.Cancelled):
                flow.start(args)
        self.assert_request_ref_retry(runtime, identifier, "cancelled")

    def test_registering_the_request_journal_is_required_before_fetch(self):
        self.bundle_remote()
        runtime = self.root / "native"
        identifier = "a" * 32
        args = flow.parser().parse_args(["start", *self.arguments("origin/main", "origin/feature"),
                                        "--runtime-root", str(runtime), "--request-id", identifier])
        update = flow.update

        def fail_registration(root, request_id, **fields):
            if "private_ref_journal" in fields:
                raise PermissionError("cannot record journal")
            return update(root, request_id, **fields)

        with mock.patch.object(flow, "update", side_effect=fail_registration), \
                mock.patch.object(context_helper.git, "run_git", wraps=context_helper.git.run_git) as commands:
            with self.assertRaisesRegex(PermissionError, "cannot record journal"):
                flow.start(args)
        self.assertFalse(any(call.args[1][0] == "fetch" for call in commands.call_args_list))
        self.assertEqual("", git(self.repo, "for-each-ref", "--format=%(refname)", "refs/release-reviewer-sync"))
        self.assertIsNone(flow.status(runtime, identifier)["private_ref_journal"])
        with mock.patch.object(sync_refs, "recover_request") as recover:
            flow.cancel(runtime, identifier)
            recover.assert_not_called()

    def test_forced_stop_recovers_refs_before_their_shas_were_recorded(self):
        self.bundle_remote()
        identifier = "a" * 32
        runtime = self.root / "native"
        paused = self.root / "after-fetch"
        program = """
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import git_support as git
import review_flow as flow
original = git.run_git
def pause_after_fetch(repo, command, **kwargs):
    result = original(repo, command, **kwargs)
    if command[0] == 'fetch':
        Path(sys.argv[2]).touch()
        time.sleep(120)
    return result
git.run_git = pause_after_fetch
sys.exit(flow.main(sys.argv[3:]))
"""
        arguments = self.arguments("origin/main", "origin/feature", "--runtime-root", str(runtime), "--request-id", identifier)
        child = subprocess.Popen([sys.executable, "-X", "utf8", "-B", "-c", program, str(SCRIPTS), str(paused),
                                  "start", *arguments], env=command_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 60
            while not paused.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(.05)
            if child.poll() is not None:
                self.fail(child.communicate()[1].decode("utf-8"))
            self.assertTrue(paused.exists(), "Git fetch did not reach the interruption point")
            helper = PrivateRefs(self.repo)
            journal, = list(helper.directory.glob("*.json"))
            receipt = json.loads(journal.read_text(encoding="utf-8"))
            self.assertTrue(receipt["fetching"])
            self.assertEqual(identifier, receipt["request_id"])
            self.assertEqual(2, len(receipt["refs"]))
            self.assertEqual({None}, set(receipt["refs"].values()))
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=20)

        sibling = PrivateRefs(self.repo, "b" * 32)
        ref = sibling.namespace + "/0"
        sha = git(self.repo, "rev-parse", "main")
        sibling.begin([ref])
        try:
            git(self.repo, "update-ref", ref, sha)
            sibling.settled()
            sibling.pin({ref: sha})
            run_capture = context_helper.git.run_git_capture

            def no_exit_proof(repo, command, **kwargs):
                if kwargs.get("supervised"):
                    raise context_helper.git.git_network_lease.LeaseError("Git child remains unconfirmed")
                return run_capture(repo, command, **kwargs)

            with mock.patch.object(context_helper.git, "run_git_capture", side_effect=no_exit_proof):
                interrupted = flow.cancel(runtime, identifier)
                self.assertEqual("cancelled", interrupted["state"])
                self.assertTrue(flow.status_summary(runtime, identifier)["private_ref_cleanup_pending"])
                self.assertTrue(journal.exists())
                self.assertEqual({*receipt["refs"], ref}, set(git(self.repo, "for-each-ref", "--format=%(refname)",
                                                                "refs/release-reviewer-sync").splitlines()))

            with coordination.FileLock(journal.with_suffix(".lock")):
                flow.cancel(runtime, identifier)
                self.assertTrue(flow.status_summary(runtime, identifier)["private_ref_cleanup_pending"])
                self.assertTrue(journal.exists())
            read_json = coordination.read_json

            def unreadable_sibling(candidate):
                if candidate == sibling.path:
                    raise PermissionError("unrelated active journal is inaccessible")
                return read_json(candidate)

            with mock.patch.object(coordination, "read_json", side_effect=unreadable_sibling) as reads:
                flow.cancel(runtime, identifier)
            self.assertNotIn(mock.call(sibling.path), reads.call_args_list)
            self.assertFalse(journal.exists())
            self.assertEqual(ref, git(self.repo, "for-each-ref", "--format=%(refname)", "refs/release-reviewer-sync"))
            finished = flow.status_summary(runtime, identifier)
            self.assertNotIn("private_ref_cleanup_pending", finished)
            flow.cancel(runtime, identifier)
            self.assertEqual(finished, flow.status_summary(runtime, identifier))
            self.assertEqual(sha, git(self.repo, "rev-parse", ref))
        finally:
            sibling.close()

    def abandoned_job_with_ref(self, *, registered):
        identifier = "d" * 32
        runtime = coordination.runtime(self.root / "native", create=True)
        journal = PrivateRefs(self.repo, identifier)
        ref = journal.namespace + "/0"
        sha = git(self.repo, "rev-parse", "main")
        journal.begin([ref])
        try:
            git(self.repo, "update-ref", ref, sha)
            journal.settled()
            journal.pin({ref: sha})
        finally:
            journal.owner.release()
        job = {"schema": flow.SCHEMA, "request_id": identifier, "runtime_root": str(runtime), "state": "cancelled",
               "inputs": {"project": str(self.repo)}, "network_log": {"private_ref_cleanup_pending": True}}
        if registered:
            job["private_ref_journal"] = {"id": journal.identifier, "scope_sha256": journal.scope_sha256}
        (runtime / "jobs").mkdir()
        coordination.atomic(flow.job_path(runtime, identifier), job)
        return runtime, identifier, journal.path, ref

    def test_registered_retry_keeps_pending_after_runtime_or_git_directory_changes(self):
        runtime, identifier, journal, ref = self.abandoned_job_with_ref(registered=True)
        with mock.patch.dict(os.environ, {"GITLAB_WORKSPACE_RUNTIME_DIR": str(self.root / "replacement-runtime")}):
            result = flow.cancel(runtime, identifier)
            self.assertTrue(result["network_log"]["private_ref_cleanup_pending"])
        self.assertTrue(journal.exists())
        self.assertTrue(git(self.repo, "rev-parse", ref))

        original = self.repo / ".git"
        saved = self.repo / "original-git"
        original.rename(saved)
        try:
            git(self.repo, "init", "-q")
            result = flow.cancel(runtime, identifier)
            self.assertTrue(result["network_log"]["private_ref_cleanup_pending"])
            self.assertTrue(journal.exists())
        finally:
            if original.exists():
                original.rename(self.repo / "replacement-git")
            saved.rename(original)
        result = flow.cancel(runtime, identifier)
        self.assertNotIn("private_ref_cleanup_pending", result["network_log"])
        self.assertFalse(journal.exists())
        self.assertEqual("", git(self.repo, "for-each-ref", "--format=%(refname)", ref))

    def test_terminal_legacy_job_can_retry_its_original_journal(self):
        runtime, identifier, journal, ref = self.abandoned_job_with_ref(registered=False)
        result = flow.cancel(runtime, identifier)
        self.assertNotIn("private_ref_cleanup_pending", result["network_log"])
        self.assertFalse(journal.exists())
        self.assertEqual("", git(self.repo, "for-each-ref", "--format=%(refname)", ref))

    def test_registered_retry_preserves_an_unreadable_or_foreign_journal(self):
        runtime, identifier, journal, ref = self.abandoned_job_with_ref(registered=True)
        original = coordination.read_json(journal)
        read_json = coordination.read_json

        def unreadable(candidate):
            if candidate == journal:
                raise PermissionError("registered journal is inaccessible")
            return read_json(candidate)

        with mock.patch.object(coordination, "read_json", side_effect=unreadable):
            result = flow.cancel(runtime, identifier)
        self.assertTrue(result["network_log"]["private_ref_cleanup_pending"])
        coordination.atomic(journal, {**original, "request_id": "e" * 32})
        result = flow.cancel(runtime, identifier)
        self.assertTrue(result["network_log"]["private_ref_cleanup_pending"])
        self.assertEqual("e" * 32, coordination.read_json(journal)["request_id"])
        self.assertEqual(original["refs"][ref], git(self.repo, "rev-parse", ref))

    def test_next_capture_keeps_changed_refs_and_cleans_other_abandoned_refs(self):
        self.bundle_remote()
        previous = PrivateRefs(self.repo)
        changed, owned = (previous.namespace + f"/{index}" for index in range(2))
        old = git(self.repo, "rev-parse", "main")
        current = git(self.repo, "rev-parse", "feature")
        previous.begin([changed, owned])
        try:
            git(self.repo, "update-ref", changed, current)
            git(self.repo, "update-ref", owned, old)
            previous.settled()
            previous.pin({changed: old, owned: old})
        finally:
            previous.owner.release()
        _, manifest = self.capture(base="origin/main", head="origin/feature")
        self.assertEqual(current, manifest["head_sha"])
        self.assertEqual(changed, git(self.repo, "for-each-ref", "--format=%(refname)", "refs/release-reviewer-sync"))
        self.assertEqual(current, git(self.repo, "rev-parse", changed))
        self.assertTrue(manifest["network_log"]["repository_private_ref_cleanup_pending"])
        self.assertNotIn("private_ref_cleanup_pending", manifest["network_log"])
        self.assertEqual({changed: old}, json.loads(previous.path.read_text(encoding="utf-8"))["refs"])

    def test_recovery_records_the_original_sha_before_a_conflicting_delete(self):
        journal = PrivateRefs(self.repo, "c" * 32)
        ref = journal.namespace + "/0"
        original = git(self.repo, "rev-parse", "main")
        changed = git(self.repo, "rev-parse", "feature")
        journal.begin([ref])
        journal.settled()
        git(self.repo, "update-ref", ref, original)
        journal.owner.release()
        delete = sync_refs._delete_direct_ref

        def move_before_delete(repo, name, expected, deadline):
            self.assertEqual({ref: original}, coordination.read_json(journal.path)["refs"])
            self.assertEqual(original, expected)
            git(repo, "update-ref", name, changed, original)
            return delete(repo, name, expected, deadline)

        with mock.patch.object(sync_refs, "_delete_direct_ref", side_effect=move_before_delete):
            self.assertFalse(sync_refs.recover_request(self.repo, "c" * 32))
        self.assertFalse(sync_refs.recover_request(self.repo, "c" * 32))
        self.assertEqual(changed, git(self.repo, "rev-parse", ref))
        self.assertEqual({ref: original}, coordination.read_json(journal.path)["refs"])

    def test_prepared_transaction_deletes_only_direct_refs_in_both_git_formats(self):
        ref = "refs/release-reviewer-sync/" + "e" * 32 + "/0"
        for backend in ("files", "reftable"):
            with self.subTest(backend=backend):
                repo = self.root / ("refs-" + backend)
                git(self.root, "init", "--ref-format=" + backend, str(repo))
                sha = git(repo, "hash-object", "-w", str(Path(__file__)))
                target = "refs/fixture/target"
                git(repo, "update-ref", target, sha)
                git(repo, "symbolic-ref", ref, target)
                self.assertFalse(sync_refs._delete_direct_ref(repo, ref, sha, time.monotonic() + 8))
                self.assertEqual(target, git(repo, "symbolic-ref", "--no-recurse", ref))
                git(repo, "symbolic-ref", "--delete", ref)
                git(repo, "update-ref", ref, sha)
                read = context_helper.git.run_git_capture

                def competing_writer(repo, args, **kwargs):
                    with self.assertRaisesRegex(AssertionError, "cannot lock ref"):
                        git(repo, "-c", "core.filesRefLockTimeout=10", "-c", "reftable.lockTimeout=10", "symbolic-ref", ref, target)
                    return read(repo, args, **kwargs)

                with mock.patch.object(context_helper.git, "run_git_capture", side_effect=competing_writer):
                    self.assertTrue(sync_refs._delete_direct_ref(repo, ref, sha, time.monotonic() + 8))
                self.assertEqual("", git(repo, "for-each-ref", "--format=%(refname)", ref))
                self.assertEqual(sha, git(repo, "rev-parse", target))

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
        events = raised.exception.network_log["fetches"]
        self.assertEqual([("origin", 1, 1), ("upstream", 1, 0)],
                         [(row["remote"], row["fetch_attempt_count"], row["fetch_success_count"]) for row in events])

    def test_same_request_retry_reuses_the_original_network_log(self):
        self.remote()
        args = flow.parser().parse_args(["start", "--workspace", str(self.root), "--project", str(self.repo),
                                         "--base", "origin/main", "--head", "origin/feature"])
        first = flow.start(args)
        with mock.patch.object(context_helper.git, "fetch_inputs", side_effect=AssertionError("unexpected refetch")):
            second = flow.start(args)
        self.assertEqual(first["request_id"], second["request_id"])
        self.assertEqual(first["network_log"], second["network_log"])
        flow.cancel(Path(first["runtime_root"]), first["request_id"])

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
