import copy
import json
from unittest.mock import patch

from support import *
import frozen_evidence
import review_coordinator as coordination
import review_flow as flow
import review_runtime


class SessionOptimizationTests(RepoTest):
    def start(self):
        args = flow.parser().parse_args(["start", "--workspace", str(self.root), "--project", str(self.repo),
                                         "--base", "main", "--head", "feature"])
        return flow.start(args)

    def test_read_batch_deduplicates_blob_and_preserves_failed_prefix(self):
        started = self.start()
        root, identifier = Path(started["runtime_root"]), started["request_id"]
        operations = [{"id": "one", "op": "read", "source": "head", "path": "src/service.py"},
                      {"id": "two", "op": "read", "source": "merge-preview", "path": "src/service.py"},
                      {"id": "bad", "op": "read", "path": "missing.py"},
                      {"id": "later", "op": "read", "path": "src/existing.py"}]
        with patch.object(evidence, "command", wraps=evidence.command) as command:
            result = flow.step(root, identifier, operations)
        self.assertFalse(result["complete"])
        self.assertEqual(2, result["next_index"])
        self.assertEqual(["one", "two"], [item["id"] for item in result["operations"]])
        batches = [c for c in command.call_args_list if c.args[1] == ["cat-file", "--batch"]]
        self.assertEqual(1, len(batches))
        self.assertEqual(1, len(batches[0].kwargs["input_bytes"].splitlines()))
        operations[2]["path"] = "src/existing.py"
        resumed = flow.step(root, identifier, operations)
        self.assertTrue(resumed["complete"])
        self.assertTrue(all(item["reused"] for item in resumed["operations"][:2]))
        flow.cancel(root, identifier)

    def test_conflicting_duplicate_id_is_excluded_from_the_prefetch_prefix(self):
        started = self.start()
        root, identifier = Path(started["runtime_root"]), started["request_id"]
        operations = [{"id": "same", "op": "read", "path": "src/service.py"},
                      {"id": "same", "op": "read", "path": "src/existing.py"},
                      {"id": "later", "op": "read", "path": "src/settings.json"}]
        with patch.object(evidence, "_prefetch_reads", wraps=evidence._prefetch_reads) as prefetch:
            result = flow.step(root, identifier, operations)
        self.assertFalse(result["complete"])
        self.assertEqual(1, result["next_index"])
        self.assertEqual(["src/service.py"], [request["path"] for request in prefetch.call_args.args[2]])
        flow.cancel(root, identifier)

    def test_interleaved_repositories_use_one_batch_each_and_keep_order(self):
        peer = init_repo(self.root, "consumer")
        write(peer, "peer.py", "consumer = True\n")
        write(peer, "other.py", "other = True\n")
        git(peer, "add", ".")
        git(peer, "commit", "-m", "peer files")
        started = self.start()
        root, identifier = Path(started["runtime_root"]), started["request_id"]
        peer_id = next(row["id"] for row in started["repositories"] if row["role"] == "supporting")
        operations = [{"id": "entry-head", "op": "read", "source": "head", "path": "src/service.py"},
                      {"id": "peer-first", "op": "read", "repo": peer_id, "source": "supporting", "path": "peer.py"},
                      {"id": "entry-final", "op": "read", "source": "merge-preview", "path": "src/service.py"},
                      {"id": "peer-second", "op": "read", "repo": peer_id, "source": "supporting", "path": "other.py"}]
        with patch.object(evidence, "command", wraps=evidence.command) as commands:
            result = flow.step(root, identifier, operations)
        self.assertTrue(result["complete"])
        self.assertEqual([row["id"] for row in operations], [row["id"] for row in result["operations"]])
        self.assertEqual(2, sum(call.args[1] == ["cat-file", "--batch"] for call in commands.call_args_list))
        flow.cancel(root, identifier)

    def test_invalid_interleaved_read_stops_prefetch_before_later_repos(self):
        peer = init_repo(self.root, "consumer")
        write(peer, "peer.py", "consumer = True\n")
        git(peer, "add", ".")
        git(peer, "commit", "-m", "peer files")
        started = self.start()
        root, identifier = Path(started["runtime_root"]), started["request_id"]
        peer_id = next(row["id"] for row in started["repositories"] if row["role"] == "supporting")
        operations = [{"id": "entry-first", "op": "read", "path": "src/service.py"},
                      {"id": "peer-missing", "op": "read", "repo": peer_id, "source": "supporting", "path": "missing.py"},
                      {"id": "entry-later", "op": "read", "path": "src/existing.py"}]
        with patch.object(evidence, "_prefetch_reads", wraps=evidence._prefetch_reads) as prefetch:
            result = flow.step(root, identifier, operations)
        self.assertFalse(result["complete"])
        self.assertEqual(1, result["next_index"])
        self.assertEqual(["src/service.py"], [row["path"] for row in prefetch.call_args.args[2]])
        flow.cancel(root, identifier)

    def test_worklist_ids_auto_shared_content_and_artifact_navigation(self):
        started = self.start()
        root, identifier, context = Path(started["runtime_root"]), started["request_id"], Path(started["context_dir"])
        worklist = context / "input-worklist.json"
        requests = [{"action": "read", "repo": "entry", "source": source, "path": "src/service.py"}
                    for source in ("head", "merge-preview")]
        worklist.write_text(json.dumps({"schema": "ReleaseReviewWorklist/v1", "requests": requests}), encoding="utf-8")
        self.assertEqual(flow.worklist_operations(worklist), flow.worklist_operations(worklist))
        result = flow.step(root, identifier, worklist=worklist, response_format="auto")
        self.assertEqual("ReleaseReviewStep/v2", result["schema"])
        self.assertEqual(1, len(result["contents"]))
        content_id = result["operations"][0]["result"]["content_id"]
        self.assertEqual(content_id, result["operations"][1]["result"]["content_id"])
        self.assertTrue(result["contents"][content_id]["lines"])
        again = flow.step(root, identifier, worklist=worklist)
        self.assertTrue(all(item["reused"] for item in again["operations"]))
        page = flow.compact({**result, "padding": "x" * (flow.LIMIT * 2)}, context)
        self.assertLessEqual(len(review_runtime.canonical(page)), flow.LIMIT)
        view = flow.artifact_view(context, page["artifact"], pointer="/contents/" + content_id + "/lines", page_size=2)
        self.assertEqual(result["contents"][content_id]["lines"][:2], view["items"])
        flow.cancel(root, identifier)

    def test_cli_accepts_worklist_and_uses_same_ids_on_retry(self):
        started = self.start()
        context = Path(started["context_dir"])
        arguments = ["step", "--runtime-root", started["runtime_root"], "--request-id", started["request_id"],
                     "--worklist", context / "review-worklist.json", "--response-format", "auto"]
        first = python(SCRIPTS / "review_session.py", *arguments)
        self.assertEqual(0, first.returncode, first.stderr)
        self.assertLessEqual(len(first.stdout.encode("utf-8")), flow.LIMIT)
        second = python(SCRIPTS / "review_session.py", *arguments)
        self.assertEqual(0, second.returncode, second.stderr)
        response = json.loads(second.stdout)
        if response.get("schema") == "ReleaseReviewEvidenceSummary/v1":
            response = review_runtime.load_view(context, response["artifact"])
        self.assertTrue(response["complete"])
        self.assertTrue(all(row["reused"] for row in response["operations"]))
        flow.cancel(Path(started["runtime_root"]), started["request_id"])

    def test_shared_response_uses_raw_bytes_even_when_decoding_matches(self):
        write(self.repo, "src/invalid-a.txt", b"\xff" * 400 + b"\n")
        write(self.repo, "src/invalid-b.txt", b"\xfe" * 400 + b"\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "distinct invalid byte sequences")
        started = self.start()
        root, identifier = Path(started["runtime_root"]), started["request_id"]
        operations = [{"id": str(i), "op": "read", "path": f"src/invalid-{letter}.txt"}
                      for i, letter in enumerate(("a", "a", "b", "b"))]
        response = flow.step(root, identifier, operations, response_format="auto")
        self.assertEqual("ReleaseReviewStep/v2", response["schema"])
        ids = [row["result"]["content_id"] for row in response["operations"]]
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(ids[2], ids[3])
        self.assertNotEqual(ids[0], ids[2])
        self.assertEqual(response["contents"][ids[0]], response["contents"][ids[2]])
        flow.cancel(root, identifier)

    def test_receipt_rejects_added_loose_objects_controls_and_forged_source(self):
        for damage in ("loose", "config", "source", "downgrade"):
            with self.subTest(damage=damage):
                started = self.start()
                root, identifier, context = Path(started["runtime_root"]), started["request_id"], Path(started["context_dir"])
                manifest_path = context / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                record = manifest["repositories"][0]
                snapshot = frozen_evidence.repository(context, record)
                if damage == "loose":
                    (snapshot / "objects" / "aa").mkdir()
                    (snapshot / "objects" / "aa" / "fake").write_bytes(b"unexpected")
                elif damage == "config":
                    path = snapshot / "config"
                    raw = path.read_bytes()
                    path.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
                elif damage == "source":
                    record["snapshot"]["sources"][manifest["head_sha"]]["entries"][0]["mode"] = "000000"
                    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                else:
                    manifest.pop("validation_policy")
                    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaises(ValueError):
                    flow.step(root, identifier, [{"id": "read", "op": "read", "path": "src/service.py"}])
                self.assertEqual("failed", flow.status(root, identifier)["state"])

    def test_old_session_without_receipts_still_fully_validates(self):
        started = self.start()
        root, identifier, context = Path(started["runtime_root"]), started["request_id"], Path(started["context_dir"])
        path = context / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest.pop("validation_policy")
        manifest["repositories"][0].pop("validation_receipt")
        path.write_text(json.dumps(manifest), encoding="utf-8")
        job_path = flow.job_path(root, identifier)
        job = coordination.read_json(job_path)
        job.pop("snapshot_receipts")
        coordination.atomic(job_path, job)
        with patch.object(frozen_evidence, "validate", wraps=frozen_evidence.validate) as validate:
            result = flow.step(root, identifier, [{"id": "read", "op": "read", "path": "src/service.py"}])
        self.assertTrue(result["complete"])
        self.assertEqual(1, validate.call_count)
        flow.cancel(root, identifier)

    def test_new_capture_cli_rejects_a_missing_or_unknown_receipt_policy(self):
        context, manifest = self.capture()
        path = context / "manifest.json"
        for damage in ("missing-receipt", "unknown-policy"):
            broken = copy.deepcopy(manifest)
            if damage == "missing-receipt":
                broken["repositories"][0].pop("validation_receipt")
            else:
                broken["validation_policy"] = "unknown/v1"
            path.write_text(json.dumps(broken), encoding="utf-8")
            with self.subTest(damage=damage), self.assertRaises(ValueError):
                evidence.load_manifest(context)

    def test_instructions_use_final_entry_tree_and_supporting_committed_head(self):
        git(self.repo, "switch", "main")
        write(self.repo, "AGENTS.md", "final root\n")
        write(self.repo, "src/AGENTS.md", "final nested\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "base instructions")
        git(self.repo, "switch", "feature")
        write(self.repo, "AGENTS.md", "untracked worktree instructions\n")
        peer = init_repo(self.root, "consumer")
        write(peer, "AGENTS.md", "supporting committed\n")
        git(peer, "add", ".")
        git(peer, "commit", "-m", "supporting instructions")
        write(peer, "AGENTS.md", "supporting uncommitted\n")
        started = self.start()
        root, identifier = Path(started["runtime_root"]), started["request_id"]
        peer_id = next(item["id"] for item in started["repositories"] if item["role"] == "supporting")
        result = flow.step(root, identifier, [{"id": "entry-instructions", "op": "instructions"},
                                              {"id": "peer-instructions", "op": "instructions", "repo": peer_id}])
        entry, supporting = [item["result"]["instructions"] for item in result["operations"]]
        self.assertEqual({"final root\n", "final nested\n"}, {item["content"] for item in entry})
        self.assertEqual({"supporting committed\n"}, {item["content"] for item in supporting})
        manifest = evidence.load_manifest(Path(started["context_dir"]), full=False)
        self.assertTrue(all(item["ref"] == manifest["merge_preview"]["tree_sha"] for item in entry))
        self.assertFalse(any(Path(item["origin"]) == self.repo / "AGENTS.md" for item in manifest["workspace_instructions"]))
        flow.cancel(root, identifier)

    def test_risk_causality_and_deleted_path_absence_are_checked(self):
        git(self.repo, "rm", "src/existing.py")
        git(self.repo, "commit", "-m", "delete previously used file")
        context, manifest = self.capture()
        draft = draft_for(manifest)
        final_source, final_ref = release_report.final_version(manifest)
        risk = {**risk_item(manifest), **diff_anchor(manifest, "src/existing.py"),
                "evidence": [citation(manifest, "base", "src/existing.py", 1, 2)],
                "final_absence": [{"repo": "entry", "source": final_source, "ref": final_ref,
                                   "path": "src/existing.py"}]}
        draft["code_risks"] = [risk]
        result = release_report.validate_result(draft, manifest, context)
        self.assertEqual("src/existing.py", result["code_risks"][0]["final_absence"][0]["path"])
        self.assertIn("最終版本不存在", release_report.render_markdown(result))
        for field in ("diff_roots", "diff_reason"):
            broken = copy.deepcopy(draft)
            broken["code_risks"][0].pop(field)
            with self.assertRaises(release_report.DraftValidationError):
                release_report.validate_result(broken, manifest, context)
        broken = copy.deepcopy(draft)
        broken["code_risks"][0]["final_absence"][0]["path"] = "src/service.py"
        with self.assertRaises(release_report.DraftValidationError):
            release_report.validate_result(broken, manifest, context)

    def test_legacy_risks_without_new_causality_fields_remain_compatible(self):
        context, manifest = self.capture()
        manifest.pop("risk_causality_policy")
        draft = draft_for(manifest)
        risk = risk_item(manifest)
        risk.pop("diff_roots")
        risk.pop("diff_reason")
        draft["code_risks"] = [risk]
        self.assertEqual(1, len(release_report.validate_result(draft, manifest, context)["code_risks"]))


if __name__ == "__main__":
    unittest.main()
