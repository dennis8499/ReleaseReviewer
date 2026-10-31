import copy
import hashlib
import json
import os
import unittest
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
        metrics = flow.status(root, identifier)["metrics"]
        self.assertEqual(8, metrics["step_operation_requests"])
        self.assertEqual(4, metrics["step_operations"])
        self.assertEqual(2, metrics["step_reused_operations"])
        flow.cancel(root, identifier)

    def test_snapshot_resolves_frozen_trees_and_instructions_in_batches(self):
        write(self.repo, "AGENTS.md", "Keep this captured review invariant.\n")
        git(self.repo, "add", "AGENTS.md")
        git(self.repo, "commit", "-m", "add scoped review guidance")
        args = flow.parser().parse_args(["start", "--workspace", str(self.root), "--project", str(self.repo),
                                         "--base", "main", "--head", "feature"])
        with patch.object(context_helper.git, "run_git", wraps=context_helper.git.run_git) as calls:
            started = flow.start(args)
        manifest = json.loads((Path(started["context_dir"]) / "manifest.json").read_text(encoding="utf-8"))
        tree_batches = [call for call in calls.call_args_list
                        if call.args[1][:2] == ["cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"]]
        instruction_batches = [call for call in calls.call_args_list
                               if call.args[1] == ["cat-file", "--batch"]]
        self.assertEqual(2, len(tree_batches))  # capture plus independent sealed-snapshot validation
        self.assertEqual(2, len(instruction_batches))  # capture plus independent content check
        self.assertGreaterEqual(len(manifest["repositories"][0]["instructions"]), 1)
        self.assertFalse(any(call.args[1][0] == "rev-parse" and
                             any(value.endswith("^{tree}") for value in call.args[1])
                             for call in calls.call_args_list))
        flow.cancel(Path(started["runtime_root"]), started["request_id"])

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

    def test_oversized_single_artifact_field_is_segmented_with_following_page(self):
        started = self.start()
        context = Path(started["context_dir"])
        original = "長字串內容" * 12000
        value = {"operations": [{"id": "large", "text": original}, {"id": "after", "text": "next"}],
                 "results": [], "padding": "x" * (flow.LIMIT * 2)}
        summary = flow.compact(value, context)
        self.assertLessEqual(len(review_runtime.canonical(summary)), flow.LIMIT)
        self.assertEqual("/operations", summary["continue"]["pointer"])
        self.assertEqual(0, summary["continue"]["offset"])
        self.assertEqual("/operations", summary["next_page"]["pointer"])
        page = flow.artifact_view(context, summary["artifact"], pointer="/operations", page_size=100)
        self.assertLessEqual(len(review_runtime.canonical(page)), flow.LIMIT)
        self.assertEqual(0, page["items"][0]["index"])
        self.assertEqual("/operations", page["next_page"]["pointer"])
        self.assertEqual(1, page["next_page"]["offset"])
        request = page["continue"]
        chunks = []
        while request:
            self.assertEqual("view", request["op"])
            request = {key: item for key, item in request.items() if key != "op"}
            part = flow.artifact_view(context, **request)
            self.assertLessEqual(len(review_runtime.canonical(part)), flow.LIMIT)
            chunks.append(part["text"])
            request = part.get("continue")
        self.assertEqual(original, "".join(chunks))
        following = page["next_page"]
        following = {key: item for key, item in following.items() if key != "op"}
        self.assertEqual("after", flow.artifact_view(context, **following)["items"][0]["id"])
        with self.assertRaises(ValueError):
            flow.artifact_view(context, summary["artifact"], pointer="/operations", offset=3)
        with self.assertRaises(ValueError):
            flow.artifact_view(context, summary["artifact"], pointer="/operations/0/text",
                               string_offset=len(original) + 1)
        flow.cancel(Path(started["runtime_root"]), started["request_id"])

    def test_compact_summary_continuation_covers_first_and_later_list_pages(self):
        started = self.start()
        context = Path(started["context_dir"])
        operations = [{"id": f"operation-{index}", "text": f"證據列 {index}"} for index in range(1500)]
        summary = flow.compact({"operations": operations, "padding": "x" * (flow.LIMIT * 2)}, context)
        self.assertEqual("/operations", summary["continue"]["pointer"])
        request = {key: item for key, item in summary["continue"].items() if key != "op"}
        visited = []
        while request:
            page = flow.artifact_view(context, **request)
            self.assertLessEqual(len(review_runtime.canonical(page)), flow.LIMIT)
            visited.extend(item["id"] for item in page["items"])
            continuation = page.get("continue")
            request = ({key: item for key, item in continuation.items() if key != "op"}
                       if continuation else None)
        self.assertEqual([item["id"] for item in operations], visited)
        self.assertEqual(len(visited), len(set(visited)))
        flow.cancel(Path(started["runtime_root"]), started["request_id"])

    def test_large_object_fields_and_nested_string_have_complete_cursors(self):
        started = self.start()
        context = Path(started["context_dir"])
        fields = {f"field-{index:03}": index for index in range(93)}
        fields["payload"] = "\u6f22\u5b57\U0001f9ea" * 12000
        artifact = review_runtime.store_view(context, fields)
        page = flow.artifact_view(context, artifact, page_size=17)
        names = list(page["fields"])
        cursor = page.get("continue")
        while cursor:
            page = flow.artifact_view(context, **{key: value for key, value in cursor.items() if key != "op"})
            names.extend(page["fields"])
            cursor = page.get("continue")
        self.assertEqual(list(fields), names)
        self.assertEqual(len(names), len(set(names)))
        cursor = {"artifact": artifact, "pointer": "/payload"}
        chunks = []
        while cursor:
            page = flow.artifact_view(context, **cursor)
            chunks.append(page["text"])
            next_cursor = page.get("continue")
            cursor = ({key: value for key, value in next_cursor.items() if key != "op"}
                      if next_cursor else None)
        self.assertEqual(fields["payload"], "".join(chunks))
        small = review_runtime.store_view(context, {"only": "field"})
        with self.assertRaises(ValueError):
            flow.artifact_view(context, small, offset=2)
        flow.cancel(Path(started["runtime_root"]), started["request_id"])

    def test_string_view_uses_utf8_byte_budget_and_handles_empty_and_tail(self):
        started = self.start()
        context = Path(started["context_dir"])
        value = {"text": "漢😊" * 12000}
        artifact = review_runtime.store_view(context, value)
        request = {"artifact": artifact, "pointer": "/text"}
        chunks = []
        while request:
            page = flow.artifact_view(context, **request)
            self.assertLessEqual(len(review_runtime.canonical(page)), flow.VIEW_LIMIT)
            chunks.append(page["text"])
            continuation = page.get("continue")
            request = ({key: item for key, item in continuation.items() if key != "op"}
                       if continuation else None)
        self.assertEqual(value["text"], "".join(chunks))
        empty = flow.artifact_view(context, review_runtime.store_view(context, {"text": ""}), pointer="/text")
        self.assertEqual("", empty["text"])
        self.assertTrue(empty["view_complete"])
        tail = flow.artifact_view(context, artifact, pointer="/text", string_offset=len(value["text"]) - 1)
        self.assertEqual(value["text"][-1], tail["text"])
        self.assertTrue(tail["view_complete"] is False)
        flow.cancel(Path(started["runtime_root"]), started["request_id"])

    def test_patch_content_keeps_original_hash_and_reversible_invalid_bytes(self):
        raw = (b"diff --git a/a.txt b/a.txt\nindex 1..2 100644\n@@ -1 +1 @@\n"
               b"-legacy:\xff\n+utf8:\xe6\xbc\xa2\n"
               b"diff --git a/b.txt b/b.txt\n@@ -0,0 +1 @@\n+added\n")
        result = flow.patch_content(Path("diff.patch"), raw)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), result["raw_sha256"])
        self.assertEqual(len(raw), result["raw_byte_length"])
        self.assertEqual("utf-8-byte-escaped", result["encoding"])
        self.assertTrue(result["display_has_byte_escapes"])
        self.assertIn(r"\xff", result["text"])
        self.assertEqual(result["text"], "".join(item["text"] for item in result["segments"]))
        self.assertEqual(["file-header", "hunk", "file-header", "hunk"],
                         [item["kind"] for item in result["segments"]])
        self.assertEqual([0, 0, 1, 1], [item["file_index"] for item in result["segments"]])

    def test_patch_content_honors_utf16_bom_and_explicit_encoding(self):
        text = "diff --git a/a.txt b/a.txt\n@@ -1 +1 @@\n-café\n+料金\n"
        raw = b"\xff\xfe" + text.encode("utf-16-le")
        result = flow.patch_content(Path("diff.patch"), raw)
        self.assertEqual(text, result["text"])
        self.assertEqual("utf-16-le-bom", result["encoding"])
        self.assertEqual(text, "".join(part["text"] for part in result["segments"]))
        self.assertEqual(0, result["segments"][0]["byte_start"])
        self.assertEqual(len(raw), result["segments"][-1]["byte_end"])
        legacy = b"diff --git a/a.txt b/a.txt\n@@ -1 +1 @@\n-caf\xe9\n+old\n"
        decoded = flow.patch_content(Path("diff.patch"), legacy, encoding="cp1252")
        self.assertIn("café", decoded["text"])
        self.assertEqual("cp1252", decoded["encoding"])
        with self.assertRaises(ValueError):
            flow.patch_content(Path("diff.patch"), raw, encoding="cp1252")

    def test_read_window_handles_file_tail_and_strict_read_stays_strict(self):
        started = self.start()
        root, identifier = Path(started["runtime_root"]), started["request_id"]
        context = Path(started["context_dir"])
        manifest = evidence.load_manifest(context, full=False)
        source = evidence.read_blob(manifest, context, "head", "src/service.py")
        total = len(source.splitlines())
        first = flow.step(root, identifier, [{"id": "window-first", "op": "read-window",
                                              "source": "head", "path": "src/service.py",
                                              "line_start": 1, "line_count": 2}])
        value = first["operations"][0]["result"]
        self.assertEqual(total, value["total_lines"])
        self.assertEqual(1, value["line_start"])
        self.assertEqual(2, value["line_end"])
        self.assertEqual(2, value["line_count"])
        self.assertEqual(2, value["requested_line_count"])
        self.assertEqual(3, value["next_line_start"])
        self.assertEqual(3, value["next_window"]["line_start"])
        tail = flow.step(root, identifier, [{"id": "window-tail", "op": "read-window",
                                            "source": "head", "path": "src/service.py",
                                            "line_start": total + 4, "line_count": 20}])
        tail_value = tail["operations"][0]["result"]
        self.assertEqual([], tail_value["lines"])
        self.assertEqual(0, tail_value["line_count"])
        self.assertEqual(20, tail_value["requested_line_count"])
        self.assertIsNone(tail_value["line_end"])
        self.assertIsNone(tail_value["next_line_start"])
        strict = flow.step(root, identifier, [{"id": "strict-read", "op": "read",
                                              "source": "head", "path": "src/service.py",
                                              "line_start": 1, "line_end": total + 1}])
        self.assertFalse(strict["complete"])
        self.assertEqual(0, strict["next_index"])
        flow.cancel(root, identifier)

    def test_consecutive_case_sensitive_literal_searches_share_git_grep(self):
        started = self.start()
        root, identifier = Path(started["runtime_root"]), started["request_id"]
        operations = [{"id": "search-billing", "op": "search", "source": "head", "query": "Billing"},
                      {"id": "search-billing-db", "op": "search", "source": "head", "query": "BillingDb"},
                      {"id": "search-tax-rate", "op": "search", "source": "head", "query": "tax_rate"}]
        with patch.object(evidence, "command", wraps=evidence.command) as command:
            result = flow.step(root, identifier, operations)
        self.assertTrue(result["complete"])
        grep_calls = [call for call in command.call_args_list if call.args[1][0] == "grep"]
        self.assertEqual(1, len(grep_calls))
        self.assertEqual(3, len([arg for arg in grep_calls[0].args[1] if arg == "-e"]))
        rows = [item["result"] for item in result["operations"]]
        self.assertTrue(all(item["search_id"] for item in rows))
        self.assertIn("src/service.py", {match["path"] for match in rows[0]["matches"]})
        self.assertEqual(rows[0]["matches"], rows[1]["matches"])
        manifest = evidence.load_manifest(Path(started["context_dir"]), full=False)
        expected = []
        with evidence.operation_cache(manifest, Path(started["context_dir"]), persistent=False):
            expected = [evidence.search(manifest, Path(started["context_dir"]), "head", query)
                        for query in ("Billing", "BillingDb", "tax_rate")]
        self.assertEqual(expected, [{key: value for key, value in row.items() if key != "search_id"}
                                    for row in rows])
        flow.cancel(root, identifier)

    def test_search_batching_does_not_accept_extra_fields_on_first_operation(self):
        started = self.start()
        root, identifier = Path(started["runtime_root"]), started["request_id"]
        operations = [{"id": "invalid-search", "op": "search", "source": "head",
                       "query": "Billing", "unexpected": True},
                      {"id": "valid-search", "op": "search", "source": "head", "query": "BillingDb"}]
        with patch.object(evidence, "command", wraps=evidence.command) as command:
            result = flow.step(root, identifier, operations)
        self.assertFalse(result["complete"])
        self.assertEqual(0, result["next_index"])
        self.assertFalse(any(call.args[1][0] == "grep" for call in command.call_args_list))
        flow.cancel(root, identifier)

    def test_single_operation_request_file_is_still_a_utf8_json_array(self):
        started = self.start()
        root, identifier, context = (Path(started["runtime_root"]), started["request_id"],
                                     Path(started["context_dir"]))
        request_file = context / "single-operation.json"
        request_file.write_text(json.dumps([{"id": "single-array", "op": "read-window",
            "source": "head", "path": "src/service.py", "line_start": 1, "line_count": 1}],
            ensure_ascii=False), encoding="utf-8")
        result = flow.step(root, identifier, requests_file=request_file)
        self.assertTrue(result["complete"])
        self.assertEqual("single-array", result["operations"][0]["id"])
        self.assertEqual(1, len(result["operations"][0]["result"]["lines"]))
        flow.cancel(root, identifier)

    def test_operation_range_errors_include_pointer_range_and_repair(self):
        started = self.start()
        root, identifier = Path(started["runtime_root"]), started["request_id"]
        result = flow.step(root, identifier, [{"id": "bad-window", "op": "read-window",
            "source": "head", "path": "src/service.py", "line_start": 1, "line_count": 1001}])
        self.assertFalse(result["complete"])
        self.assertEqual("/operations/0/line_count", result["errors"][0]["pointer"])
        self.assertEqual("integer 1..1000", result["errors"][0]["valid_range"])
        self.assertIn("Reduce the window", result["errors"][0]["correction"])
        flow.cancel(root, identifier)

    def test_capture_and_step_metrics_separate_git_bytes_from_cli_and_agent_metrics(self):
        started = self.start()
        root, identifier = Path(started["runtime_root"]), started["request_id"]
        metrics = flow.status(root, identifier)["metrics"]
        self.assertGreater(metrics["capture_git_commands"], 0)
        self.assertGreater(metrics["capture_git_output_bytes"], 0)
        self.assertGreater(metrics["capture_instrumented_seconds"], 0)
        self.assertIsInstance(metrics["capture_phase_seconds"], dict)
        self.assertIn("fetch_seconds", metrics)
        self.assertIn("network_lock_wait_seconds", metrics)
        self.assertIn("request_owner_lock_wait_seconds", metrics)
        self.assertIn("pack_seconds", metrics)
        self.assertIn("index_seconds", metrics)
        self.assertIsNone(metrics["agent_tool_round_trips"])
        self.assertIsNone(metrics["model_tokens"])
        self.assertNotIn("cli_stdout_bytes", metrics)
        flow.cancel(root, identifier)

    def test_capabilities_are_granular_readonly_and_bind_the_installed_payload(self):
        before = sorted(path.relative_to(SKILL).as_posix() for path in SKILL.rglob("*") if path.is_file())
        result = flow.capabilities(self.root)
        after = sorted(path.relative_to(SKILL).as_posix() for path in SKILL.rglob("*") if path.is_file())
        self.assertEqual(before, after)
        self.assertEqual("ReviewCapabilities/v1", result["schema"])
        self.assertEqual("ReleaseReviewUpdates/v1", result["updates_schema"])
        self.assertTrue(result["features"]["read_window"])
        self.assertTrue(result["features"]["request_id"])
        self.assertIsNone(result["observability"]["http_requests"])
        self.assertEqual(0, result["git_commands"])
        self.assertEqual(0, result["network_commands"])

    def test_updates_file_revision_replay_and_readonly_queries(self):
        started = self.start()
        root, identifier, context = (Path(started["runtime_root"]), started["request_id"],
                                     Path(started["context_dir"]))
        update_file = context / "updates.json"
        document = {"schema": "ReleaseReviewUpdates/v1", "request_id": identifier,
                    "expected_revision": 0, "update_id": "summary-1",
                    "draft_updates": [{"pointer": "/summary", "value": "已讀取證據後完成判斷。"}],
                    "progress": {"read_evidence_ids": ["evidence-1"],
                                 "unread_cursor": {"artifact": "a" * 64, "offset": 2},
                                 "excluded_questions": ["既有且未改動的設定不屬本次新增依賴"],
                                 "next_action": "讀取下一個必要變更檔"}}
        update_file.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        applied = flow.step(root, identifier, [], updates_file=update_file)
        self.assertTrue(applied["complete"])
        self.assertEqual({"update_id": "summary-1", "revision": 1,
                          "applied": True, "replayed": False}, applied["draft_update"])
        draft = json.loads((context / "review-result-draft.json").read_text(encoding="utf-8"))
        self.assertEqual("已讀取證據後完成判斷。", draft["summary"])
        replay = flow.step(root, identifier, [], updates_file=update_file)
        self.assertEqual(1, replay["draft_update"]["revision"])
        self.assertTrue(replay["draft_update"]["replayed"])
        conflicting = {**document, "expected_revision": 0, "update_id": "summary-2",
                       "draft_updates": [{"pointer": "/summary", "value": "不得覆寫"}]}
        update_file.write_text(json.dumps(conflicting, ensure_ascii=False), encoding="utf-8")
        rejected = flow.step(root, identifier, [], updates_file=update_file)
        self.assertFalse(rejected["complete"])
        self.assertIn("current revision is 1", rejected["error"])
        self.assertEqual("已讀取證據後完成判斷。",
                         json.loads((context / "review-result-draft.json").read_text(encoding="utf-8"))["summary"])

        artifact = review_runtime.store_view(context, {"rows": [{"id": 1}, {"id": 2}]})
        manifest_path = context / "manifest.json"
        manifest_before = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        view_files_before = sorted(path.name for path in (context / "views").iterdir())
        with patch.object(evidence, "command", side_effect=AssertionError("read-only query ran Git")):
            summary = flow.status_summary(root, identifier)
            page = flow.view_page(root, identifier, artifact, pointer="/rows", page_size=1)
        self.assertEqual(1, summary["review_progress"]["revision"])
        self.assertGreater(summary["counts"]["worklist_remaining"], 0)
        self.assertEqual(1, len(page["items"]))
        self.assertEqual(manifest_before, hashlib.sha256(manifest_path.read_bytes()).hexdigest())
        self.assertEqual(view_files_before, sorted(path.name for path in (context / "views").iterdir()))
        flow.cancel(root, identifier)

    def test_draft_update_survives_a_failed_operation_and_resumes_successful_prefix(self):
        started = self.start()
        root, identifier, context = (Path(started["runtime_root"]), started["request_id"],
                                     Path(started["context_dir"]))
        update_file = context / "partial-update.json"
        document = {"schema": "ReleaseReviewUpdates/v1", "request_id": identifier,
                    "expected_revision": 0, "update_id": "save-before-reads",
                    "draft_updates": [{"pointer": "/summary", "value": "Checkpointed before evidence work."}],
                    "progress": {"next_action": "Fix the invalid query and resume."}}
        update_file.write_text(json.dumps(document), encoding="utf-8")
        operations = [{"id": "prefix-read", "op": "read", "source": "head", "path": "src/service.py"},
                      {"id": "invalid-query", "op": "search", "source": "head", "query": ""}]
        result = flow.step(root, identifier, operations, updates_file=update_file)
        self.assertFalse(result["complete"])
        self.assertEqual(1, result["next_index"])
        self.assertEqual(1, result["draft_update"]["revision"])
        self.assertEqual("Checkpointed before evidence work.",
                         json.loads((context / "review-result-draft.json").read_text(encoding="utf-8"))["summary"])
        manifest = evidence.load_manifest(context, full=False)
        self.assertIn("prefix-read", manifest["operations"])
        operations[1] = {"id": "invalid-query", "op": "search", "source": "head", "query": "BillingDb"}
        resumed = flow.step(root, identifier, operations)
        self.assertTrue(resumed["complete"])
        self.assertTrue(resumed["operations"][0]["reused"])
        flow.cancel(root, identifier)

    @unittest.skipUnless(os.name == "nt", "Windows managed temp volumes may deny hard links")
    def test_artifact_store_falls_back_to_atomic_rename_when_hard_links_are_denied(self):
        started = self.start()
        context = Path(started["context_dir"])
        value = {"saved": "完整固定證據"}
        with patch.object(review_runtime.os, "link", side_effect=PermissionError("hard links denied")):
            identifier = review_runtime.store_view(context, value)
        self.assertEqual(value, review_runtime.load_view(context, identifier))
        flow.cancel(Path(started["runtime_root"]), started["request_id"])

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
