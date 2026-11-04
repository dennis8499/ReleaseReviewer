"""Keep the entry base/final comparison explicit without rewriting legacy v3."""
from support import *
import review_flow as flow


class ReleaseBaselineTests(RepoTest):
    def test_new_risk_requires_a_base_final_explanation(self):
        context, manifest = self.capture()
        draft = draft_for(manifest)
        risk = risk_item(manifest)
        risk.pop("baseline_reason")
        draft["code_risks"] = [risk]
        errors = flow.diagnostics(draft, manifest)["errors"]
        self.assertTrue(any(row["pointer"] == "/code_risks/0/baseline_reason" for row in errors))
        with self.assertRaisesRegex(release_report.DraftValidationError, "baseline_reason"):
            release_report.validate_result(draft, manifest, context)

    def test_final_only_evidence_cannot_substitute_for_base_on_existing_path(self):
        context, manifest = self.capture()
        draft = draft_for(manifest)
        risk = risk_item(manifest)
        risk["evidence"] = [row for row in risk["evidence"] if row["source"] != "base"]
        draft["code_risks"] = [risk]
        with self.assertRaisesRegex(release_report.DraftValidationError, "base"):
            release_report.validate_result(draft, manifest, context)

    def test_legacy_context_keeps_its_original_risk_contract(self):
        context, manifest = self.capture()
        manifest.pop("risk_baseline_policy")
        draft = draft_for(manifest)
        risk = risk_item(manifest)
        risk.pop("baseline_reason")
        draft["code_risks"] = [risk]
        result = release_report.validate_result(draft, manifest, context)
        self.assertEqual(1, result["blocker_count"])


if __name__ == "__main__":
    unittest.main()
