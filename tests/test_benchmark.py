import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/benchmark_review.py"
SPEC = importlib.util.spec_from_file_location("benchmark_review", SCRIPT)
benchmark = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = benchmark
SPEC.loader.exec_module(benchmark)


class BenchmarkTests(unittest.TestCase):
    def test_digest_ignores_only_run_times_and_replaced_locations(self):
        first = {"generated_at": "now", "repo": "C:/one/repo", "content": {"line": 1, "text": "same"}}
        second = {"generated_at": "later", "repo": "C:/two/repo", "content": {"line": 1, "text": "same"}}
        digest = benchmark.semantic_digest(first, [("C:/one", "<fixture>")])
        self.assertEqual(digest, benchmark.semantic_digest(second, [("C:/two", "<fixture>")]))
        second["content"]["text"] = "changed"
        self.assertNotEqual(digest, benchmark.semantic_digest(second, [("C:/two", "<fixture>")]))

    def sample(self, variant, seconds, digest="same", success=True):
        metric = dict(wall_seconds=seconds, git_subprocesses=2, helper_commands=1,
                      output_bytes=10, artifact_bytes=0)
        return dict(variant=variant, scenario="normal", success=success, semantic_digest=digest,
                    total=metric, stages={stage: metric.copy() for stage in benchmark.STAGES})

    def test_summary_uses_medians_and_requires_semantic_equivalence(self):
        samples = [self.sample("baseline", value) for value in (1, 20, 3)]
        samples += [self.sample("current", value) for value in (1, 1.5, 2)]
        result = benchmark.summarize(samples)
        self.assertTrue(result["success"])
        self.assertEqual(result["scenarios"]["normal"]["speedup"], 2)
        samples[-1]["semantic_digest"] = "changed"
        result = benchmark.summarize(samples)
        self.assertFalse(result["success"])
        self.assertFalse(result["scenarios"]["normal"]["success"])
        self.assertNotIn("speedup", result["scenarios"]["normal"])

    def test_failed_or_missing_worker_cannot_be_reported_as_success(self):
        for samples in ([], [self.sample("baseline", 1)],
                        [self.sample("baseline", 1), self.sample("current", 0.01, success=False)]):
            with self.subTest(samples=samples):
                result = benchmark.summarize(samples)
                self.assertFalse(result["success"])
                self.assertTrue(all("speedup" not in item for item in result["scenarios"].values()))

    def test_worker_timeout_is_a_failed_sample(self):
        with mock.patch.object(benchmark.subprocess, "run", side_effect=benchmark.subprocess.TimeoutExpired("worker", 600)):
            result = benchmark.launch_worker(Path("unused"), "normal")
        self.assertFalse(result["success"])
        self.assertIn("TimeoutExpired", result["error"])

    def test_isolated_same_variant_smoke_run_has_equal_digest(self):
        skill = SCRIPT.parents[1] / "skills/release-reviewer"
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "benchmark.json"
            code = benchmark.main(["--baseline-skill-dir", str(skill), "--runs", "1",
                                   "--scenario", "no-diff", "--output", str(output)])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text(encoding="utf-8"))
        self.assertTrue(result["summary"]["success"])
        self.assertEqual(len(result["samples"]), 2)
        for sample in result["samples"]:
            self.assertEqual(sample["stages"]["capture"]["helper_commands"], 1)
            self.assertEqual(sample["stages"]["publish"]["helper_commands"], 1)
            self.assertGreater(sample["stages"]["validate"]["git_subprocesses"], -1)
            self.assertGreater(sample["stages"]["publish"]["artifact_bytes"], 0)
            self.assertGreaterEqual(sample["stages"]["publish"]["wall_seconds"], 0)


if __name__ == "__main__":
    unittest.main()
