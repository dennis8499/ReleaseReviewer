import importlib.util
from zipfile import ZipFile

from support import *

spec = importlib.util.spec_from_file_location("package_release", ROOT / "scripts/package_release.py")
packaging = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packaging)


class PackageTests(RepoTest):
    def test_crlf_text_payload_is_canonical_and_binary_is_exact(self):
        import shutil
        copied = self.root / "crlf-skill"
        shutil.copytree(SKILL, copied)
        original = (copied / "SKILL.md").read_bytes().replace(b"\r\n", b"\n")
        (copied / "SKILL.md").write_bytes(original.replace(b"\n", b"\r\n"))
        (copied / "fixture.bin").write_bytes(b"\x00\r\n\xff")
        archive = packaging.build_release(copied, self.root / "canonical")
        with ZipFile(archive) as packaged:
            self.assertEqual(original, packaged.read("release-reviewer/SKILL.md"))
            self.assertEqual(b"\x00\r\n\xff", packaged.read("release-reviewer/fixture.bin"))

    def test_deterministic_zip_contains_only_installable_skill(self):
        first = packaging.build_release(SKILL, self.root / "one")
        second = packaging.build_release(SKILL, self.root / "two")
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertTrue(first.with_suffix(".zip.sha256").is_file())
        with ZipFile(first) as archive:
            names = archive.namelist()
            self.assertTrue(all(n.startswith("release-reviewer/") for n in names))
            self.assertFalse(any("tests/" in n or "__pycache__" in n for n in names))
            self.assertIn("release-reviewer/scripts/git_support.py", names)

    def test_packaged_capabilities_hash_matches_the_canonical_installed_payload(self):
        archive = packaging.build_release(SKILL, self.root / "capability-dist")
        with ZipFile(archive) as zipped:
            zipped.extractall(self.root / "capability-install")
        script = self.root / "capability-install/release-reviewer/scripts/review_session.py"
        result = python(script, "capabilities", "--workspace", self.root)
        self.assertEqual(0, result.returncode, result.stderr)
        capability = json.loads(result.stdout)
        self.assertEqual((SKILL / "VERSION").read_text(encoding="utf-8").strip(), capability["skill_version"])
        self.assertEqual(64, len(capability["payload_sha256"]))
        self.assertTrue(capability["features"]["read_window"])

    def test_unpacked_skill_captures_and_publishes_without_merge_reviewer(self):
        archive = packaging.build_release(SKILL, self.root / "dist")
        with ZipFile(archive) as zipped:
            zipped.extractall(self.root / "standalone")
        scripts = self.root / "standalone/release-reviewer/scripts"
        result = python(scripts / "git_review_context.py", "--workspace", self.repo,
                        "--project", self.repo, "--base", "main", "--head", "feature", "--pretty")
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads(result.stdout)
        context = Path(manifest["context_dir"])
        self.contexts.append(context)
        result = python(scripts / "release_report.py", "--context-dir", context,
                        "--result", save_draft(context, draft_for(manifest)))
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual({"markdown_report"}, set(output))
        report = Path(output["markdown_report"])
        self.assertTrue(report.is_file())
        self.assertEqual(self.repo / "review-reports", report.parent)
        self.assertEqual([report], list((self.repo / "review-reports").glob("release-review-*.md")))
        self.assertFalse(context.exists())

    def test_unpacked_session_reads_worklist_and_publishes(self):
        archive = packaging.build_release(SKILL, self.root / "session-dist")
        with ZipFile(archive) as zipped:
            zipped.extractall(self.root / "session-standalone")
        script = self.root / "session-standalone/release-reviewer/scripts/review_session.py"
        runtime = self.root / "session-runtime"
        started = python(script, "start", "--workspace", self.root, "--project", self.repo,
                         "--base", "main", "--head", "feature", "--runtime-root", runtime)
        self.assertEqual(0, started.returncode, started.stderr)
        start = json.loads(started.stdout)
        context = Path(start["context_dir"])
        args = ["--runtime-root", runtime, "--request-id", start["request_id"]]
        step = python(script, "step", *args, "--worklist", context / "review-worklist.json", "--response-format", "auto")
        self.assertEqual(0, step.returncode, step.stderr)
        response = json.loads(step.stdout)
        self.assertTrue(response.get("complete", response.get("evidence_complete")))
        manifest = json.loads((context / "manifest.json").read_text(encoding="utf-8"))
        save_draft(context, draft_for(manifest))
        checked = python(script, "check", *args)
        self.assertEqual(0, checked.returncode, checked.stderr)
        finished = python(script, "finish", *args)
        self.assertEqual(0, finished.returncode, finished.stderr)
        published = json.loads(finished.stdout)
        self.assertTrue(Path(published["report_path"]).is_file())
        self.assertFalse(context.exists())
