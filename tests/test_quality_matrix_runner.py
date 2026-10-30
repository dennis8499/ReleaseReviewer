"""Keep quality-runner failure and report provenance separate."""
import base64
import json
import hashlib
import sys
import tempfile
from pathlib import Path

from support import *

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_quality_matrix


class QualityMatrixJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="quality-matrix-job-", dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name) / "workspace"
        self.jobs = self.workspace / ".release-review-runtime" / "jobs"
        self.jobs.mkdir(parents=True)

    def write_job(self, identifier, **values):
        job = {"request_id": identifier, **values}
        (self.jobs / f"{identifier}.json").write_text(
            json.dumps(job, ensure_ascii=False), encoding="utf-8")
        return job

    def test_failed_job_without_report_retains_error_and_cleanup_fields(self):
        self.write_job("failed", state="failed", updated_at="2026-10-29T12:00:00Z",
                       report_path=None, context_dir="missing-context", error="view rename failed",
                       cleanup_error=None)

        result = run_quality_matrix.completed_job(self.workspace, [])

        self.assertEqual("failed", result["state"])
        self.assertEqual("view rename failed", result["error"])
        self.assertIsNone(result["cleanup_error"])

    def test_report_match_wins_over_a_newer_unpublished_failure(self):
        report = self.workspace / "review-reports" / "release-report.md"
        report.parent.mkdir(parents=True)
        self.write_job("published", state="published", updated_at="2026-10-29T12:00:00Z",
                       report_path=str(report))
        self.write_job("failed", state="failed", updated_at="2026-10-29T12:01:00Z",
                       report_path=None)

        result = run_quality_matrix.completed_job(self.workspace, [report])

        self.assertEqual("published", result["state"])
        self.assertNotIn("_report_matches", result)

    def test_report_hash_matches_exact_utf8_artifact_bytes(self):
        report = self.workspace / "reports" / "release-report.md"
        report.parent.mkdir(parents=True)
        text = "# 上版檢查\n\nTable／FTP／SFTP\n"

        actual_hash, actual_bytes = run_quality_matrix.write_text_exact(report, text)
        expected = text.encode("utf-8")

        self.assertEqual(expected, report.read_bytes())
        self.assertEqual(hashlib.sha256(expected).hexdigest(), actual_hash)
        self.assertEqual(len(expected), actual_bytes)

    def test_local_path_redaction_handles_nested_json_and_non_home_roots(self):
        raw = json.dumps({
            "workspace": r"C:\Users\reviewer\OneDrive\Desktop\ReleaseReviewer",
            "fixture": r"D:\scratch\fixture\workspace",
            "owner": Path.home().name,
        })

        sanitized = run_quality_matrix.redact_local_paths(raw)

        self.assertNotIn("C:\\", sanitized)
        self.assertNotIn("D:\\", sanitized)
        self.assertNotIn(Path.home().name.lower(), sanitized.lower())
        self.assertIn("<LOCAL_PATH>", sanitized)

    def test_report_path_redaction_recomputes_static_integrity_metadata(self):
        body = ("# 上版檢查\n\nRepo: C:\\Users\\reviewer\\OneDrive\\project\n\n"
                "<!-- release-checklist:start -->\n| K-001 | 待確認 |\n"
                "<!-- release-checklist:end -->\n")
        metadata = {
            "repo": r"C:\Users\reviewer\OneDrive\project",
            "report_root": r"C:\Users\reviewer\OneDrive\project\review-reports",
            "static_body_sha256": hashlib.sha256(
                run_quality_matrix._static_report_body(body).encode("utf-8")).hexdigest(),
        }
        footer = "<!-- release-review-report:" + base64.b64encode(
            json.dumps(metadata, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")).encode("utf-8")).decode("ascii") + " -->"

        sanitized = run_quality_matrix.redact_report_paths(body + footer)
        marker = sanitized.split("release-review-report:", 1)[1].split(" -->", 1)[0]
        recovered = json.loads(base64.b64decode(marker))

        self.assertNotIn("C:\\", sanitized)
        self.assertEqual(
            hashlib.sha256(run_quality_matrix._static_report_body(
                sanitized.split("<!-- release-review-report:", 1)[0]).encode("utf-8")).hexdigest(),
            recovered["static_body_sha256"],
        )


if __name__ == "__main__":
    unittest.main()
