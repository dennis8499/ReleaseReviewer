"""Offline adversarial tests for release provenance and resumable publication."""
import copy
import datetime as dt
import importlib.util
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock
import urllib.request
import zipfile

spec = importlib.util.spec_from_file_location("release_ci", Path(__file__).with_name("release_ci.py"))
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


class ProofTests(unittest.TestCase):
    def setUp(self):
        self.now = ci.utcnow()
        self.source = {"repository": "owner/MergeReviewer", "sha": "a" * 40,
                       "workflow": ".github/workflows/ci.yml", "version": "1.2.3",
                       "matrix": {"windows-py3.14": ["Windows", "3.14"]}, "canonical": "windows-py3.14"}
        self.run = {"id": 10, "head_sha": self.source["sha"], "head_repository": {"full_name": "owner/MergeReviewer"},
                    "path": self.source["workflow"], "created_at": self.now.isoformat(), "event": "push",
                    "head_branch": "main", "status": "completed", "conclusion": "success"}
        self.job = {"name": "Validate [windows-py3.14]", "status": "completed", "conclusion": "success", "run_attempt": 1}

    def test_main_and_manual_main_are_trusted_but_pr_and_foreign_sources_are_not(self):
        for event in ("push", "workflow_dispatch"):
            ci.validate_run(dict(self.run, event=event), self.source)
        for field, value in (("event", "pull_request"), ("head_branch", "feature"), ("head_sha", "b" * 40),
                             ("status", "in_progress"), ("conclusion", "cancelled"), ("path", "other.yml"),
                             ("head_repository", {"full_name": "attacker/MergeReviewer"})):
            with self.subTest(field=field), self.assertRaises(ci.ProofError):
                ci.validate_run(dict(self.run, **{field: value}), self.source)

    def test_expired_future_and_naive_timestamps_are_rejected(self):
        for value in ((self.now - dt.timedelta(days=8)).isoformat(), (self.now + dt.timedelta(hours=1)).isoformat(), "2026-10-16T00:00:00"):
            with self.assertRaises(ci.ProofError):
                ci.validate_run(dict(self.run, created_at=value), self.source)

    def test_fallback_is_current_matching_tag_run_only(self):
        run = dict(self.run, head_branch="v1.2.3", status="in_progress", conclusion=None)
        ci.validate_run(run, self.source, fallback=True, current_run="10", tag="v1.2.3")
        for current, tag in (("11", "v1.2.3"), ("10", "v1.2.4")):
            with self.assertRaises(ci.ProofError):
                ci.validate_run(run, self.source, fallback=True, current_run=current, tag=tag)

    def test_matrix_requires_each_cell_success_latest_attempt_and_no_duplicates(self):
        self.assertEqual(set(ci.matrix_jobs([self.job], self.source["matrix"])), {"windows-py3.14"})
        for jobs in ([], [dict(self.job, conclusion="skipped")], [dict(self.job, conclusion="cancelled")],
                     [self.job, self.job], [self.job, dict(self.job, run_attempt=2, conclusion="failure")]):
            with self.assertRaises(ci.ProofError):
                ci.matrix_jobs(jobs, self.source["matrix"])
        retry = dict(self.job, run_attempt=2)
        self.assertEqual(ci.matrix_jobs([dict(self.job, conclusion="failure"), retry], self.source["matrix"])["windows-py3.14"], retry)

    def artifact(self, mutate=None, extra=None):
        data = b"exact validated release"
        filename = "merge-reviewer-1.2.3.zip"
        proof = {"schema": ci.SCHEMA, "identity": copy.deepcopy(self.source), "cell": "windows-py3.14",
                 "environment": ["Windows", "3.14"], "run_id": 10, "run_attempt": 1,
                 "event": "push", "ref": "refs/heads/main", "created_at": self.now.isoformat(),
                 "files": {filename: {"sha256": ci.digest(data), "bytes": len(data)}}}
        if mutate:
            mutate(proof)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as stream:
            stream.writestr("release-proof.json", json.dumps(proof))
            stream.writestr(filename, data)
            if extra:
                stream.writestr(extra, b"unexpected")
        raw = buffer.getvalue()
        row = {"id": 20, "name": f"release-ci-{self.source['sha']}-windows-py3.14-1", "expired": False,
               "digest": "sha256:" + ci.digest(raw), "workflow_run": {"id": 10, "head_sha": self.source["sha"]}}
        api = mock.Mock()
        api.pages.return_value = [row]
        api.request.return_value = raw
        return api, row

    def verify(self, api):
        return ci.verify_artifacts(api, self.run, self.source, {"windows-py3.14": self.job})

    def test_exact_canonical_bytes_and_complete_artifact_inventory(self):
        api, row = self.artifact()
        self.assertEqual(self.verify(api), {"merge-reviewer-1.2.3.zip": b"exact validated release"})
        for field, value in (("digest", None), ("expired", True), ("workflow_run", {"id": 9, "head_sha": self.source["sha"]})):
            saved = row[field]
            row[field] = value
            with self.assertRaises(ci.ProofError):
                self.verify(api)
            row[field] = saved
        api.pages.return_value = []
        with self.assertRaises(ci.ProofError):
            self.verify(api)

    def test_proof_tampering_wrong_dependencies_cell_and_asset_digest_are_rejected(self):
        mutations = [lambda p: p["identity"].update(version="1.2.4"), lambda p: p.update(cell="windows-py3.10"),
                     lambda p: p.update(run_attempt=2), lambda p: p.update(ref="refs/pull/1/merge"),
                     lambda p: p["files"]["merge-reviewer-1.2.3.zip"].update(sha256="0" * 64)]
        for mutate in mutations:
            api, _ = self.artifact(mutate)
            with self.assertRaises(ci.ProofError):
                self.verify(api)
        for extra in ("unlisted.exe", "../outside", "folder/file", "C:/outside"):
            api, _ = self.artifact(extra=extra)
            with self.assertRaises(ci.ProofError):
                self.verify(api)

    def test_cross_host_redirect_strips_token(self):
        request = urllib.request.Request("https://api.github.com/example", headers={"Authorization": "Bearer secret"})
        redirected = ci.SafeRedirect().redirect_request(request, None, 302, "", {}, "https://storage.example.com/file")
        self.assertFalse(redirected.has_header("Authorization"))
        with self.assertRaises(ci.ProofError):
            ci.SafeRedirect().redirect_request(request, None, 302, "", {}, "http://storage.example.com/file")

    def test_artifact_downloads_use_json_accept_and_release_assets_use_octet_stream(self):
        api = ci.GitHub("owner/MergeReviewer", "never-sent")
        cases = [("actions/artifacts/20/zip", "application/vnd.github+json"),
                 ("releases/assets/30", "application/octet-stream"),
                 ("https://api.github.com/repos/owner/MergeReviewer/releases/assets/30", "application/octet-stream")]
        for path, accept in cases:
            with self.subTest(path=path), mock.patch.object(api.opener, "open", return_value=io.BytesIO(b"exact binary bytes")) as opened:
                self.assertEqual(api.request(path, raw=True), b"exact binary bytes")
                self.assertEqual(opened.call_args.args[0].get_header("Accept"), accept)

    def test_product_specs_keep_required_matrix_and_canonical_versions(self):
        self.assertEqual([len(ci.SPECS[name]["cells"]) for name in ("GitlabWorkSpace", "MergeReviewer", "ReleaseReviewer", "code-base-llm-wiki")], [3, 2, 2, 2])
        for name, spec in ci.SPECS.items():
            self.assertIn(spec["canonical"], spec["cells"])
        scoped_names = ("GitlabWorkSpace", "MergeReviewer", "ReleaseReviewer", "code-base-llm-wiki")
        if "WorkflowKit" in ci.SPECS:
            scoped_names += ("WorkflowKit",)
        for name in scoped_names:
            scoped = ci.SPECS[name]
            self.assertTrue(all(environment[0] == "Windows" for environment in scoped["cells"].values()), name)
            self.assertTrue(scoped["canonical"].startswith("windows-"), name)

    def test_checksums_cover_exact_asset_inventory_and_bytes(self):
        data = b"archive"
        checksum = f"{ci.digest(data)}  release.zip\n".encode()
        ci.validate_checksums({"release.zip": data, "release.zip.sha256": checksum})
        ci.validate_checksums({"release.zip": data, "SHA256SUMS": checksum})
        for files in ({"release.zip": b"changed", "SHA256SUMS": checksum},
                      {"release.zip": data, "extra": b"", "SHA256SUMS": checksum},
                      {"release.zip": data, "SHA256SUMS": checksum + checksum}):
            with self.assertRaises(ci.ProofError):
                ci.validate_checksums(files)

    def test_attempt_jobs_are_loaded_from_explicit_attempt_endpoints(self):
        api = mock.Mock()
        api.request.return_value = dict(self.run, run_attempt=2)
        def pages(path, key):
            return [dict(self.job, conclusion="failure")] if "/attempts/1/" in path else [self.job]
        api.pages.side_effect = pages
        with mock.patch.object(ci, "verify_artifacts", return_value={"asset": b"bytes"}) as verify:
            ci.verified_run(api, 10, self.source)
            self.assertEqual(verify.call_args.args[3]["windows-py3.14"]["run_attempt"], 2)

    def test_github_pagination_accepts_actual_release_array_and_actions_envelope(self):
        api = ci.GitHub("owner/MergeReviewer", "never-sent")
        with mock.patch.object(api, "request", return_value=[{"name": "release.zip"}]):
            self.assertEqual(api.pages("releases/123/assets", "assets"), [{"name": "release.zip"}])
            with self.assertRaises(ci.ProofError):
                api.pages("actions/runs/10/artifacts", "artifacts")
        with mock.patch.object(api, "request", return_value={"artifacts": [{"id": 1}]}):
            self.assertEqual(api.pages("actions/runs/10/artifacts", "artifacts"), [{"id": 1}])
            with self.assertRaises(ci.ProofError):
                api.pages("releases/123/assets", "assets")

    def test_identity_hashes_committed_bytes_across_lf_and_crlf_checkouts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "skills/merge-reviewer").mkdir(parents=True)
            (root / "skills/merge-reviewer/VERSION").write_bytes(b"1.2.3\r\n")
            (root / "requirements-dev.txt").write_bytes(b"behave>=1.2.6\r\n")
            with mock.patch.object(ci, "git", side_effect=lambda root, *args: "a" * 40), mock.patch.object(ci, "git_blob", return_value=b"committed LF\n"):
                windows = ci.identity(root, "owner/MergeReviewer")
                (root / "requirements-dev.txt").write_bytes(b"behave>=1.2.6\n")
                (root / "skills/merge-reviewer/VERSION").write_bytes(b"1.2.3\n")
                linux = ci.identity(root, "owner/MergeReviewer")
            self.assertEqual(windows, linux)

    def test_runtime_provenance_records_selected_tools_without_environment_dump(self):
        with mock.patch.object(ci, "tool_version", side_effect=lambda root, command: " ".join(command)), mock.patch.object(ci.importlib.metadata, "version", return_value="1.2.3"):
            details = ci.runtime_details(Path("."), "owner/MergeReviewer")
        self.assertEqual(details["packages"], {"behave": "1.2.3", "PyYAML": "1.2.3"})
        self.assertEqual(set(details), {"git", "node", "python", "packages", "fixed_dependencies"})

    def test_workspace_lock_names_and_versions_must_match_manifest(self):
        manifest = {"name": "gitlab-workspace", "version": "1.2.3"}
        locked = dict(manifest, packages={"": dict(manifest)})
        ci.validate_workspace_versions(manifest, locked)
        for field in ("name", "version"):
            for nested in (False, True):
                invalid = copy.deepcopy(locked)
                (invalid["packages"][""] if nested else invalid)[field] = "different"
                with self.assertRaises(ci.ProofError):
                    ci.validate_workspace_versions(manifest, invalid)

    def test_manual_tag_requires_matching_source_still_in_main(self):
        api = mock.Mock()
        api.request.side_effect = [{"object": {"type": "commit", "sha": self.source["sha"]}},
                                   {"merge_base_commit": {"sha": self.source["sha"]}}]
        ci.validate_tag_target(api, "v1.2.3", self.source)
        api.request.side_effect = [{"object": {"type": "commit", "sha": self.source["sha"]}},
                                   {"merge_base_commit": {"sha": "b" * 40}}]
        with self.assertRaises(ci.ProofError):
            ci.validate_tag_target(api, "v1.2.3", self.source)
        with self.assertRaises(ci.ProofError):
            ci.validate_tag_target(api, "v1.2.4", self.source)

    def test_workspace_retains_semver_and_historical_prerelease_policy(self):
        for version, expected in (("1.2.3", False), ("0.1.0", True), ("1.2.3-beta.1", True),
                                  ("1.2.3+build.sha", False), ("1.2.3-beta.1+build.sha", True)):
            self.assertEqual(ci.validate_version("owner/GitlabWorkSpace", version), version)
            self.assertEqual(ci.release_is_prerelease("owner/GitlabWorkSpace", version), expected)
        for version in ("01.2.3", "1.2.3-01", "1.2.3-beta..1", "1.2.3+", "v1.2.3", "1.2.3٢"):
            with self.assertRaises(ci.ProofError):
                ci.validate_version("owner/GitlabWorkSpace", version)
        for repository in ("MergeReviewer", "ReleaseReviewer", "code-base-llm-wiki"):
            with self.assertRaises(ci.ProofError):
                ci.validate_version("owner/" + repository, "1.2.3-beta.1")

    def test_build_metadata_asset_names_are_safe_and_checksum_bound(self):
        filename = "gitlab-workspace-1.2.3+sha.7.vsix"
        data = b"artifact"
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr(filename, data)
        self.assertEqual(ci.unpack_artifact(output.getvalue()), {filename: data})
        ci.validate_checksums({filename: data, "SHA256SUMS": f"{ci.digest(data)}  {filename}\n".encode()})


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.source = {"repository": "owner/MergeReviewer", "sha": "a" * 40, "version": "1.2.3"}
        self.files = {"merge-reviewer-1.2.3.zip": b"release bytes"}
        self.release = {"id": 1, "draft": False, "prerelease": False, "html_url": "https://github.com/owner/MergeReviewer/releases/tag/v1.2.3", "upload_url": "https://uploads.github.com/repos/owner/MergeReviewer/releases/1/assets{?name}"}
        self.assets = [{"id": 2, "name": next(iter(self.files))}]
        self.api = mock.Mock()
        self.api.pages.side_effect = lambda *args: list(self.assets)
        def request(path, method="GET", data=None, **kwargs):
            if path.startswith("git/ref/"):
                return {"object": {"type": "commit", "sha": self.source["sha"]}}
            if path.startswith("releases/tags/"):
                return self.release
            if path.startswith("compare/"):
                return {"merge_base_commit": {"sha": self.source["sha"]}}
            if path == "releases/assets/2":
                return self.files[self.assets[0]["name"]]
            if method == "PATCH":
                self.release["draft"] = False
                return self.release
            raise AssertionError((path, method))
        self.api.request.side_effect = request

    def test_identical_formal_release_is_accepted_without_mutation(self):
        result = ci.publish_assets(self.api, "v1.2.3", self.source, self.files)
        self.assertEqual(result["sha"], self.source["sha"])
        self.assertTrue(all(call.args[1:2] not in [("POST",), ("PATCH",)] for call in self.api.request.call_args_list))

    def test_conflicting_and_missing_formal_assets_are_refused(self):
        with self.assertRaises(ci.ProofError):
            ci.publish_assets(self.api, "v1.2.3", self.source, {next(iter(self.files)): b"changed"})
        self.assets.clear()
        with self.assertRaises(ci.ProofError):
            ci.publish_assets(self.api, "v1.2.3", self.source, self.files)

    def test_completed_draft_can_resume_and_finalize(self):
        self.release["draft"] = True
        ci.publish_assets(self.api, "v1.2.3", self.source, self.files)
        self.assertFalse(self.release["draft"])
        self.assertTrue(any(call.args[1:2] == ("PATCH",) for call in self.api.request.call_args_list))

    def test_partial_draft_upload_resumes_only_missing_assets(self):
        files = {"one.zip": b"one", "two.zip": b"two"}
        assets = []
        release = dict(self.release, draft=True)
        api = mock.Mock()
        api.pages.side_effect = lambda *args: list(assets)
        fail_once = [True]
        def request(path, method="GET", data=None, **kwargs):
            if path.startswith("git/ref/"):
                return {"object": {"type": "commit", "sha": self.source["sha"]}}
            if path.startswith("releases/tags/"):
                return release
            if path.startswith("compare/"):
                return {"merge_base_commit": {"sha": self.source["sha"]}}
            if path.startswith("https://uploads.github.com/"):
                name = path.split("?name=", 1)[1]
                if name == "two.zip" and fail_once[0]:
                    fail_once[0] = False
                    raise ci.ProofError("transient upload failure")
                assets.append({"id": len(assets) + 1, "name": name})
                return assets[-1]
            if path.startswith("releases/assets/"):
                return files[assets[int(path.rsplit("/", 1)[1]) - 1]["name"]]
            if method == "PATCH":
                release["draft"] = False
                return release
            raise AssertionError((path, method))
        api.request.side_effect = request
        with self.assertRaises(ci.ProofError):
            ci.publish_assets(api, "v1.2.3", self.source, files)
        self.assertTrue(release["draft"])
        self.assertEqual([a["name"] for a in assets], ["one.zip"])
        ci.publish_assets(api, "v1.2.3", self.source, files)
        self.assertFalse(release["draft"])
        self.assertEqual([a["name"] for a in assets], ["one.zip", "two.zip"])

    def test_new_release_notes_include_verified_source_ci_and_hash_inventory(self):
        request = self.api.request.side_effect
        created = []
        def create(path, method="GET", data=None, **kwargs):
            if path.startswith("releases/tags/"):
                return None
            if path == "releases/generate-notes":
                return {"body": "Generated changes"}
            if path == "releases" and method == "POST":
                created.append(data)
                return self.release
            return request(path, method, data, **kwargs)
        self.api.request.side_effect = create
        ci.publish_assets(self.api, "v1.2.3", self.source, self.files, proof_run_id=10)
        self.assertTrue(created[0]["draft"])
        self.assertIn(self.source["sha"], created[0]["body"])
        self.assertIn("/actions/runs/10", created[0]["body"])
        self.assertIn(ci.digest(next(iter(self.files.values()))), created[0]["body"])

    def test_existing_workspace_prerelease_flag_must_match_original_policy(self):
        for version, expected in (("0.1.0", True), ("1.2.3-beta.1", True), ("1.2.3+sha", False), ("1.2.3", False)):
            source = dict(self.source, repository="owner/GitlabWorkSpace", version=version)
            self.release["prerelease"] = expected
            ci.publish_assets(self.api, "v" + version, source, self.files)
            self.release["prerelease"] = not expected
            with self.assertRaises(ci.ProofError):
                ci.publish_assets(self.api, "v" + version, source, self.files)


class WorkflowContractTests(unittest.TestCase):
    def test_local_workflow_matches_fixed_matrix_and_guarded_promotion(self):
        root = Path(__file__).resolve().parents[1]
        # Local source checkouts are named by their repository. CI checks out
        # into GITHUB_WORKSPACE, whose basename is also the repository name.
        name = root.name
        if name not in ci.SPECS:
            self.skipTest("Standalone helper fixture has no product workflow")
        spec = ci.SPECS[name]
        text = (root / ".github/workflows" / spec["workflow"]).read_text(encoding="utf-8")
        cells = re.findall(r"^\s+- cell: (\S+)$", text, flags=re.M)
        self.assertEqual(set(cells), set(spec["cells"]))
        self.assertEqual(len(cells), len(spec["cells"]))
        for token in ("workflow_dispatch:", "pull_request:", "      - main", "release_ci.py select",
                      "release_ci.py record", "release_ci.py publish", "retention-days: 14",
                      "include-hidden-files: true", "--fallback-current-run", "needs: [select, build]",
                      "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/')"):
            self.assertIn(token, text)
        before_publish, publish = text.split("\n  publish:\n", 1)
        self.assertNotIn("contents: write", before_publish)
        self.assertIn("contents: write", publish)
        self.assertNotIn("--clobber", text)
        self.assertNotIn("package_release.py", publish)
        self.assertNotIn("npm run package", publish)


if __name__ == "__main__":
    unittest.main()
