"""Local process-only tests for the shared TypeScript/Python lease protocol."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/release-reviewer/scripts"
sys.path.insert(0, str(SCRIPTS))
import git_network_lease as network
import review_runtime


class NetworkLeaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="review-lease-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.environment = mock.patch.dict(os.environ, {"GITLAB_WORKSPACE_RUNTIME_DIR": str(self.root)})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_nonce_and_existing_owner_are_never_reclaimed_by_age_or_pid(self):
        lease = network.DirectoryLease(timeout=.1).acquire()
        owner = json.loads((lease.path / "owner.json").read_text())
        owner.update(pid=99999999, createdAt="1970-01-01T00:00:00Z", nonce="another-runtime")
        (lease.path / "owner.json").write_text(json.dumps(owner))
        with self.assertRaises(network.LeaseError):
            network.DirectoryLease(timeout=.03).acquire()
        with self.assertRaises(network.LeaseError):
            lease.release()
        self.assertTrue(lease.path.exists())

    def test_cancelled_wait_preserves_the_existing_owner(self):
        lease = network.DirectoryLease(timeout=.1).acquire()
        with self.assertRaisesRegex(network.LeaseError, "cancelled"):
            network.DirectoryLease(timeout=.1, cancelled=lambda: True).acquire()
        lease.release()

    def test_missing_owner_crash_window_stays_closed(self):
        (self.root / "git-network.lock").mkdir()
        with self.assertRaises(network.LeaseError):
            network.DirectoryLease(timeout=.03).acquire()
        self.assertTrue((self.root / "git-network.lock").is_dir())

    def test_transient_sharing_violation_retries_without_changing_nonce(self):
        original = network.os.replace
        attempts = []
        def sharing_once(source, destination):
            attempts.append(1)
            if len(attempts) == 1:
                raise PermissionError(13, "synthetic sharing violation")
            return original(source, destination)
        with mock.patch.object(network.os, "replace", side_effect=sharing_once):
            lease = network.DirectoryLease(timeout=.2).acquire()
        nonce = lease.owner["nonce"]
        self.assertEqual(nonce, json.loads((lease.path / "owner.json").read_text())["nonce"])
        lease.release()

    def test_zero_and_nonzero_process_close_release(self):
        for code in (0, 7):
            result = network.run([sys.executable, "-c", f"raise SystemExit({code})"], timeout=5)
            self.assertEqual(code, result.returncode)
            self.assertFalse((self.root / "git-network.lock").exists())

    def test_timeout_terminates_the_supervisor_and_descendant_tree(self):
        child = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(20)']); time.sleep(20)"
        with self.assertRaises(subprocess.TimeoutExpired):
            network.run([sys.executable, "-c", child], timeout=.3)
        self.assertFalse((self.root / "git-network.lock").exists())

    def test_two_processes_share_one_network_slot(self):
        task = ("import json,sys,time; import git_network_lease as n; "
                "r=n.run([sys.executable,'-c','import json,time; a=time.monotonic(); time.sleep(.15); print(json.dumps([a,time.monotonic()]))'],timeout=10); "
                "sys.stdout.buffer.write(r.stdout)")
        env = {**os.environ, "PYTHONPATH": str(SCRIPTS), "PYTHONDONTWRITEBYTECODE": "1"}
        children = [subprocess.Popen([sys.executable, "-B", "-c", task], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env) for _ in range(2)]
        intervals = []
        for child in children:
            output, error = child.communicate(timeout=15)
            self.assertEqual(0, child.returncode, error)
            intervals.append(json.loads(output))
        intervals.sort()
        self.assertLessEqual(intervals[0][1], intervals[1][0])

    def test_generation_rejects_stale_writer_and_paged_evidence_is_complete_on_disk(self):
        first = {"schema": "fixture"}
        review_runtime.save_manifest(self.root, first)
        stale = dict(first)
        review_runtime.save_manifest(self.root, first)
        with self.assertRaisesRegex(ValueError, "generation"):
            review_runtime.save_manifest(self.root, stale)
        original = {"matches": [{"line": i, "text": "fixed"} for i in range(5)], "output": "full raw"}
        page = review_runtime.page(original, self.root, size=2)
        self.assertEqual(2, len(page["result"]["matches"]))
        self.assertFalse(page["result"]["view"]["view_complete"])
        self.assertEqual(original, review_runtime.load_view(self.root, page["artifact"]))
        next_page = review_runtime.page(original, self.root, size=2, offset=2, artifact=page["artifact"])
        self.assertEqual([2, 3], [row["line"] for row in next_page["result"]["matches"]])


if __name__ == "__main__":
    unittest.main()
