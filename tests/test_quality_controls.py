import copy
from unittest.mock import patch

from support import *
import review_runtime
import merge_parent_coverage as parents


class QualityControlTests(RepoTest):
    def test_summary_v2_size_does_not_scale_with_full_catalog_inventory(self):
        context, manifest = self.capture()
        expanded = copy.deepcopy(manifest)
        source = next(iter(expanded["repositories"][0]["snapshot"]["sources"].values()))
        source["entries"] *= 1000
        old = context_helper.capture_summary(expanded)
        compact = context_helper.capture_summary_v2(expanded)
        self.assertEqual("ReleaseReviewCaptureSummary/v2", compact["schema"])
        self.assertNotIn("sources", compact["repositories"][0]["snapshot"])
        self.assertLess(len(json.dumps(compact)), len(json.dumps(old)) / 10)
        self.assertEqual(manifest["repositories"], context_helper.capture_summary(manifest)["repositories"])

    def test_invalid_local_and_shallow_inputs_never_fetch(self):
        git(self.repo, "remote", "add", "origin", str(self.root / "not-contacted.git"))
        with patch.object(context_helper.git, "fetch_inputs", side_effect=AssertionError("network must not start")):
            with self.assertRaisesRegex(Exception, "找不到基礎版本"):
                self.capture(base="missing", head="origin/feature")
            with patch.object(context_helper.git, "is_shallow_repository", return_value=True):
                with self.assertRaisesRegex(Exception, "完整歷史"):
                    self.capture(base="main", head="origin/feature")

    def test_authored_passwd_and_multiline_private_keys_are_redacted(self):
        value = 'passwd=SYNTHETIC-CREDENTIAL\n-----BEGIN PRIVATE KEY-----\nSYNTHETIC-KEY-DATA\n-----END PRIVATE KEY-----'
        cleaned = release_report.redact({"summary": value, "nested": [value]})
        self.assertNotIn("SYNTHETIC", json.dumps(cleaned))

    def test_search_pages_keep_all_matches_and_allow_checksum_verified_reread(self):
        context, manifest = self.capture()
        requests = [{"action": "search", "source": "head", "query": "def"}]
        full = evidence.batch(manifest, context, requests)
        page = review_runtime.page(full, context, size=1)
        self.assertFalse(page["result"]["results"][0]["result"]["view"]["view_complete"])
        self.assertEqual(full, review_runtime.load_view(context, page["artifact"]))
        identifier = full["results"][0]["result"]["search_id"]
        self.assertEqual(len(full["results"][0]["result"]["matches"]),
                         len(evidence.validate_search(context, manifest, identifier, source="head")["matches"]))

    def test_parent_gate_has_no_effect_for_no_final_changes_and_preserves_incomplete(self):
        merge = {"sha": "a" * 40, "parent_views": [{"position": 1, "sha": "b" * 40,
                    "changes": [{"path": "app.py", "old_mode": "100644"}]}]}
        self.assertEqual([], parents.required([], [merge]))
        views = parents.required([{"path": "app.py"}], [merge])
        def require(ok, message):
            if not ok:
                raise ValueError(message)
        self.assertFalse(parents.validate(views, parents.unfinished(views), lambda rows: None,
                                          final_ref="c" * 40, parent_repo="entry", require=require))
        with self.assertRaisesRegex(ValueError, "final-version"):
            parents.validate(views, [{"id": views[0]["id"], "status": "reviewed", "reason": "checked", "evidence": []}],
                             lambda rows: None, final_ref="c" * 40, parent_repo="entry", require=require)
