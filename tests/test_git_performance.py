"""Compatibility checks for fewer Git subprocesses, using real Git objects."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from support import command_env, init_repo
import git_support as git


def legacy_changes(repo, left, right):
    """The original three-pass behavior, kept as an independent oracle."""
    options = ["--find-renames", "--find-copies", "-z", left, right, "--"]
    changes = git.parse_name_status(git.run_git(
        repo, ["diff", *git.DIFF_FLAGS, "--name-status", "--diff-algorithm=histogram", *options]))
    numstat = git.parse_numstat(git.run_git(repo, ["diff", *git.DIFF_FLAGS, "--numstat", *options]))
    parts = git.run_git(repo, ["diff", *git.DIFF_FLAGS, "--raw", "--no-abbrev", *options]).split("\0")
    metadata = {}
    index = 0
    while index < len(parts):
        header = parts[index]
        index += 1
        if not header:
            continue
        old_mode, new_mode, old_blob, new_blob, status = header.removeprefix(":").split()[:5]
        if status.startswith(("R", "C")):
            old_path, path = parts[index:index + 2]
            index += 2
        else:
            old_path, path = None, parts[index]
            index += 1
        metadata[old_path, path] = dict(old_mode=old_mode, new_mode=new_mode,
                                        old_blob=old_blob, new_blob=new_blob)
    for change in changes:
        additions, deletions = numstat.get(change["path"], ("0", "0"))
        change.update(additions=None if additions == "-" else int(additions),
                      deletions=None if deletions == "-" else int(deletions),
                      binary=additions == "-" or deletions == "-")
        change.update(metadata.get((change["old_path"], change["path"]), {}))
    return changes


def legacy_merges(repo, left, right):
    result = []
    for sha in git.run_git(repo, ["rev-list", "--merges", "--reverse", f"{left}..{right}"]).splitlines():
        parents = git.run_git(repo, ["rev-list", "--parents", "-n", "1", sha]).split()[1:]
        result.append({
            "sha": sha,
            "subject": git.run_git(repo, ["show", "-s", "--format=%s", sha]).strip(),
            "parents": parents,
            "parent_views": [dict(position=position, sha=parent,
                                  changes=legacy_changes(repo, parent, sha),
                                  stat=git.run_git(repo, ["diff", "--stat", "--find-renames", parent, sha]).strip())
                             for position, parent in enumerate(parents, 1)],
        })
    return result


class GitPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = init_repo(Path(self.temporary.name))
        self.environment = command_env()
        self.environment_patch = mock.patch.dict("os.environ", self.environment)
        self.environment_patch.start()
        self.addCleanup(self.environment_patch.stop)

    def command(self, *args, data=None):
        result = subprocess.run(["git", "-C", str(self.repo), *args], input=data,
                                capture_output=True, env=self.environment, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        return result.stdout.decode("utf-8").strip()

    def blob(self, data):
        return self.command("hash-object", "-w", "--stdin", data=data)

    def tree(self, entries):
        # mktree permits Git-valid names that Windows cannot create on disk.
        raw = b"".join(f"{mode} {'commit' if mode == '160000' else 'blob'} {sha}\t{name}\0".encode("utf-8")
                       for name, (mode, sha) in entries.items())
        return self.command("mktree", "-z", data=raw)

    def commit(self, tree, parents=(), subject="fixture"):
        args = ["commit-tree", tree]
        for parent in parents:
            args += ["-p", parent]
        return self.command(*args, "-m", subject)

    def changes_fixture(self):
        empty = self.tree({})
        anchor = self.commit(empty, subject="gitlink baseline")
        next_anchor = self.commit(empty, [anchor], "gitlink update")
        original = self.blob(b"".join(f"original copy source line {n}\n".encode() for n in range(30)))
        old = {
            "rename old\tline\n名字.txt": ("100644", self.blob(b"unique rename content\n")),
            "copy-source.txt": ("100644", original),
            "deleted.txt": ("100644", self.blob(b"removed file\n")),
            "binary.dat": ("100644", self.blob(b"before\0binary\n")),
            "mode.sh": ("100644", self.blob(b"#!/bin/sh\necho mode\n")),
            "type-change.txt": ("100644", self.blob(b"ordinary file\n")),
            ":leading colon.txt": ("100644", self.blob(b"before colon\n")),
            "-leading dash.txt": ("100644", self.blob(b"before dash\n")),
            "submodule": ("160000", anchor),
        }
        new = dict(old)
        new["renamed\tline\n名字.txt"] = new.pop("rename old\tline\n名字.txt")
        del new["deleted.txt"]
        new.update({
            "copied.txt": ("100644", original),
            "copy-source.txt": ("100644", self.blob(b"".join(f"modified source line {n}\n".encode() for n in range(30)))),
            "binary.dat": ("100644", self.blob(b"after\0binary\n")),
            "mode.sh": ("100755", old["mode.sh"][1]),
            "type-change.txt": ("120000", self.blob(b"mode.sh")),
            ":leading colon.txt": ("100644", self.blob(b"after colon\n")),
            "-leading dash.txt": ("100644", self.blob(b"after dash\n")),
            "新增\tfile\n.txt": ("100644", self.blob(b"new Unicode path\n")),
            "submodule": ("160000", next_anchor),
        })
        left = self.commit(self.tree(old), [anchor], "baseline")
        right = self.commit(self.tree(new), [left], "changes")
        return left, right

    def test_combined_diff_matches_three_pass_output_for_all_metadata(self):
        left, right = self.changes_fixture()
        expected = legacy_changes(self.repo, left, right)
        actual = git.collect_changes(self.repo, left, right)
        self.assertEqual(actual, expected)
        by_path = {item["path"]: item for item in actual}
        self.assertEqual(by_path["renamed\tline\n名字.txt"]["status"], "R100")
        self.assertEqual(by_path["renamed\tline\n名字.txt"]["old_path"], "rename old\tline\n名字.txt")
        self.assertEqual(by_path["copied.txt"]["status"], "C100")
        self.assertEqual(by_path["copied.txt"]["old_path"], "copy-source.txt")
        self.assertEqual(by_path["deleted.txt"]["status"], "D")
        self.assertTrue(by_path["binary.dat"]["binary"])
        self.assertIsNone(by_path["binary.dat"]["additions"])
        self.assertIsNone(by_path["binary.dat"]["deletions"])
        self.assertEqual((by_path["mode.sh"]["old_mode"], by_path["mode.sh"]["new_mode"]), ("100644", "100755"))
        self.assertEqual(by_path["type-change.txt"]["new_mode"], "120000")
        self.assertEqual(by_path["submodule"]["old_mode"], "160000")
        self.assertNotEqual(by_path["submodule"]["old_blob"], by_path["submodule"]["new_blob"])
        self.assertEqual(by_path["新增\tfile\n.txt"]["status"], "A")

    def test_each_changes_query_uses_two_commands_and_forwards_environment(self):
        left, right = self.changes_fixture()
        environment = {"RELEASE_REVIEWER_TEST": "forwarded"}
        with mock.patch.object(git, "run_git", wraps=git.run_git) as commands:
            git.collect_changes(self.repo, left, right, env=environment)
        self.assertEqual(commands.call_count, 2)
        first, second = [call.args[1] for call in commands.call_args_list]
        self.assertIn("--diff-algorithm=histogram", first)
        self.assertNotIn("--diff-algorithm=histogram", second)
        self.assertIn("--raw", second)
        self.assertIn("--numstat", second)
        for call in commands.call_args_list:
            self.assertIs(call.kwargs["env"], environment)
            self.assertIn("--no-ext-diff", call.args[1])
            self.assertIn("--no-textconv", call.args[1])

    def test_empty_and_reverse_changes_match_legacy(self):
        left, right = self.changes_fixture()
        self.assertEqual(git.collect_changes(self.repo, left, left), [])
        self.assertEqual(git.collect_changes(self.repo, right, left), legacy_changes(self.repo, right, left))

    def test_incomplete_combined_diff_is_rejected(self):
        for output in (":100644\0name\0", "invalid numstat\0"):
            with self.subTest(output=output), mock.patch.object(git, "run_git", side_effect=["M\0name\0", output]):
                with self.assertRaises(git.ReviewContextError):
                    git.collect_changes(self.repo, "left", "right")

    def test_merge_metadata_matches_legacy_and_preserves_every_parent(self):
        empty = self.tree({})
        root = self.commit(empty, subject="root")
        tree_a = self.tree({"a.txt": ("100644", self.blob(b"A\n"))})
        tree_b = self.tree({"b.txt": ("100644", self.blob(b"B\n"))})
        tree_c = self.tree({"c.txt": ("100644", self.blob(b"C\n"))})
        side_a = self.commit(tree_a, [root], "side A")
        side_b = self.commit(tree_b, [root], "side B")
        side_c = self.commit(tree_c, [root], "side C")
        first = self.commit(tree_a, [root, side_a], "合併第一條線\n\nbody ignored")
        last = self.commit(tree_c, [first, side_b, side_c], "合併\t所有 parents")
        expected = legacy_merges(self.repo, root, last)
        with mock.patch.object(git, "run_git", wraps=git.run_git) as commands:
            actual = git.merge_commit_records(self.repo, root, last)
        self.assertEqual(actual, expected)
        self.assertEqual([item["sha"] for item in actual], [first, last])
        self.assertEqual(actual[0]["parents"], [root, side_a])
        self.assertEqual(actual[1]["parents"], [first, side_b, side_c])
        for item in actual:
            self.assertEqual([view["sha"] for view in item["parent_views"]], item["parents"])
            self.assertEqual([view["position"] for view in item["parent_views"]], list(range(1, len(item["parents"]) + 1)))
        self.assertEqual(commands.call_count, 1 + 3 * 5)
        self.assertEqual(sum(call.args[1][0] == "log" for call in commands.call_args_list), 1)
        self.assertFalse(any(call.args[1][0] in {"show", "rev-list"} for call in commands.call_args_list))

    def test_no_merge_commits_still_uses_one_metadata_query(self):
        empty = self.tree({})
        left = self.commit(empty, subject="root")
        right = self.commit(empty, [left], "ordinary commit")
        with mock.patch.object(git, "run_git", wraps=git.run_git) as commands:
            self.assertEqual(git.merge_commit_records(self.repo, left, right), [])
        self.assertEqual(commands.call_count, 1)

    def test_incomplete_merge_metadata_is_rejected(self):
        with mock.patch.object(git, "run_git", return_value="sha\0parents\0"):
            with self.assertRaises(git.ReviewContextError):
                git.merge_commit_records(self.repo, "left", "right")


if __name__ == "__main__":
    unittest.main()
