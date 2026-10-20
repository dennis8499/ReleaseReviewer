import contextlib
import copy
import hashlib
import io
from unittest import mock

from support import *


class EvidencePerformanceTests(RepoTest):
    def run_cli(self, *args):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            status = evidence.main(list(args))
        return status, output.getvalue(), errors.getvalue()

    def test_batch_preserves_single_results_order_ids_and_zero_matches(self):
        context, manifest = self.capture()
        requests = [
            {"action": "list"},
            {"action": "read", "path": "src/service.py"},
            {"action": "read", "source": "merge-preview", "path": "src/service.py"},
            {"action": "search", "query": "COREDB", "ignore_case": True},
            {"action": "search", "query": "MISSING_TOKEN_95815"},
            {"action": "search", "query": "COREDB", "ignore_case": True},
        ]
        expected = [evidence.request_result(manifest, context, request) for request in requests]
        requests_path = context / "requests.json"
        requests_path.write_text(json.dumps(requests), encoding="utf-8")
        status, raw, errors = self.run_cli("batch", "--context-dir", str(context),
                                           "--requests", str(requests_path))
        self.assertEqual((status, errors), (0, ""))
        self.assertEqual(len(raw.splitlines()), 1)
        result = json.loads(raw)
        self.assertTrue(result["complete"])
        self.assertEqual([item["index"] for item in result["results"]], list(range(len(requests))))
        self.assertTrue(all(item["ok"] for item in result["results"]))
        self.assertEqual([item["result"] for item in result["results"]], expected)
        self.assertEqual(expected[4]["matches"], [])
        self.assertIn("src/existing.py", {match["path"] for match in expected[3]["matches"]})
        for item in expected[3:]:
            record = evidence.validate_search(context, manifest, item["search_id"])
            self.assertEqual(record, {key: value for key, value in item.items() if key != "search_id"})

    def test_batch_exposes_every_error_and_continues(self):
        context, _ = self.capture()
        requests = [
            {"action": "read", "path": "../escape"},
            {"action": "read", "path": "src/service.py"},
            {"action": "search", "query": ""},
            {"action": "search", "query": "missing"},
            {"action": "read", "path": "src/service.py", "ref": "0" * 40},
            None,
            {"action": "list", "typo": True},
        ]
        status, raw, errors = self.run_cli("batch", "--context-dir", str(context),
                                           "--requests", json.dumps(requests))
        self.assertEqual((status, errors), (2, ""))
        result = json.loads(raw)
        self.assertFalse(result["complete"])
        self.assertEqual([item["ok"] for item in result["results"]],
                         [False, True, False, True, False, False, False])
        self.assertTrue(all(item["error"] for item in result["results"] if not item["ok"]))

    def test_requests_accept_worklist_and_reject_invalid_top_level(self):
        context, manifest = self.capture()
        path = context / "worklist.json"
        requests = [{"action": "read", "path": "src/service.py"}]
        path.write_text(json.dumps({"schema": "ReleaseReviewWorklist/v1", "requests": requests,
                                    "metadata_only": [{"path": "binary.dat"}]}), encoding="utf-8")
        self.assertEqual(evidence.load_requests(str(path)), requests)
        for raw in ("null", '"text"', '{"schema":"other","requests":[]}',
                    '{"schema":"ReleaseReviewWorklist/v1","requests":{}}'):
            with self.subTest(raw=raw), self.assertRaises((OSError, ValueError)):
                evidence.load_requests(raw)
        self.assertEqual(evidence.batch(manifest, context, []), {"results": [], "complete": True})

    def test_preview_cli_resolves_source_once(self):
        context, _ = self.capture()
        with mock.patch.object(evidence, "command", wraps=evidence.command) as command:
            status, raw, errors = self.run_cli("read", "--context-dir", str(context),
                                               "--source", "merge-preview", "--path", "src/service.py")
        self.assertEqual((status, errors), (0, ""))
        self.assertIn("lines", json.loads(raw))
        self.assertEqual(len(raw.splitlines()) > 1, True)
        self.assertEqual([call.args[1][0] for call in command.call_args_list], ["cat-file"])

    def test_batch_shares_blobs_source_resolution_and_searches(self):
        context, manifest = self.capture()
        requests = [{"action": "read", "source": "merge-preview", "path": "src/service.py"}] * 3
        requests += [{"action": "search", "source": "merge-preview", "query": "def "}] * 3
        with mock.patch.object(evidence, "command", wraps=evidence.command) as command:
            result = evidence.batch(manifest, context, requests)
        self.assertTrue(result["complete"])
        self.assertEqual([call.args[1][0] for call in command.call_args_list],
                         ["cat-file", "grep"])
        self.assertEqual(result["results"][3]["result"], result["results"][5]["result"])

    def test_cache_does_not_bypass_source_ref_repo_path_or_manifest_checks(self):
        context, manifest = self.capture()
        path = "src/service.py"
        with evidence.operation_cache(manifest, context):
            original = evidence.read_blob(manifest, context, "base", path)
            cases = [
                ("base", path, manifest["head_sha"], "entry"),
                ("supporting", path, manifest["base_sha"], "entry"),
                ("base", "../src/service.py", manifest["base_sha"], "entry"),
                ("base", path, manifest["base_sha"], "missing"),
                ("unknown", path, manifest["base_sha"], "entry"),
            ]
            for source, item_path, ref, repo in cases:
                with self.subTest(source=source, path=item_path, repo=repo), self.assertRaises(ValueError):
                    evidence.blob_metadata(manifest, context, source, item_path, ref, repo)
            manifest["base_sha"] = manifest["head_sha"]
            with self.assertRaises(ValueError):
                evidence.read_blob(manifest, context, "base", path, git(self.repo, "rev-parse", "main"))
            self.assertNotEqual(evidence.read_blob(manifest, context, "base", path), original)

    def test_cached_preview_still_validates_object_directory_and_status(self):
        context, manifest = self.capture()
        with evidence.operation_cache(manifest, context):
            evidence.read_blob(manifest, context, "merge-preview", "src/service.py")
            manifest["merge_preview"]["status"] = "unavailable"
            with self.assertRaises(ValueError):
                evidence.read_blob(manifest, context, "merge-preview", "src/service.py")
            manifest["merge_preview"]["status"] = "clean"
            manifest["merge_preview"]["object_directory"] = "../objects"
            with self.assertRaises(ValueError):
                evidence.read_blob(manifest, context, "merge-preview", "src/service.py")

    def test_metadata_is_shared_but_cache_does_not_cross_operations(self):
        context, manifest = self.capture()
        path = "src/service.py"
        with mock.patch.object(evidence, "command", wraps=evidence.command) as command:
            with evidence.operation_cache(manifest, context):
                first = evidence.blob_metadata(manifest, context, "base", path)
                first["line_count"] = 0
                second = evidence.blob_metadata(manifest, context, "base", path)
                self.assertEqual(second["line_count"], 4)
                self.assertFalse(second["binary"])
                self.assertEqual(second["content_sha256"], hashlib.sha256(second["content"]).hexdigest())
                self.assertEqual(command.call_count, 1)
                evidence.read_blob(copy.deepcopy(manifest), context, "base", path)
                self.assertEqual(command.call_count, 2)
                with evidence.operation_cache(manifest, context):
                    evidence.read_blob(manifest, context, "base", path)
                self.assertEqual(command.call_count, 3)
                evidence.read_blob(manifest, context, "base", path)
                self.assertEqual(command.call_count, 3)
            with evidence.operation_cache(manifest, context):
                evidence.read_blob(manifest, context, "base", path)
            evidence.read_blob(manifest, context, "base", path)
            self.assertEqual(command.call_count, 5)

    def test_cache_is_reset_after_exception_and_cached_results_are_not_mutable(self):
        context, manifest = self.capture()
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            with evidence.operation_cache(manifest, context):
                result = evidence.search(manifest, context, "base", "return")
                expected = copy.deepcopy(result)
                result["search_id"] = "bad"
                result["matches"][0]["path"] = "tampered"
                self.assertEqual(evidence.search(manifest, context, "base", "return"), expected)
                raise RuntimeError("cancelled")
        with mock.patch.object(evidence, "command", wraps=evidence.command) as command:
            evidence.search(manifest, context, "base", "return")
            self.assertEqual(command.call_count, 1)

    def test_cached_search_does_not_hide_record_tampering(self):
        context, manifest = self.capture()
        with evidence.operation_cache(manifest, context):
            record = evidence.search(manifest, context, "base", "return")
            identifier = evidence.record_search(context, record)
            evidence.validate_search(context, manifest, identifier)
            record["matches"] = []
            (context / "searches" / f"{identifier}.json").write_text(json.dumps(record), encoding="utf-8")
            with self.assertRaises(ValueError):
                evidence.validate_search(context, manifest, identifier)

    def test_dense_grep_keeps_all_matches_unicode_and_unterminated_last_line(self):
        context, manifest = self.capture()
        ref = manifest["base_sha"]
        filename, text = "資料/項目\nname.txt", "文字 café"
        count = 20000
        raw = b"\n".join((f"{ref}:{filename}\0{number}\0{text} {number}").encode("utf-8")
                           for number in range(1, count + 1))
        with mock.patch.object(evidence, "command", return_value=raw):
            result = evidence.search(manifest, context, "base", "文字")
        self.assertEqual(len(result["matches"]), count)
        self.assertEqual(result["matches"][0], {"path": filename, "line": 1, "text": text + " 1"})
        self.assertEqual(result["matches"][-1], {"path": filename, "line": count,
                                               "text": text + f" {count}"})
        identifier = evidence.record_search(context, result)
        stored = json.loads((context / "searches" / f"{identifier}.json").read_text(encoding="utf-8"))
        self.assertEqual(stored, result)
        self.assertEqual(hashlib.sha256(evidence.canonical(stored)).hexdigest(), identifier)

    def test_malformed_grep_is_an_explicit_error(self):
        context, manifest = self.capture()
        prefix = manifest["base_sha"].encode() + b":file.txt"
        for raw in (b"broken", prefix + b"\0", prefix + b"\0bad\0text\n",
                    prefix + b"\0\0text", b"wrong:file\0" + b"1\0text\n",
                    prefix + b"\0" + b"1\0text\ntrailing garbage"):
            with self.subTest(raw=raw), mock.patch.object(evidence, "command", return_value=raw):
                with self.assertRaises(ValueError):
                    evidence.search(manifest, context, "base", "text")


if __name__ == "__main__":
    unittest.main()
