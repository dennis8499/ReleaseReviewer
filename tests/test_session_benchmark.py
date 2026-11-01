from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/benchmark_session.py"
SPEC = importlib.util.spec_from_file_location("benchmark_session", SCRIPT)
benchmark = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = benchmark
SPEC.loader.exec_module(benchmark)


class SessionBenchmarkTests(unittest.TestCase):
    def test_helper_regression_gate_allows_at_most_five_percent(self):
        for scenario in ("repository", "group", "no-change"):
            with self.subTest(scenario=scenario):
                self.assertEqual(1.05, benchmark.helper_target_ratio(scenario))

    def test_unknown_scenario_has_no_implicit_performance_gate(self):
        with self.assertRaisesRegex(ValueError, "unknown benchmark scenario"):
            benchmark.helper_target_ratio("large")

    def test_results_must_be_written_outside_measured_payloads(self):
        root = Path.cwd() / ".benchmark-path-test"
        baseline, candidate = root / "baseline", root / "candidate"
        output = root / "results.json"
        self.assertEqual(output.resolve(), benchmark.validate_output_path(output, (baseline, candidate)))
        with self.assertRaisesRegex(ValueError, "outside the baseline and candidate"):
            benchmark.validate_output_path(baseline / "results.json", (baseline, candidate))


if __name__ == "__main__":
    unittest.main()
