import copy
import hashlib
from unittest.mock import patch

from support import *


class SharedContentTests(RepoTest):
    def expand(self, result):
        expanded = {"results": copy.deepcopy(result["results"]), "complete": result["complete"]}
        for item in expanded["results"]:
            if item["ok"] and "content_id" in item["result"]:
                content_id = item["result"].pop("content_id")
                item["result"].update(copy.deepcopy(result["contents"][content_id]))
        return expanded

    def test_shared_wrapper_expands_to_legacy_results_and_decodes_equal_bodies_once(self):
        context, manifest = self.capture()
        requests = [
            {"action": "read", "source": "head", "path": "src/service.py"},
            {"action": "read", "source": "merge-preview", "path": "src/service.py"},
            {"action": "read", "source": "head", "path": "src/service.py"},
            {"action": "list", "source": "base"},
            {"action": "search", "source": "base", "query": "MISSING_SHARED_8594"},
            {"action": "read", "source": "base", "path": "src/service.py"},
        ]
        expected = evidence.batch(manifest, context, requests)
        with patch.object(evidence, "text_lines", wraps=evidence.text_lines) as decode:
            result = evidence.batch(manifest, context, requests, shared_content=True)
        self.assertEqual("ReleaseReviewEvidenceBatch/v1", result["schema"])
        self.assertEqual(expected, self.expand(result))
        self.assertEqual(2, len(result["contents"]))
        self.assertEqual(2, decode.call_count)
        content_id = hashlib.sha256(HEAD_FILES["src/service.py"].encode()).hexdigest()
        self.assertEqual([content_id] * 3, [item["result"]["content_id"] for item in result["results"][:3]])
        self.assertEqual(["head", "merge-preview", "head"],
                         [item["result"]["source"] for item in result["results"][:3]])
        self.assertEqual({"results", "complete"}, set(expected))

    def test_each_repo_source_ref_and_path_is_checked_before_content_reuse(self):
        peer = init_repo(self.root, "same-content-peer")
        write(peer, "src/service.py", HEAD_FILES["src/service.py"])
        git(peer, "add", ".")
        git(peer, "commit", "-m", "same content")
        context, manifest = self.capture()
        manifest["repositories"].append({"id": "repo-001", "path": str(peer), "role": "supporting",
                                          "status": "captured", "ref": git(peer, "rev-parse", "main")})
        requests = [
            {"action": "read", "source": "head", "path": "src/service.py"},
            {"action": "read", "source": "supporting", "repo": "repo-001", "path": "src/service.py"},
            {"action": "read", "source": "head", "repo": "repo-001", "path": "src/service.py"},
            {"action": "read", "source": "supporting", "repo": "entry", "path": "src/service.py"},
            {"action": "read", "source": "head", "repo": "missing", "path": "src/service.py"},
            {"action": "read", "source": "head", "ref": "0" * 40, "path": "src/service.py"},
            {"action": "read", "source": "head", "path": "../src/service.py"},
            {"action": "read", "source": "head", "path": "src/missing.py"},
            {"action": "read", "source": "merge-preview", "path": "src/service.py"},
        ]
        before = sorted(str(p.relative_to(context)) for p in context.rglob("*") if p.is_file())
        legacy = evidence.batch(manifest, context, requests)
        result = evidence.batch(manifest, context, requests, shared_content=True)
        self.assertEqual(legacy, self.expand(result))
        self.assertEqual([True, True, False, False, False, False, False, False, True],
                         [item["ok"] for item in result["results"]])
        self.assertFalse(result["complete"])
        self.assertEqual(1, len(result["contents"]))
        self.assertEqual(before, sorted(str(p.relative_to(context)) for p in context.rglob("*") if p.is_file()))

    def test_content_identity_uses_raw_bytes_not_replacement_decoding(self):
        write(self.repo, "src/invalid-one.txt", b"\xff\n")
        write(self.repo, "src/invalid-two.txt", b"\xfe\n")
        write(self.repo, "src/binary.dat", b"binary\0data")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "raw identity fixtures")
        context, manifest = self.capture()
        requests = [{"action": "read", "source": "head", "path": path} for path in
                    ("src/invalid-one.txt", "src/invalid-two.txt", "src/binary.dat")]
        result = evidence.batch(manifest, context, requests, shared_content=True)
        first, second, binary = result["results"]
        self.assertNotEqual(first["result"]["content_id"], second["result"]["content_id"])
        self.assertEqual(result["contents"][first["result"]["content_id"]],
                         result["contents"][second["result"]["content_id"]])
        self.assertFalse(binary["ok"])
        self.assertFalse(result["complete"])
        self.assertEqual(evidence.batch(manifest, context, requests), self.expand(result))

    def test_cli_opt_in_preserves_errors_order_exit_and_default_shape(self):
        context, _ = self.capture()
        requests = [{"action": "read", "source": "head", "path": "src/service.py"},
                    None, {"action": "search", "query": ""},
                    {"action": "search", "query": "MISSING_SHARED_191"}]
        path = context / "requests.json"
        path.write_text(json.dumps(requests), encoding="utf-8-sig")
        legacy = python(SCRIPTS / "evidence.py", "batch", "--context-dir", context, "--requests", path)
        shared = python(SCRIPTS / "evidence.py", "batch", "--context-dir", context,
                        "--requests", path, "--shared-content")
        self.assertEqual((2, 2), (legacy.returncode, shared.returncode))
        self.assertEqual("", shared.stderr)
        self.assertEqual(json.loads(legacy.stdout), self.expand(json.loads(shared.stdout)))
        self.assertEqual({"results", "complete"}, set(json.loads(legacy.stdout)))
        rejected = python(SCRIPTS / "evidence.py", "read", "--context-dir", context,
                          "--path", "src/service.py", "--shared-content")
        self.assertEqual(2, rejected.returncode)
        self.assertEqual("", rejected.stdout)
        self.assertIn("--shared-content", rejected.stderr)
        self.assertEqual({"schema": "ReleaseReviewEvidenceBatch/v1", "contents": {},
                          "results": [], "complete": True},
                         evidence.batch(evidence.load_manifest(context), context, [], shared_content=True))


if __name__ == "__main__":
    unittest.main()
