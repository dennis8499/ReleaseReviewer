import copy
from unittest.mock import patch

from support import *


def expand(result):
    if "contents" not in result:
        return result
    normal = {"results": copy.deepcopy(result["results"]), "complete": result["complete"]}
    for item in normal["results"]:
        if item["ok"] and "content_id" in item["result"]:
            identifier = item["result"].pop("content_id")
            item["result"].update(result["contents"][identifier])
    return normal


class AutoContentTests(RepoTest):
    def test_exact_utf8_size_selects_both_formats_without_changing_full_results(self):
        write(self.repo, "src/large.txt", "繁體中文完整證據。" * 1000 + "\n")
        write(self.repo, "src/tiny.txt", "短\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "size selection fixtures")
        context, manifest = self.capture()
        cases = [([], False),
                 ([{"action": "read", "source": "head", "path": "src/tiny.txt"}], False),
                 ([{"action": "read", "source": "head", "path": "src/large.txt"}] * 3, True),
                 ([{"action": "search", "query": "InvoiceTax"},
                   {"action": "list"}, None], False)]
        for requests, choose_shared in cases:
            with self.subTest(requests=requests):
                normal = evidence.batch(manifest, context, requests)
                shared = evidence.batch(manifest, context, requests, shared_content=True)
                auto = evidence.batch(manifest, context, requests, auto_content=True)
                self.assertEqual(normal, expand(auto))
                sizes = [len(evidence.compact_json(item).encode("utf-8")) for item in (normal, shared)]
                self.assertEqual(min(sizes), len(evidence.compact_json(auto).encode("utf-8")))
                self.assertEqual(choose_shared, "contents" in auto)

    def test_requests_execute_once_and_bodies_decode_once_with_same_git_calls(self):
        context, manifest = self.capture()
        requests = [{"action": "read", "source": source, "path": "src/service.py"}
                    for source in ("head", "merge-preview", "head")] + [
                        {"action": "search", "query": "InvoiceTax"},
                        {"action": "search", "query": "InvoiceTax"}]
        with patch.object(evidence, "command", wraps=evidence.command) as normal_git:
            normal = evidence.batch(manifest, context, requests)
        with patch.object(evidence, "command", wraps=evidence.command) as auto_git, \
                patch.object(evidence, "request_result", wraps=evidence.request_result) as execute, \
                patch.object(evidence, "text_lines", wraps=evidence.text_lines) as decode:
            auto = evidence.batch(manifest, context, requests, auto_content=True)
        self.assertEqual(normal, expand(auto))
        self.assertEqual(len(requests), execute.call_count)
        self.assertEqual(1, decode.call_count)
        self.assertEqual(normal_git.call_args_list, auto_git.call_args_list)

    def test_normal_expansion_reuses_search_trees_and_body_lines(self):
        tree = {"matches": [{"path": "many.txt", "line": n, "text": "hit"} for n in range(1000)]}
        lines = [{"line": 1, "text": "read body"}]
        shared = {"schema": "ReleaseReviewEvidenceBatch/v1", "complete": True,
                  "contents": {"body": {"lines": lines}},
                  "results": [{"index": 0, "ok": True, "result": tree},
                              {"index": 1, "ok": True, "result": {"content_id": "body", "path": "a"}}]}
        normal = evidence._normal_batch(shared)
        self.assertIs(tree, normal["results"][0]["result"])
        self.assertIs(lines, normal["results"][1]["result"]["lines"])
        self.assertIn("content_id", shared["results"][1]["result"])

    def test_equal_wire_sizes_prefer_normal_and_conflicting_api_flags_fail(self):
        context, manifest = self.capture()
        with patch.object(evidence, "compact_json", return_value="equal encoded size"):
            result = evidence.batch(manifest, context, [], auto_content=True)
        self.assertEqual({"results": [], "complete": True}, result)
        with self.assertRaisesRegex(ValueError, "不可同時"):
            evidence.batch(manifest, context, [], shared_content=True, auto_content=True)

    def test_ledger_ids_and_bytes_stay_canonical_and_tampering_is_rejected(self):
        context, manifest = self.capture()
        requests = [{"action": "search", "query": "InvoiceTax"},
                    {"action": "search", "query": "MISSING_AUTO_894"}]
        normal = evidence.batch(manifest, context, requests)
        before = {p.name: p.read_bytes() for p in (context / "searches").glob("*.json")}
        auto = evidence.batch(manifest, context, requests, auto_content=True)
        self.assertEqual(normal, expand(auto))
        self.assertEqual(before, {p.name: p.read_bytes() for p in (context / "searches").glob("*.json")})
        identifier = auto["results"][0]["result"]["search_id"]
        evidence.validate_baseline_search(context, manifest, identifier)
        path = context / "searches" / f"{identifier}.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        record["matches"] = []
        path.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "摘要不符"):
            evidence.validate_baseline_search(context, manifest, identifier)

    def test_cli_preserves_mixed_errors_order_and_exit_and_rejects_invalid_modes(self):
        context, _ = self.capture()
        requests = [{"action": "read", "source": "head", "path": "src/service.py"},
                    None, {"action": "read", "source": "head", "ref": "0" * 40, "path": "src/service.py"},
                    {"action": "read", "source": "supporting", "repo": "entry", "path": "src/service.py"},
                    {"action": "read", "source": "base", "path": "../outside"},
                    {"action": "search", "query": "MISSING_AUTO_881"}]
        path = context / "requests.json"
        path.write_text(json.dumps(requests), encoding="utf-8-sig")
        normal = python(SCRIPTS / "evidence.py", "batch", "--context-dir", context, "--requests", path)
        auto = python(SCRIPTS / "evidence.py", "batch", "--context-dir", context,
                      "--requests", path, "--auto-content")
        self.assertEqual((2, 2), (normal.returncode, auto.returncode))
        self.assertEqual("", auto.stderr)
        self.assertEqual(json.loads(normal.stdout), expand(json.loads(auto.stdout)))
        for extra in (("--auto-content", "--shared-content"),):
            result = python(SCRIPTS / "evidence.py", "batch", "--context-dir", context,
                            "--requests", path, *extra)
            self.assertEqual(2, result.returncode)
            self.assertEqual("", result.stdout)
        result = python(SCRIPTS / "evidence.py", "list", "--context-dir", context, "--auto-content")
        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stdout)


if __name__ == "__main__":
    unittest.main()
