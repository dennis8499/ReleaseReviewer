"""Author-controlled files are repairable; owned evidence is never rebuilt."""
import contextlib
import copy
import io
from unittest.mock import patch

from support import *
import frozen_evidence
import git_support
import review_coordinator as coordination
import review_flow as flow


class InputRecoveryTests(RepoTest):
    def start(self):
        started = flow.start(flow.parser().parse_args([
            "start", "--workspace", str(self.root), "--project", str(self.repo),
            "--base", "main", "--head", "feature"]))
        return Path(started["runtime_root"]), started["request_id"], Path(started["context_dir"])

    def cli(self, command, root, identifier, *extra):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = flow.main([command, "--runtime-root", str(root), "--request-id", identifier, *extra])
        return code, json.loads(output.getvalue() or errors.getvalue())

    @contextlib.contextmanager
    def no_git(self):
        with patch.object(git_support, "run_git", side_effect=AssertionError("Git for an input error")), \
             patch.object(evidence, "command", side_effect=AssertionError("Git for an input error")):
            yield

    def test_external_file_errors_preserve_prefix_and_same_ids_resume(self):
        root, identifier, context = self.start()
        operations = [{"id": "prefix", "op": "read", "path": "src/service.py"},
                      {"id": "repair", "op": "read", "path": "missing.py"}]
        first = flow.step(root, identifier, operations)
        self.assertFalse(first["complete"])
        self.assertEqual(1, first["next_index"])
        checkpoint = evidence.load_manifest(context, full=False)["operations"]["prefix"]
        requests = self.root / "agent-round.json"
        real_read = coordination.read_json
        for problem in ("missing", "malformed", "unreadable"):
            with self.subTest(problem=problem):
                if problem == "missing":
                    requests.unlink(missing_ok=True)
                else:
                    requests.write_text("{broken" if problem == "malformed" else "[]", encoding="utf-8")
                def read(path):
                    if problem == "unreadable" and Path(path) == requests:
                        raise PermissionError("synthetic input ACL")
                    return real_read(path)
                with self.no_git(), patch.object(coordination, "read_json", side_effect=read):
                    code, result = self.cli("step", root, identifier, "--requests-file", str(requests))
                    state = flow.status(root, identifier)
                self.assertEqual(3, code)
                self.assertFalse(result["terminal"])
                self.assertTrue(result["recoverable"])
                self.assertTrue(result["context_retained"])
                self.assertEqual("authoring.invalid-requests-file", result["errors"][0]["code"])
                self.assertEqual(0, result["git_commands"])
                self.assertEqual(("reviewing", "input-repair"), (state["state"], state["phase"]))
                self.assertEqual(checkpoint, state["resume"]["operations"]["prefix"])
        operations[1]["path"] = "src/existing.py"
        requests.write_text(json.dumps(operations), encoding="utf-8")
        with patch.object(evidence, "request_result", wraps=evidence.request_result) as requests_run:
            code, resumed = self.cli("step", root, identifier, "--requests-file", str(requests))
        self.assertEqual(0, code)
        self.assertTrue(resumed["complete"])
        self.assertTrue(resumed["operations"][0]["reused"])
        self.assertEqual(1, requests_run.call_count)
        self.assertEqual("src/existing.py", requests_run.call_args.args[2]["path"])
        self.assertEqual("review", flow.status(root, identifier)["phase"])

    def test_missing_flag_or_wrong_operations_shape_is_repairable(self):
        root, identifier, context = self.start()
        with self.no_git():
            code, result = self.cli("step", root, identifier)
        self.assertEqual(3, code)
        self.assertFalse(result["terminal"])
        self.assertEqual("authoring.invalid-operations", result["errors"][0]["code"])
        self.assertTrue(context.exists())
        self.assertTrue(flow.step(root, identifier, [{"id": "read", "op": "read", "path": "src/service.py"}])["complete"])

    def test_draft_file_errors_are_zero_git_and_same_request_finishes_once(self):
        root, identifier, context = self.start()
        manifest = evidence.load_manifest(context, full=False)
        valid = draft_for(manifest)
        draft_path = save_draft(context, valid)
        real_read_text = Path.read_text
        for problem in ("missing", "malformed", "unreadable"):
            with self.subTest(problem=problem):
                if problem == "missing":
                    draft_path.unlink(missing_ok=True)
                else:
                    draft_path.write_text("{broken" if problem == "malformed" else json.dumps(valid), encoding="utf-8")
                def read_text(path, *args, **kwargs):
                    if problem == "unreadable" and path == draft_path:
                        raise PermissionError("synthetic draft ACL")
                    return real_read_text(path, *args, **kwargs)
                with self.no_git(), patch.object(Path, "read_text", read_text):
                    code, result = self.cli("finish", root, identifier)
                    state = flow.status(root, identifier)
                self.assertEqual(3, code)
                self.assertFalse(result["terminal"])
                self.assertTrue(result["recoverable"])
                self.assertEqual("authoring.invalid-draft-file", result["errors"][0]["code"])
                self.assertEqual(("reviewing", "draft-repair"), (state["state"], state["phase"]))
                self.assertTrue(context.exists())
        save_draft(context, valid)
        with patch.object(release_report, "validate_result", wraps=release_report.validate_result) as validate, \
             patch.object(frozen_evidence, "validate", wraps=frozen_evidence.validate) as snapshots:
            finished, code = flow.finish(root, identifier)
        self.assertEqual(0, code)
        self.assertEqual("published", finished["state"])
        self.assertEqual(1, validate.call_count)
        self.assertEqual(1, snapshots.call_count)
        self.assertFalse(context.exists())

    def test_corrupt_owned_manifest_is_fatal_even_when_external_file_is_missing(self):
        root, identifier, context = self.start()
        (context / "manifest.json").write_text("{broken", encoding="utf-8")
        with self.no_git():
            code, result = self.cli("step", root, identifier, "--requests-file", str(self.root / "absent.json"))
        self.assertEqual(2, code)
        self.assertTrue(result["terminal"])
        self.assertEqual("failed", flow.status(root, identifier)["state"])
        self.assertFalse(context.exists())

    def test_corrupt_successful_checkpoint_is_fatal_without_source_fallback(self):
        root, identifier, context = self.start()
        operation = {"id": "read", "op": "read", "path": "src/service.py"}
        first = flow.step(root, identifier, [operation])
        (context / "views" / (first["operations"][0]["artifact"] + ".json")).write_text("{broken", encoding="utf-8")
        with self.no_git(), patch.object(evidence, "request_result", side_effect=AssertionError("reran successful prefix")):
            with self.assertRaises(ValueError):
                flow.step(root, identifier, [operation])
        self.assertEqual("failed", flow.status(root, identifier)["state"])
        self.assertFalse(context.exists())

    def test_json_diagnostics_enforce_existing_source_and_impact_relations(self):
        root, identifier, context = self.start()
        manifest = evidence.load_manifest(context, full=False)
        valid = draft_for(manifest)
        config = config_item(context, manifest)
        cases = []
        changed = copy.deepcopy(valid)
        changed["dependency_coverage"][0]["evidence"] = [citation(manifest, "base", "src/service.py")]
        cases.append((changed, "dependency_final_source"))
        changed = copy.deepcopy(valid)
        changed["config_changes"] = [{**config, "evidence": [citation(manifest, "base", "src/service.py")]}]
        cases.append((changed, "extension_final_source"))
        changed = copy.deepcopy(valid)
        changed["config_changes"] = [{**config, "definition_status": "defined", "definition_evidence": [citation(manifest, "base", "src/service.py")]}]
        cases.append((changed, "definition_final_source"))
        changed = copy.deepcopy(valid)
        changed["code_risks"] = [{**risk_item(manifest), "evidence": [citation(manifest, "base", "src/service.py")]}]
        cases.append((changed, "risk_final_source"))
        changed = copy.deepcopy(valid)
        final = release_report.final_version(manifest)[0]
        changed["code_risks"] = [{**risk_item(manifest), "evidence": [citation(manifest, final, "src/existing.py")]}]
        cases.append((changed, "risk_changed_path"))
        changed = copy.deepcopy(valid)
        changed["functional_chain"] = [{"id": "F-001", "from_repo": "entry", "to_repo": "entry", "kind": "api",
                                         "status": "reviewed", "description": "An entry-local API call.", **diff_anchor(manifest),
                                         "evidence": [citation(manifest, "base", "src/service.py")]}]
        cases.append((changed, "chain_final_source"))
        with self.no_git():
            self.assertTrue(flow.diagnostics(valid, manifest)["precheck_passed"])
            for draft, expected_code in cases:
                with self.subTest(code=expected_code):
                    checked = flow.diagnostics(draft, manifest)
                    self.assertFalse(checked["precheck_passed"])
                    self.assertIn(expected_code, {error["code"] for error in checked["errors"]})
                    self.assertFalse(checked["evidence_verified"])
                    self.assertEqual(0, checked["git_commands"])

    def test_merge_parent_diagnostics_bind_evidence_to_its_own_paths_and_versions(self):
        git(self.repo, "switch", "-c", "other", "main")
        write(self.repo, "src/extra.py", "from_other_parent = True\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "other parent")
        git(self.repo, "switch", "feature")
        git(self.repo, "merge", "--no-ff", "other", "-m", "merge other parent")
        root, identifier, context = self.start()
        manifest = evidence.load_manifest(context, full=False)
        valid = draft_for(manifest)
        view = next(view for view in manifest["required_merge_views"] if view["parent_content_available"])
        row = next(row for row in valid["merge_parent_review"] if row["id"] == view["id"])
        self.assertNotIn("src/existing.py", view["paths"])
        with self.no_git():
            self.assertTrue(flow.diagnostics(valid, manifest)["precheck_passed"])
            for ref, expected in ((view["parent"], "parent_source_evidence"),
                                  (release_report.final_version(manifest)[1], "parent_final_evidence")):
                changed = copy.deepcopy(valid)
                changed_row = next(item for item in changed["merge_parent_review"] if item["id"] == row["id"])
                changed_row["evidence"] = [{**item, "path": "src/existing.py"} if item["ref"] == ref else item for item in row["evidence"]]
                checked = flow.diagnostics(changed, manifest)
                self.assertIn(expected, {error["code"] for error in checked["errors"]})
