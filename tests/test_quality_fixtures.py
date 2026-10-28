import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("quality_sources", Path(__file__).with_name("create_quality_fixture.py"))
quality = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quality)


class QualityFixtureTests(unittest.TestCase):
    def test_canaries_have_real_failures_safe_controls_and_parent_only_oracles(self):
        with tempfile.TemporaryDirectory(prefix="review-quality-seeds-") as temporary:
            root = Path(temporary)
            for case in quality.CASES:
                fixture = quality.create(root / case, case)
                repo = Path(fixture["project"])
                code = {}
                exec((repo / "app.py").read_text(encoding="utf-8"), code)
                self.assertTrue((root / case / "oracle.json").is_file())
                self.assertFalse((Path(fixture["workspace"]) / "oracle.json").exists())
                if case in ("p1", "gap"):
                    with self.assertRaises(KeyError):
                        code["checkout"](12)
                else:
                    self.assertEqual(12, code["checkout"](12))
                if case == "net-zero":
                    self.assertEqual("", quality.git(repo, "diff", "main", "feature/review"))
                if case == "p0":
                    startup = {}
                    exec((repo / "bootstrap.py").read_text(encoding="utf-8"), startup)
                    calls = []
                    class Database:
                        def execute(self, command):
                            calls.append(command)
                    startup["startup"](Database())
                    self.assertEqual("DELETE FROM orders", calls[0])
                if case == "gap":
                    self.assertEqual("", quality.git(Path(fixture["workspace"]) / "billing-consumer", "rev-parse", "--verify", "HEAD", check=False))
                if case == "unused-setting":
                    self.assertNotIn("optional_endpoint", (repo / "app.py").read_text(encoding="utf-8"))
                if case == "provider-unknown":
                    class Bus:
                        def publish(self, topic, payload):
                            return topic, payload
                    self.assertEqual(("orders.created", {"amount": 12}), code["publish_event"](Bus(), 12))
                    self.assertNotIn("kafka", (repo / "app.py").read_text(encoding="utf-8").lower())
