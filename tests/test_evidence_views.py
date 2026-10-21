"""Bounded reads and search selections preserve Release evidence and batch errors."""
import contextlib
import copy
import hashlib
import io
from unittest.mock import patch

from support import *
import review_runtime


class EvidenceViewTests(RepoTest):
    def setUp(self):
        super().setUp()
        self.text = "contract = 'new'\nsecond = '值'\ncontract = 'third'\nlast = True\n"
        write(self.repo, "range.txt", self.text)
        write(self.repo, "empty.txt", "")
        write(self.repo, "binary.dat", b"binary\0data")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "bounded evidence fixtures")
        self.context, self.manifest = self.capture()

    def cli(self, *arguments):
        return python(SCRIPTS / "evidence.py", *arguments, "--context-dir", self.context)

    def request(self, **fields):
        return {"action": "read", "source": "head", "path": "range.txt", **fields}

    def expand(self, value):
        return evidence._normal_batch(value) if "contents" in value else value

    def saved_searches(self):
        request = {"action": "search", "source": "head", "query": "contract"}
        other = {**request, "query": "second"}
        value = evidence.batch(self.manifest, self.context,
            [self.request(), request, None, request, other], shared_content=True)
        identifier = review_runtime.store_view(self.context, value)
        return value, identifier

    def test_bounded_read_keeps_full_hash_fixed_ref_and_original_line_numbers(self):
        full = evidence.request_result(self.manifest, self.context, self.request())
        bounded = evidence.request_result(self.manifest, self.context, self.request(line_start=2, line_end=3))
        self.assertEqual(full["lines"][1:3], bounded["lines"])
        self.assertEqual([2, 3], [line["line"] for line in bounded["lines"]])
        for key in ("repo", "source", "ref", "path"):
            self.assertEqual(full[key], bounded[key])
        self.assertEqual(hashlib.sha256(self.text.encode("utf-8")).hexdigest(), bounded["content_sha256"])
        self.assertEqual((2, 3, 4), (bounded["line_start"], bounded["line_end"], bounded["total_lines"]))
        self.assertEqual({"repo", "source", "ref", "path", "lines"}, set(full))

    def test_mixed_full_and_bounded_reads_expand_equally_without_sharing_slices(self):
        requests = [self.request(), self.request(line_start=1, line_end=2),
                    self.request(line_start=3, line_end=4), self.request()]
        normal = evidence.batch(self.manifest, self.context, requests)
        shared = evidence.batch(self.manifest, self.context, requests, shared_content=True)
        auto = evidence.batch(self.manifest, self.context, requests, auto_content=True)
        self.assertEqual(normal, self.expand(shared))
        self.assertEqual(normal, self.expand(auto))
        self.assertEqual(1, len(shared["contents"]))
        for index in (1, 2):
            self.assertIn("lines", shared["results"][index]["result"])
            self.assertNotIn("content_id", shared["results"][index]["result"])
        self.assertNotEqual(shared["results"][1]["result"]["lines"], shared["results"][2]["result"]["lines"])

    def test_invalid_ranges_only_fail_their_items_and_later_reads_continue(self):
        invalid = [self.request(line_start=start, line_end=end) for start, end in
                   ((None, None), (1, None), (None, 1), (0, 1), (-1, 1), (3, 2),
                    (True, 2), (1, False), (1.0, 2), ("1", 2), (1, 5))]
        invalid += [self.request(line_start=1), self.request(line_end=1),
                    self.request(path="empty.txt", line_start=1, line_end=1),
                    self.request(path="binary.dat", line_start=1, line_end=1),
                    {"action": "search", "query": "contract", "line_start": 1, "line_end": 1}]
        requests = invalid + [self.request(line_start=4, line_end=4)]
        for flags in ({}, {"shared_content": True}, {"auto_content": True}):
            with self.subTest(flags=flags):
                value = evidence.batch(self.manifest, self.context, requests, **flags)
                self.assertFalse(value["complete"])
                self.assertEqual([False] * len(invalid) + [True], [row["ok"] for row in value["results"]])
                self.assertEqual([{"line": 4, "text": "last = True"}], value["results"][-1]["result"]["lines"])
        self.assertEqual([], evidence.request_result(self.manifest, self.context, self.request(path="empty.txt"))["lines"])

    def test_cli_read_ranges_and_batch_preserve_error_exit_and_default_shape(self):
        result = self.cli("read", "--source", "head", "--path", "range.txt", "--line-start", "2", "--line-end", "3")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(evidence.request_result(self.manifest, self.context, self.request(line_start=2, line_end=3)),
                         json.loads(result.stdout))
        invalid = self.cli("read", "--source", "head", "--path", "range.txt", "--line-start", "2")
        self.assertEqual(2, invalid.returncode)
        self.assertEqual("", invalid.stdout)
        self.assertIn("成對", invalid.stderr)
        requests = [self.request(line_start=1), self.request(line_start=1, line_end=1)]
        result = self.cli("batch", "--requests", json.dumps(requests), "--auto-content")
        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stderr)
        self.assertEqual([False, True], [row["ok"] for row in json.loads(result.stdout)["results"]])
        result = self.cli("read", "--source", "head", "--path", "range.txt")
        self.assertEqual(evidence.request_result(self.manifest, self.context, self.request()), json.loads(result.stdout))
        for flags in (("list", "--line-start", "1", "--line-end", "2"),
                      ("batch", "--line-start", "1", "--line-end", "2"),
                      ("search", "--query", "contract", "--search-id", "a" * 64)):
            result = self.cli(*flags)
            self.assertEqual(2, result.returncode)
            self.assertEqual("", result.stdout)

    def test_selection_keeps_duplicate_hash_indexes_failures_and_original_complete(self):
        value, artifact = self.saved_searches()
        original = copy.deepcopy(value)
        first = value["results"][1]["result"]["search_id"]
        last = value["results"][4]["result"]["search_id"]
        selected = review_runtime.select_searches(value, [first, first])
        self.assertEqual([1, 2, 3], [row["index"] for row in selected["results"]])
        self.assertFalse(selected["complete"])
        self.assertEqual({}, selected["contents"])
        self.assertEqual(value["results"][2], selected["results"][1])
        selected = review_runtime.select_searches(value, [last, first])
        self.assertEqual([1, 2, 3, 4], [row["index"] for row in selected["results"]])
        self.assertEqual(original, value)
        self.assertEqual(original, review_runtime.load_view(self.context, artifact))

    def test_filtered_view_pages_original_artifact_without_searching_or_rewriting(self):
        value, artifact = self.saved_searches()
        identifier = value["results"][1]["result"]["search_id"]
        original = (self.context / "views" / (artifact + ".json")).read_bytes()
        ledger = {path.name: path.read_bytes() for path in (self.context / "searches").glob("*.json")}
        manifest = (self.context / "manifest.json").read_bytes()
        out, err = io.StringIO(), io.StringIO()
        arguments = ["view", "--context-dir", str(self.context), "--artifact", artifact,
                     "--search-id", identifier, "--page-size", "1", "--offset", "1"]
        with patch.object(evidence, "search", side_effect=AssertionError("must use saved search")), \
                patch.object(evidence, "command", side_effect=AssertionError("must use saved artifact")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = evidence.main(arguments)
        self.assertEqual((0, ""), (status, err.getvalue()))
        result = json.loads(out.getvalue())
        self.assertEqual(artifact, result["artifact"])
        self.assertEqual([1, 2, 3], [row["index"] for row in result["result"]["results"]])
        self.assertFalse(result["result"]["complete"])
        for row in result["result"]["results"]:
            if row["ok"]:
                self.assertEqual(value["results"][1]["result"]["matches"][1:2], row["result"]["matches"])
        self.assertEqual(original, (self.context / "views" / (artifact + ".json")).read_bytes())
        self.assertEqual(ledger, {path.name: path.read_bytes() for path in (self.context / "searches").glob("*.json")})
        self.assertEqual(manifest, (self.context / "manifest.json").read_bytes())

    def test_single_search_artifact_unknown_ids_and_nonsearch_artifacts(self):
        value, artifact = self.saved_searches()
        single = value["results"][4]["result"]
        identifier = review_runtime.store_view(self.context, single)
        result = self.cli("view", "--artifact", identifier, "--search-id", single["search_id"], "--page-size", "1")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(single["search_id"], json.loads(result.stdout)["result"]["search_id"])
        invalid = self.cli("view", "--artifact", artifact, "--search-id", "f" * 64, "--page-size", "1")
        self.assertEqual(2, invalid.returncode)
        self.assertIn("Unknown search ID", invalid.stderr)
        for ids in (["not-a-hash"], ["a" * 63], ["A" * 64]):
            with self.subTest(ids=ids), self.assertRaisesRegex(ValueError, "64-hex"):
                review_runtime.select_searches(value, ids)
        for payload in ({"results": [{"index": 0, "ok": False, "error": "failed"}], "complete": False},
                        {"results": [{"index": 0, "ok": True, "result": {"lines": []}}], "complete": True},
                        {"lines": []}):
            with self.subTest(payload=payload), self.assertRaisesRegex(ValueError, "artifact"):
                review_runtime.select_searches(payload, ["a" * 64])

    def test_unfiltered_view_remains_compatible_and_checksum_cannot_be_bypassed(self):
        value, artifact = self.saved_searches()
        result = self.cli("view", "--artifact", artifact, "--page-size", "100")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(review_runtime.page(value, self.context, size=100, artifact=artifact), json.loads(result.stdout))
        (self.context / "views" / (artifact + ".json")).write_text("{}", encoding="utf-8")
        invalid = self.cli("view", "--artifact", artifact, "--search-id", value["results"][1]["result"]["search_id"],
                           "--page-size", "1")
        self.assertEqual(2, invalid.returncode)
        self.assertIn("checksum mismatch", invalid.stderr)
