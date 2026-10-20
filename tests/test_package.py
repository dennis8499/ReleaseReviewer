import importlib.util
from zipfile import ZipFile

from support import *

spec = importlib.util.spec_from_file_location("package_release", ROOT / "scripts/package_release.py")
packaging = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packaging)


class PackageTests(RepoTest):
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
