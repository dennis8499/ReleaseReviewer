"""Use unchanged owned layouts under real, long Unicode Windows runtimes."""
from collections import Counter
import contextlib
import os
import shutil
import tempfile
from unittest.mock import patch

from support import *
import frozen_evidence
import evidence
import git_support as git_module
import git_network_lease
import review_flow as flow
import review_runtime


def operation(command):
    index = command.index("-C") + 2
    return command[index + 2] if command[index:index + 2] == ["-c", "core.longpaths=true"] else command[index]


def command_location(row):
    location = row["argv"][row["argv"].index("-C") + 1]
    return Path(row.get("cwd") or location).resolve()


class StreamGitCommandTests(unittest.TestCase):
    def test_original_argv_and_environment_are_unchanged(self):
        repo = Path("plain-source")
        completed = subprocess.CompletedProcess([], 0, stdout=b"fixed\n", stderr=b"")
        for platform in ("nt", "posix"):
            with patch.object(git_module.os, "name", platform), patch.object(git_module.subprocess, "run", return_value=completed) as run:
                git_module.run_git(repo, ["cat-file", "--batch-check"])
                git_module.run_git_capture(repo, ["diff", "base", "head"])
                evidence.command({"repo": str(repo)}, ["grep", "query"])
            for call in run.call_args_list:
                self.assertEqual(["git", "-C", str(repo)], call.args[0][:3])
                self.assertNotIn("core.longpaths=true", call.args[0])

    def test_pack_stdout_is_streamed_to_caller_file(self):
        with tempfile.TemporaryFile() as output:
            completed = subprocess.CompletedProcess([], 0, stdout=None, stderr=b"")
            with patch.object(git_module.subprocess, "run", return_value=completed) as run:
                self.assertEqual("", git_module.run_git(Path("source"), ["pack-objects", "--stdout"], output_file=output))
            self.assertIs(output, run.call_args.kwargs["stdout"])


class PackIntegrityTests(RepoTest):
    def test_sha256_pack_checksum_and_both_file_hashes_remain_bound(self):
        repo = self.root / "sha256-source"
        git(self.root, "init", "--object-format=sha256", "-b", "main", str(repo))
        git(repo, "config", "user.name", "Test")
        git(repo, "config", "user.email", "test@example.invalid")
        git(repo, "config", "commit.gpgsign", "false")
        write(repo, "blob.txt", "SHA256_OBJECT_CONTENT\n")
        git(repo, "add", ".")
        git(repo, "commit", "-m", "sha256")
        reference = git(repo, "rev-parse", "HEAD")
        context = review_session.create_session()
        self.addCleanup(review_session.cleanup_if_owned, context)
        record = {"id": "entry", "path": str(repo)}
        frozen_evidence.seal(context, record, [reference])
        snapshot = frozen_evidence.repository(context, record)
        packed = record["snapshot"]["pack"]
        self.assertEqual("sha256", record["snapshot"]["object_format"])
        self.assertEqual(64, len(packed["checksum"]))
        self.assertEqual(frozen_evidence.file_sha256(snapshot / packed["file"]), packed["sha256"])
        self.assertEqual(frozen_evidence.file_sha256(snapshot / packed["index"]), packed["index_sha256"])
        frozen_evidence.validate(context, record)
        packed["checksum"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "checksum|驗證收據"):
            frozen_evidence.validate(context, record)

    def test_legacy_hash_named_snapshot_is_not_migrated_or_recaptured(self):
        context, manifest = self.capture()
        record = manifest["repositories"][0]
        snapshot = frozen_evidence.repository(context, record)
        packed = record["snapshot"].pop("pack")
        # Simulate a context produced before validation receipts existed.
        record.pop("validation_receipt")
        manifest.pop("validation_policy")
        for extension in ("pack", "idx"):
            (snapshot / ("objects/pack/snapshot." + extension)).rename(
                snapshot / ("objects/pack/snapshot-" + packed["checksum"] + "." + extension))
        review_runtime.save_manifest(context, manifest)
        (self.repo / ".git").rename(self.repo / "original-git-removed")
        with patch.object(git_module.subprocess, "run", wraps=subprocess.run) as calls:
            loaded = evidence.load_manifest(context)
            self.assertIn(b"BillingDb.dbo.InvoiceTax", evidence.read_blob(loaded, context, "head", "src/service.py"))
        for call in calls.call_args_list:
            self.assertTrue(Path(call.args[0][2]).is_relative_to(context))
        self.assertTrue((snapshot / ("objects/pack/snapshot-" + packed["checksum"] + ".pack")).exists())
        self.assertFalse((snapshot / "objects/pack/snapshot.pack").exists())

    def test_index_corruption_is_fatal_before_git_and_never_rebuilt(self):
        context, manifest = self.capture()
        record = manifest["repositories"][0]
        index = frozen_evidence.repository(context, record) / record["snapshot"]["pack"]["index"]
        index.chmod(0o600)
        index.write_bytes(b"corrupt index")
        with patch.object(git_module.subprocess, "run", side_effect=AssertionError("must not invoke Git")):
            with self.assertRaisesRegex(ValueError, "index.*摘要|驗證收據"):
                evidence.load_manifest(context)


@unittest.skipUnless(os.name == "nt", "Real Git for Windows long-path integration")
class WindowsLongPathTests(RepoTest):
    def runtime(self, final_pack_units):
        runtime = self.root / "Unicode-\u6e2c\u8a66"
        suffix = Path("sessions") / ("a" * 32) / "repos/entry/snapshot.git/objects/pack" / ("snapshot-" + "0" * 40 + ".pack")
        remaining = final_pack_units - len(str(runtime / suffix).encode("utf-16-le")) // 2
        self.assertGreater(remaining, 4)
        # Match the failed production runtime's ten extra UTF-8 bytes from
        # five BMP Chinese characters without changing its UTF-16 depth.
        runtime /= "\u5c64\u6e2c\u8a66" + "x" * (remaining - 4)
        self.assertEqual(final_pack_units, len(str(runtime / suffix).encode("utf-16-le")) // 2)
        return runtime

    def test_atomic_json_supports_a_temporary_path_beyond_legacy_limit(self):
        with tempfile.TemporaryDirectory(prefix="long-atomic-", dir=self.root) as temporary_root:
            base = Path(temporary_root)
            filename = "result.json"
            units = lambda path: len(str(path).encode("utf-16-le")) // 2
            filler_length = 230 - units(base / filename) - 1
            self.assertGreater(filler_length, 0)
            context = base / ("x" * filler_length)
            context.mkdir()
            target = context / filename
            temporary = target.with_name("." + target.name + "-" + "0" * 32)
            self.assertEqual(230, units(target))
            self.assertGreater(units(temporary), 260)

            git_network_lease._atomic_json(target, {"written": True})

            self.assertEqual({"written": True}, json.loads(target.read_text(encoding="utf-8")))
            target.unlink()
            context.rmdir()

    def test_search_record_roundtrips_beyond_legacy_path_limit(self):
        with tempfile.TemporaryDirectory(prefix="long-search-", dir=self.root) as temporary_root:
            base = Path(temporary_root)
            filename = "0" * 64 + ".json"
            base_target = base / "searches" / filename
            units = lambda path: len(str(path).encode("utf-16-le")) // 2
            filler_length = 261 - units(base_target) - 1
            self.assertGreater(filler_length, 0)
            context = base / ("x" * filler_length)
            context.mkdir()
            expected_target = context / "searches" / filename
            self.assertEqual(261, units(expected_target))

            record = {"repo": "entry", "source": "base", "ref": "refs/heads/main",
                      "query": "InvoiceTax", "ignore_case": False, "scope": "repo", "matches": []}
            identifier = evidence.record_search(context, record)
            target = context / "searches" / f"{identifier}.json"

            self.assertEqual(261, units(target))
            self.assertEqual([target], review_runtime.path_children(context / "searches", suffix=".json"))
            self.assertEqual(record, json.loads(review_runtime.path_read_text(target, encoding="utf-8")))
            git_network_lease._plain_directory(target)
            os.unlink(review_runtime._extended_windows_path(target))
            os.rmdir(review_runtime._extended_windows_path(context / "searches"))
            os.rmdir(review_runtime._extended_windows_path(context))

    def test_search_step_and_finish_work_when_session_ledger_path_exceeds_limit(self):
        base = self.root / "long-runtime"
        base.mkdir()
        filename = "0" * 64 + ".json"
        probe = base / "sessions" / ("0" * 32) / "searches" / filename
        units = lambda path: len(str(path).encode("utf-16-le")) // 2
        filler_length = 261 - units(probe) - 1
        self.assertGreater(filler_length, 0)
        runtime = base / ("r" * filler_length)
        with patch.dict(os.environ, {"GITLAB_WORKSPACE_RUNTIME_DIR": str(runtime)}):
            args = flow.parser().parse_args(["start", "--workspace", str(self.root),
                                             "--project", str(self.repo), "--base", "main",
                                             "--head", "feature", "--runtime-root", str(runtime)])
            started = flow.start(args)
            root, identifier = Path(started["runtime_root"]), started["request_id"]
            context = Path(started["context_dir"])
            target = context / "searches" / filename
            self.assertEqual(261, units(target))

            result = flow.step(root, identifier, [
                {"id": "long-ledger", "op": "search", "source": "base", "query": "EXISTING_TIMEOUT"}])
            self.assertTrue(result["complete"])
            self.assertEqual(1, result["search_activity"]["executed"])
            self.assertEqual(1, len(review_runtime.path_children(context / "searches", suffix=".json")))

            manifest = evidence.load_manifest(context, full=False)
            save_draft(context, draft_for(manifest))
            report, code = flow.finish(root, identifier)
            self.assertEqual(0, code)
            self.assertTrue(Path(report["report_path"]).is_file())
            self.assertFalse(context.exists())

    def test_long_artifact_view_uses_extended_path_for_atomic_fallback(self):
        with tempfile.TemporaryDirectory(prefix="long-view-", dir=self.root) as temporary_root:
            base = Path(temporary_root)
            filename = "0" * 64 + ".json"
            base_target = base / "views" / filename
            units = lambda path: len(str(path).encode("utf-16-le")) // 2
            filler_length = 261 - units(base_target) - 1
            self.assertGreater(filler_length, 0)
            context = base / ("x" * filler_length)
            context.mkdir()
            target = context / "views" / filename
            self.assertEqual(261, units(target))

            value = {"saved": "long-path evidence"}
            with patch.object(review_runtime.os, "link", side_effect=PermissionError("hard links denied")):
                identifier = review_runtime.store_view(context, value)

            self.assertEqual(value, review_runtime.load_view(context, identifier))
            actual_target = context / "views" / (identifier + ".json")
            os.unlink(review_runtime._extended_windows_path(actual_target))
            (context / "views").rmdir()
            context.rmdir()

    @contextlib.contextmanager
    def record_commands(self):
        commands, real_run = [], subprocess.run
        def run(command, *args, **kwargs):
            result = real_run(command, *args, **kwargs)
            if command and command[0] == "git":
                commands.append({"argv": list(map(str, command)), "cwd": kwargs.get("cwd"), "returncode": result.returncode})
            return result
        try:
            with patch.object(git_module.subprocess, "run", side_effect=run):
                yield commands
        finally:
            output = os.environ.get("RELEASE_REVIEWER_TEST_EVIDENCE_DIR")
            if output:
                destination = Path(output)
                destination.mkdir(parents=True, exist_ok=True)
                (destination / (self._testMethodName + ".json")).write_text(json.dumps(commands, indent=2) + "\n", encoding="utf-8")

    def start(self, runtime):
        return flow.start(flow.parser().parse_args([
            "start", "--workspace", str(self.root), "--project", str(self.repo),
            "--base", "main", "--head", "feature", "--runtime-root", str(runtime)]))

    def inherited_false(self):
        # These are this test's isolated Repo/config files, never user settings.
        git(self.repo, "config", "core.longpaths", "false")
        global_config = self.root / "isolated-global.gitconfig"
        global_config.write_bytes(b"[core]\n\tlongpaths = false\n")
        return global_config, {"GIT_CONFIG_GLOBAL": str(global_config), "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.longpaths", "GIT_CONFIG_VALUE_0": "false",
            "GIT_CONFIG_PARAMETERS": "'core.longpaths=false'"}

    def assert_owned_commands(self, commands, context):
        for row in commands:
            command = row["argv"]
            self.assertTrue(command_location(row).is_relative_to(context.resolve()), row)
            self.assertNotIn("core.longpaths=true", command)
        return Counter(operation(row["argv"]) for row in commands)

    def full_session(self, units, source_change):
        runtime = self.runtime(units)
        global_config, inherited = self.inherited_false()
        source_config = (self.repo / ".git/config").read_bytes()
        global_bytes = global_config.read_bytes()
        with patch.dict(os.environ, inherited), self.record_commands() as commands:
            started = self.start(runtime)
            identifier, context = started["request_id"], Path(started["context_dir"])
            manifest = evidence.load_manifest(context, full=False)
            snapshot = frozen_evidence.repository(context, manifest["repositories"][0])
            pack = next((snapshot / "objects/pack").glob("*.pack"))
            actual_units = len(str(pack).encode("utf-16-le")) // 2
            self.assertEqual(units - 41, actual_units)
            self.assertEqual("snapshot.pack", pack.name)
            self.assertEqual(manifest["repositories"][0]["snapshot"]["pack"]["sha256"], frozen_evidence.file_sha256(pack))
            self.assertEqual(source_config, (self.repo / ".git/config").read_bytes())
            self.assertEqual(global_bytes, global_config.read_bytes())
            capture_commands = list(commands)
            for row in capture_commands:
                command = row["argv"]
                self.assertNotIn("core.longpaths=true", command)
            if source_change == "move":
                moved = self.root / "relocated-source"
                self.assertTrue(self.repo.resolve().is_relative_to(self.root))
                self.assertTrue(moved.resolve().is_relative_to(self.root))
                self.repo.rename(moved)
                config_after = moved / ".git/config"
            elif source_change == "delete":
                self.assertTrue(self.repo.resolve().is_relative_to(self.root))
                shutil.rmtree(self.repo, onerror=lambda function, path, error: (os.chmod(path, 0o700), function(path)))
                config_after = None
            else:
                (self.repo / ".git/HEAD").write_bytes(b"corrupt original HEAD\n")
                write(self.repo, "src/service.py", "CORRUPTED_SOURCE_NOT_EVIDENCE\n")
                config_after = self.repo / ".git/config"
            boundary = len(commands)
            round_result = flow.step(runtime, identifier, [
                {"id": "read", "op": "read", "source": "head", "path": "src/service.py"},
                {"id": "base", "op": "search", "source": "base", "query": "EXISTING_TIMEOUT"},
                {"id": "head", "op": "search", "source": "head", "query": "billing_timeout"}])
            self.assertTrue(round_result["complete"])
            self.assertIn("BillingDb.dbo.InvoiceTax", "\n".join(line["text"] for line in round_result["operations"][0]["result"]["lines"]))
            self.assertTrue(round_result["operations"][1]["result"]["matches"])
            self.assertIn("src/service.py", evidence.list_files(manifest, context, "head"))
            env = frozen_evidence.environment(context, manifest["repositories"][0])
            difference = git_module.run_git(snapshot, ["diff", "--stat", manifest["base_sha"], manifest["head_sha"]], env=env)
            self.assertIn("src/service.py", difference)
            self.assertEqual("false", git_module.run_git(snapshot, ["config", "--get", "core.longpaths"], env=env).strip())
            if not self.repo.exists():
                # Recreate only the authorized publication directory. No source
                # Git/content is restored for finish or its fresh ledger replay.
                (self.repo / "review-reports").mkdir(parents=True)
            save_draft(context, draft_for(manifest))
            finished, code = flow.finish(runtime, identifier)
            self.assertEqual(0, code)
            self.assertTrue(Path(finished["report_path"]).is_file())
            self.assertTrue(finished["review_complete"])
            self.assertFalse(context.exists())
            operations = self.assert_owned_commands(commands[boundary:], context)
            self.assertTrue({"verify-pack", "ls-tree", "cat-file", "grep", "diff"} <= set(operations))
            self.assertEqual(1, sum(operation(row["argv"]) == "pack-objects" for row in commands))
            self.assertEqual(global_bytes, global_config.read_bytes())
            if config_after:
                self.assertEqual(source_config, config_after.read_bytes())
            print(json.dumps({"case": self._testMethodName, "pack_path": str(pack), "utf16_units": actual_units,
                "unchanged_runtime_legacy_pack_utf16_units": units,
                "path_lengths": {name: {"utf16_units": len(str(path).encode("utf-16-le")) // 2,
                                         "utf8_bytes": len(str(path).encode("utf-8"))}
                                 for name, path in (("runtime", runtime), ("job", runtime / "jobs" / (identifier + ".json")),
                                                    ("context", context), ("store", snapshot), ("pack", pack))},
                "source_change": source_change, "post_capture_owned_operations": dict(operations),
                "unchanged_git_argv_and_inherited_false": True, "global_config_unchanged": True,
                "source_config_unchanged_by_helpers": True, "context_removed": True}, ensure_ascii=False))

    def test_unicode_264_pack_full_session_after_source_repo_move(self):
        self.full_session(264, "move")

    def test_unicode_264_pack_full_session_after_source_repo_deletion(self):
        self.full_session(264, "delete")

    def test_extreme_320_pack_root_reports_real_git_platform_limit(self):
        runtime = self.runtime(320)
        with self.record_commands() as commands:
            with self.assertRaisesRegex(git_module.ReviewContextError, "Windows Git.*owned snapshot.*路徑限制"):
                self.start(runtime)
            jobs = list((runtime / "jobs").glob("*.json"))
            self.assertEqual([], jobs)
            self.assertFalse((runtime / "sessions").exists())
            self.assertFalse(any(operation(row["argv"]) == "pack-objects" for row in commands))

    def test_short_path_full_session_after_source_repo_corruption(self):
        self.full_session(240, "corrupt")

    def test_long_owned_pack_corruption_stays_fatal_without_source_fallback(self):
        runtime = self.runtime(264)
        with self.record_commands() as commands:
            started = self.start(runtime)
            context, identifier = Path(started["context_dir"]), started["request_id"]
            manifest = evidence.load_manifest(context, full=False)
            snapshot = frozen_evidence.repository(context, manifest["repositories"][0])
            pack = next((snapshot / "objects/pack").glob("*.pack"))
            pack.chmod(0o600)
            pack.write_bytes(b"corrupt owned snapshot pack")
            boundary = len(commands)
            with self.assertRaises(ValueError):
                flow.step(runtime, identifier, [{"id": "search", "op": "search", "query": "billing_timeout"}])
            self.assertEqual("failed", flow.status(runtime, identifier)["state"])
            self.assertFalse(context.exists())
            self.assert_owned_commands(commands[boundary:], context)
            self.assertEqual(1, sum(operation(row["argv"]) == "pack-objects" for row in commands))
            print(json.dumps({"case": self._testMethodName, "utf16_units": 264,
                "corrupt_pack_fatal": True, "source_fallback": False, "recapture": False, "context_removed": True}))
