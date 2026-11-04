"""Test isolation evidence and independent scoring without making model calls."""
import copy
import hashlib
import itertools
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import quality_isolation as isolation
import score_quality_matrix as scoring


class IsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="quality-isolation-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = self.root / "profile"
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()

    def test_dedicated_profile_preserves_user_environment_and_copies_no_credentials(self):
        original = dict(os.environ)
        env = isolation.profile_environment(self.profile)
        self.assertEqual(str(self.profile.resolve()), env["CODEX_HOME"])
        self.assertEqual(original, dict(os.environ))
        self.assertFalse((self.profile / "auth.json").exists())
        with self.assertRaises(ValueError):
            isolation.profile_environment(Path(original.get("CODEX_HOME", Path.home() / ".codex")))

    @unittest.skipUnless(os.name == "nt", "Windows npm launcher")
    def test_npm_launcher_keeps_metacharacters_as_literal_arguments(self):
        wrapper = self.root / "codex.cmd"
        launcher = self.root / "node_modules/@openai/codex/bin/codex.js"
        launcher.parent.mkdir(parents=True)
        launcher.write_text("// transport fixture", encoding="utf-8")
        argument = r"C:\fixture&literal\report%data%.md"
        with patch.object(isolation.shutil, "which", side_effect=lambda name: "node.exe" if name == "node.exe" else None):
            command = isolation.command(str(wrapper), ["exec", "--output-last-message", argument])
        self.assertEqual("node.exe", command[0])
        self.assertEqual(argument, command[-1])
        self.assertNotIn("/c", command)

    def test_unowned_config_and_later_config_changes_are_rejected(self):
        self.profile.mkdir()
        config = self.profile / "config.toml"
        config.write_text("user setting", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unowned"):
            isolation.profile_environment(self.profile)
        self.assertEqual("user setting", config.read_text(encoding="utf-8"))
        config.unlink()
        isolation.profile_environment(self.profile)
        config.write_text("changed during review", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "changed"):
            isolation.profile_environment(self.profile)

    def test_actual_enabled_inventory_must_match_condition_before_and_after(self):
        selected = self.workspace / ".agents/skills/release-reviewer/SKILL.md"
        external = {"name": "unrelated", "path": str(self.root / "global/SKILL.md"), "enabled": True, "scope": "user"}
        allowed = {"name": "release-reviewer", "path": str(selected), "enabled": True, "scope": "repo"}
        disabled = {**external, "enabled": False}
        with patch.object(isolation, "skill_inventory", side_effect=[[external, allowed], [disabled, allowed], [disabled, allowed]]):
            env, evidence = isolation.prepare_isolation("unused", self.workspace, self.profile, selected)
            proof = isolation.public_evidence(isolation.finish_isolation("unused", self.workspace, self.profile, env, evidence))
        self.assertTrue(isolation.proof_valid(proof))
        self.assertNotIn(str(self.root), json.dumps(proof))
        proof["after"].append({"enabled": True, "path_key": "f" * 64})
        self.assertFalse(isolation.proof_valid(proof))

    def test_no_skill_preflight_stops_if_a_global_skill_stays_enabled(self):
        unwanted = {"name": "global", "path": str(self.root / "global/SKILL.md"), "enabled": True}
        with patch.object(isolation, "skill_inventory", return_value=[unwanted]):
            with self.assertRaisesRegex(ValueError, "isolation failed"):
                isolation.prepare_isolation("unused", self.workspace, self.profile)


class IndependentScoringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="independent-scores-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "reports").mkdir()
        self.cases = ("preexisting", "new-trigger", "deleted-endpoint", "gap")
        self.conditions = ("no-skill", "v0.9.0", "candidate")
        self.matrix = {"schema": "ReleaseReviewerBlindQualityMatrix/v1", "complete": True,
            "runs_per_cell": 1, "models": {"fixture-model": "high"}, "cases": list(self.cases),
            "conditions": list(self.conditions), "samples": [],
            "baseline_skill_sha256": "b" * 64, "candidate_skill_sha256": "c" * 64}
        self.reviews = {"schema": "ReleaseReviewerIndependentScores/v1", "independent": True,
                        "reviewer": "independent fixture grader", "reviews": []}
        for condition, case in itertools.product(self.conditions, self.cases):
            identifier = f"fixture-model-{condition}-{case}-01"
            relative = f"reports/{identifier}.md"
            payload = ("Synthetic scoring mechanics fixture: " + identifier).encode("utf-8")
            (self.root / relative).write_bytes(payload)
            checksum = hashlib.sha256(payload).hexdigest()
            active = [] if condition == "no-skill" else [{"name": "release-reviewer", "enabled": True, "path_key": "1" * 64}]
            proof = {"schema": "ReviewerSkillIsolation/v1", "proven": True, "config_sha256": "2" * 64,
                     "expected_skill_key": None if condition == "no-skill" else "1" * 64,
                     "before": copy.deepcopy(active), "after": copy.deepcopy(active)}
            installed = None if condition == "no-skill" else ("c" * 64 if condition == "candidate" else "b" * 64)
            self.matrix["samples"].append({"id": identifier, "model": "fixture-model", "condition": condition,
                "case": case, "run": 1, "report_path": relative, "report_sha256": checksum,
                "exit_code": 0, "report_created": True, "secret_leak": False,
                "job_state": "published", "cleanup_verified": True, "skill_isolation": proof,
                "report_receipt_verified": condition != "no-skill",
                "installed_skill_sha256": installed, "installed_skill_sha256_after": installed})
            self.reviews["reviews"].append({"id": identifier, "report_sha256": checksum,
                "criteria": {name: True for name in scoring.CRITERIA},
                "rationale": "Synthetic unit fixture for hash/coverage gates; this is not a live semantic acceptance score."})

    def evaluate(self):
        return scoring.evaluate(self.matrix, self.reviews, self.root, required_models=("fixture-model",),
            required_cases=self.cases, required_conditions=self.conditions, minimum_runs=1)

    def test_complete_independent_scores_can_satisfy_the_configured_matrix(self):
        result = self.evaluate()
        self.assertTrue(result["semantic_scoring_complete"])
        self.assertTrue(result["acceptance_complete"])
        self.assertTrue(result["acceptance_passed"])
        self.assertEqual(0, result["semantic_cells"]["fixture-model/preexisting"]["candidate_score_delta"]["no-skill"])

    def test_missing_review_and_null_scores_never_count_as_passes(self):
        self.reviews["reviews"].pop()
        result = self.evaluate()
        self.assertFalse(result["acceptance_complete"])
        self.assertIsNone(result["samples"][-1]["semantic_score"])
        self.assertIsNone(result["semantic_cells"]["fixture-model/gap"]["candidate_score_delta"]["no-skill"])
        self.reviews["reviews"][0]["criteria"]["coverage_honest"] = None
        with self.assertRaisesRegex(ValueError, "booleans"):
            self.evaluate()

    def test_false_blocker_false_completion_or_lost_gap_reject_candidate(self):
        target = next(row for row in self.reviews["reviews"] if "-candidate-" in row["id"])
        for name in ("no_false_blockers", "coverage_honest", "required_gaps_preserved"):
            with self.subTest(criterion=name):
                target["criteria"][name] = False
                result = self.evaluate()
                self.assertTrue(result["acceptance_complete"])
                self.assertFalse(result["acceptance_passed"])
                self.assertLess(result["semantic_cells"]["fixture-model/preexisting"]["candidate_score_delta"]["no-skill"], 0)
                target["criteria"][name] = True

    def test_global_isolation_claim_cannot_replace_inventory_proof(self):
        self.matrix["no_skill_isolation_proven"] = True
        self.matrix["samples"][0]["skill_isolation"]["after"] = None
        self.assertFalse(self.evaluate()["acceptance_complete"])

    def test_changed_report_or_selected_payload_invalidates_acceptance(self):
        sample = self.matrix["samples"][-1]
        sample["installed_skill_sha256_after"] = "f" * 64
        self.assertFalse(self.evaluate()["acceptance_passed"])
        sample["installed_skill_sha256_after"] = sample["installed_skill_sha256"]
        (self.root / sample["report_path"]).write_text("changed", encoding="utf-8")
        self.assertFalse(self.evaluate()["acceptance_complete"])

    def test_missing_required_cell_and_self_attested_nonindependent_scores_are_rejected(self):
        self.reviews["independent"] = False
        with self.assertRaisesRegex(ValueError, "independent reviewer"):
            self.evaluate()
        self.reviews["independent"] = True
        removed = self.matrix["samples"].pop()
        self.reviews["reviews"] = [r for r in self.reviews["reviews"] if r["id"] != removed["id"]]
        self.assertFalse(self.evaluate()["acceptance_complete"])

    def test_review_file_is_bound_to_exact_matrix_bytes(self):
        matrix, scores = self.root / "matrix.json", self.root / "scores.json"
        matrix.write_text(json.dumps(self.matrix), encoding="utf-8")
        self.reviews["matrix_sha256"] = "0" * 64
        scores.write_text(json.dumps(self.reviews), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "different matrix"):
            scoring.score_files(matrix, scores)
        self.reviews["matrix_sha256"] = hashlib.sha256(matrix.read_bytes()).hexdigest()
        scores.write_text(json.dumps(self.reviews), encoding="utf-8")
        result = scoring.score_files(matrix, scores, required_models=("fixture-model",),
            required_cases=self.cases, required_conditions=self.conditions, minimum_runs=1)
        self.assertTrue(result["acceptance_passed"])


if __name__ == "__main__":
    unittest.main()
