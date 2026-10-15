from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/release-reviewer"
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))
import evidence
import git_review_context as context_helper
import release_report
import review_session


def command_env():
    return {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": "", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_DATE": "2026-10-10T00:00:00+00:00",
            "GIT_COMMITTER_DATE": "2026-10-10T00:00:00+00:00"}


def git(repo, *args, check=True):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", env=command_env(), timeout=30)
    if check and result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def python(script, *args):
    return subprocess.run([sys.executable, "-X", "utf8", "-B", str(script), *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=command_env(), timeout=60)


def write(repo, path, value):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, bytes):
        target.write_bytes(value)
    else:
        target.write_text(value, encoding="utf-8", newline="\n")


def init_repo(root, name="repo"):
    repo = root / name
    git(root, "init", "-b", "main", str(repo))
    git(repo, "config", "user.name", "Release Reviewer Test")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "core.autocrlf", "false")
    git(repo, "config", "commit.gpgsign", "false")
    git(repo, "config", "core.hooksPath", str(root / "no-hooks"))
    return repo


BASE_FILES = {
    "src/service.py": 'def average(total, count):\n    if count == 0:\n        return 0\n    return total / count\n',
    "src/existing.py": 'import os\n\ndef read_orders(db):\n    timeout = os.getenv("EXISTING_TIMEOUT", "30")\n    return db.execute("SELECT id FROM CoreDb.dbo.Orders")\n',
    "src/settings.json": '{"existing_timeout": 30}\n',
    "schema.sql": "CREATE TABLE BillingDb.dbo.InvoiceTax (tax_rate DECIMAL(5, 2));\n",
}
HEAD_FILES = {
    "src/service.py": 'import json\nimport os\nfrom pathlib import Path\n\nSETTINGS = json.loads(Path(__file__).with_name("settings.json").read_text())\n\ndef average(total, count):\n    return total / count\n\ndef billing_timeout():\n    return SETTINGS["billing_timeout_seconds"]\n\ndef invoice_url():\n    return os.getenv("INVOICE_API_URL", "http://invoice.internal")\n\ndef discount():\n    return SETTINGS["discount_rate"]\n\ndef query_tax(db):\n    return db.execute("SELECT tax_rate FROM BillingDb.dbo.InvoiceTax")\n\ndef read_orders_again(db):\n    return db.execute("SELECT id FROM CoreDb.dbo.Orders")\n',
    "src/settings.json": '{"existing_timeout": 30, "discount_rate": 0.1}\n',
}


def fixture(root):
    repo = init_repo(root)
    for path, value in BASE_FILES.items():
        write(repo, path, value)
    git(repo, "add", ".")
    git(repo, "commit", "-m", "baseline")
    git(repo, "switch", "-c", "feature")
    for path, value in HEAD_FILES.items():
        write(repo, path, value)
    git(repo, "add", ".")
    git(repo, "commit", "-m", "release changes")
    return repo


def capture(repo, mode="merge", base="main", head="feature", workspace=None, workspace_file=None):
    args = context_helper.parse_args(["--workspace", str(workspace or repo), "--project", str(repo),
                                     "--base", base, "--head", head] +
                                     (["--workspace-file", str(workspace_file)] if workspace_file else []))
    manifest = context_helper.build_manifest(args)
    return Path(manifest["context_dir"]), manifest


def citation(manifest, source, path, start=None, end=None, ref=None):
    refs = {"base": manifest["base_sha"], "head": manifest["head_sha"],
            "merge-base": manifest["merge_base"],
            "merge-preview": (manifest.get("merge_preview") or {}).get("tree_sha")}
    result = {"repo": "entry", "source": source, "ref": ref or refs[source], "path": path}
    if start is not None:
        result.update({"line_start": start, "line_end": end if end is not None else start})
    return result


def draft_for(manifest):
    # Setup utility only. Dedicated tests assert coverage against literal paths and versions.
    coverage = []
    for path, pairs in release_report.expected_coverage(manifest).items():
        coverage.append({"path": path, "status": "reviewed",
                         "evidence": [citation(manifest, source, path, ref=ref)
                                      for source, ref in sorted(pairs)]})
    final = "merge-preview" if (manifest.get("merge_preview") or {}).get("status") == "clean" else "head"
    dependency_evidence = [citation(manifest, final, "src/service.py")] if manifest["changed_files"] else []
    return {"schema": "ReleaseReviewDraft/v3", "summary": "測試固定版本的上板檢查。",
            "coverage": coverage, "code_risks": [], "config_changes": [], "db_changes": [],
            "external_changes": [], "supporting_coverage": [], "functional_chain": [], "chain_gaps": [],
            "dependency_coverage": [{"kind": kind, "status": "reviewed", "reason": "已檢視固定變更與依賴使用。",
                                      "evidence": dependency_evidence} for kind in ("config", "db", "ftp", "sftp", "redis", "kafka", "other")],
            "next_steps": ["開發核對程式風險；維運核對上板準備。"],
            "tests_executed": [], "tests_not_executed": ["本次未執行專案測試、部署或 DB 查詢。"],
            "limitations": []}


def baseline_search(context, manifest, query):
    return evidence.record_search(context, evidence.search(manifest, context, "base", query))


def config_item(context, manifest, name="INVOICE_API_URL", status="external"):
    source = release_report.final_version(manifest)[0]
    return {"id": "C-001", "kind": "environment-variable", "name": name,
            "description": "新增讀取；未提供時有預設值。",
            "baseline_reason": "搜尋基礎版本全部檔案未找到此變數的執行程式使用。",
            "baseline_searches": [baseline_search(context, manifest, name)],
            "recommendation": "維運確認是否需要覆寫預設值；環境值未查證。",
            "evidence": [citation(manifest, source, "src/service.py", 14)],
            "definition_status": status, "definition_evidence": []}


def db_item(context, manifest, resolution="identified"):
    source = release_report.final_version(manifest)[0]
    return {"id": "D-001", "kind": "table", "name": "BillingDb.dbo.InvoiceTax",
            "connection": "傳入的 DB 連線", "resolution": resolution,
            "description": "查詢發票稅率。",
            "baseline_reason": "基礎版本命中 schema 定義，沒有執行程式使用；這次首次查詢。",
            "baseline_searches": [baseline_search(context, manifest, "InvoiceTax")],
            "recommendation": "確認此連線可使用該資料表；本次未查證環境建置狀態。",
            "evidence": [citation(manifest, source, "src/service.py", 20)]}


def risk_item(manifest):
    source = release_report.final_version(manifest)[0]
    return {"id": "R-001", "priority": "P1", "title": "數量為零時平均金額計算會失敗",
            "blocking_reason": "正式統計流程允許零數量，本次移除防護會使該必要流程中斷。",
            "trigger": "以數量 0 計算平均金額。",
            "impact": "原本回傳零的流程改成除以零例外。",
            "recommendation": "開發恢復零數量的處理。",
            "scenario_source": "code-derived",
            "evidence": [citation(manifest, source, "src/service.py", 7, 8),
                         citation(manifest, "base", "src/service.py", 1, 4)]}


def save_draft(context, draft):
    path = context / "review-result-draft.json"
    path.write_text(json.dumps(draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


class RepoTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="release-reviewer-test-")).resolve()
        self.repo = fixture(self.root)
        self.contexts = []

    def capture(self, *args, **kwargs):
        context, manifest = capture(self.repo, *args, **kwargs)
        self.contexts.append(context)
        return context, manifest

    def tearDown(self):
        for context in self.contexts:
            review_session.cleanup_session(context)
        target = self.root.resolve()
        temp = Path(tempfile.gettempdir()).resolve()
        if target == temp or not target.is_relative_to(temp):
            raise AssertionError("refusing to remove a non-temporary test directory")
        review_session._remove_tree(target)
