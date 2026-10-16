from unittest.mock import patch

from support import *


class PreviewAnalysisTests(RepoTest):
    def test_full_file_analysis_is_shared_across_ranges_and_reset_between_validations(self):
        context, manifest = self.capture()
        draft = draft_for(manifest)
        draft["code_risks"] = [risk_item(manifest)]
        draft["config_changes"] = [config_item(context, manifest)]
        draft["db_changes"] = [db_item(context, manifest)]
        with patch.object(release_report, "preview_analysis", wraps=release_report.preview_analysis) as analyse:
            first = release_report.validate_result(draft, manifest, context)
            self.assertEqual(2, analyse.call_count)
            first["coverage"][0]["evidence"][0]["path"] = "changed-by-consumer"
            analyse.reset_mock()
            second = release_report.validate_result(draft, manifest, context)
            self.assertEqual(2, analyse.call_count)
        self.assertNotEqual(first, second)
        self.assertNotIn("changed-by-consumer", str(second))

    def test_equal_bytes_keep_path_classification_and_full_file_secret_checks(self):
        same = b'{"outer": [{"visible": "StoredValue"}]}\r\n'
        files = {
            "src/shared.py": same,
            "src/shared.json": same,
            "src/shared.yaml": same,
            "src/.env.shared": same,
            "src/private.py": b"ordinary line\n-----BEGIN PRIVATE KEY-----\nHiddenPrivateValue\n",
            "src/key.pem": b"ordinary line\n",
            "src/broken.json": b'{"broken":\n',
            "src/bom.json": b'\xef\xbb\xbf{"hidden": "HiddenBomValue"}\n',
            "src/invalid.py": b'good = "\xff"\r\n',
            "src/credentials.py": b'PASSWORD = "HiddenPassword"\nurl = "https://user:HiddenPass@host/path"\n',
        }
        for path, content in files.items():
            write(self.repo, path, content)
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "preview classifications")
        context, manifest = self.capture()
        proofs = [citation(manifest, "merge-preview", path, 1) for path in files]
        with patch.object(release_report, "preview_analysis", wraps=release_report.preview_analysis) as analyse:
            result = release_report.validate_citations(proofs, manifest, context)
        self.assertEqual(9, analyse.call_count)
        by_path = {item["path"]: item for item in result}
        self.assertEqual(['1: {"outer": [{"visible": "StoredValue"}]}'], by_path["src/shared.py"]["excerpt"])
        self.assertEqual(["outer", "visible"], by_path["src/shared.json"]["configuration_keys"])
        for path in ("src/shared.yaml", "src/.env.shared", "src/broken.json", "src/bom.json"):
            self.assertEqual([], by_path[path]["configuration_keys"])
            self.assertNotIn("excerpt", by_path[path])
        for path in ("src/private.py", "src/key.pem"):
            self.assertIn("私鑰", by_path[path]["snapshot_note"])
            self.assertNotIn("excerpt", by_path[path])
        self.assertEqual(['1: good = "\ufffd"'], by_path["src/invalid.py"]["excerpt"])
        self.assertEqual(["1: [含認證或連線設定的行已省略]"], by_path["src/credentials.py"]["excerpt"])
        uri = release_report.validate_citations([citation(manifest, "merge-preview", "src/credentials.py", 2)],
                                               manifest, context)[0]
        self.assertEqual(['2: url = "https://[已遮蔽]@host/path"'], uri["excerpt"])
        no_range = release_report.validate_citations([citation(manifest, "merge-preview", "src/shared.py")],
                                                    manifest, context)[0]
        self.assertNotIn("excerpt", no_range)

    def test_cached_analysis_cannot_bypass_later_citation_checks(self):
        context, manifest = self.capture()
        valid = citation(manifest, "merge-preview", "src/service.py", 7, 8)
        for change in ({"ref": "0" * 40}, {"repo": "missing"}, {"path": "../src/service.py"},
                       {"source": "head"}, {"line_end": 99999}, {"line_start": True}):
            with self.subTest(change=change):
                invalid = {**valid, **change}
                with self.assertRaises(release_report.DraftValidationError):
                    release_report.validate_citations([valid, invalid], manifest, context)

    def test_configuration_key_results_do_not_mutate_the_shared_analysis(self):
        content = b'{"root": [{"nested": 1}]}\n'
        analysis = release_report.preview_analysis(content, "config.json")
        first = release_report.preview_snapshot(content, "config.json", None, None, analysis=analysis)
        first["configuration_keys"].append("consumer mutation")
        second = release_report.preview_snapshot(content, "config.json", 1, 1, analysis=analysis)
        self.assertEqual(["nested", "root"], second["configuration_keys"])


if __name__ == "__main__":
    unittest.main()
