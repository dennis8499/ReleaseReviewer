import copy

from support import *


class GroupTests(RepoTest):
    def setUp(self):
        super().setUp()
        git(self.repo, "switch", "main")
        write(self.repo, "src/contract.py", 'def emit():\n    return {"amount": 10}\n')
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "old event contract")
        git(self.repo, "switch", "feature")
        git(self.repo, "merge", "--no-edit", "main")
        write(self.repo, "src/contract.py", 'def emit():\n    return {"total": 10}\n\ndef publish(kafka):\n    return kafka.send("invoice.created", emit())\n')
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "rename event field")
        self.peers = {}
        for name, source in {
            "relay": "def forward(payload):\n    return payload\n",
            "consumer": 'def consume(payload):\n    return payload["amount"]\n\nTOPIC = "invoice.created"\n',
            "unrelated": "def clock():\n    return 42\n",
        }.items():
            peer = init_repo(self.root, name)
            write(peer, "src/service.py", source)
            git(peer, "add", ".")
            git(peer, "commit", "-m", name)
            git(peer, "switch", "-c", "feature/local")
            write(peer, "src/service.py", "LOCAL_CHECKOUT_MUST_NOT_BE_USED = True\n")
            git(peer, "add", ".")
            git(peer, "commit", "-m", "local feature")
            write(peer, "untracked.txt", "LOCAL_UNCOMMITTED\n")
            self.peers[name] = peer
        self.hints = {"repositories": [{"path": str(p), "defaultBranch": "main"} for p in self.peers.values()]}

    def group_capture(self, hints=None):
        args = context_helper.parse_args(["--workspace", str(self.root), "--project", str(self.repo),
                                         "--base", "main", "--head", "feature", "--workspace-context-json",
                                         json.dumps(self.hints if hints is None else hints)])
        manifest = context_helper.build_manifest(args)
        context = Path(manifest["context_dir"])
        self.contexts.append(context)
        return context, manifest

    def group_draft(self, context, manifest):
        draft = draft_for(manifest)
        by_name = {r["name"]: r for r in manifest["repositories"]}
        refs = {}
        for name, peer in self.peers.items():
            repo = by_name[name]
            identifier = evidence.record_search(context, evidence.search(manifest, context, "supporting", "def ", repo=repo["id"]))
            proof = {"repo": repo["id"], "source": "supporting", "ref": repo["ref"], "path": "src/service.py", "line_start": 1, "line_end": 2}
            refs[name] = proof
            draft["supporting_coverage"].append({"repo": repo["id"], "status": "unrelated" if name == "unrelated" else "reviewed",
                                                "reason": "本機預設分支的服務契約已核對；clock 工具不參與此事件鏈。" if name == "unrelated" else "追查事件傳遞與消費。",
                                                "evidence": [proof], "searches": [identifier]})
        entry = citation(manifest, "merge-preview", "src/contract.py", 1, 2)
        draft["functional_chain"] = [
            {"id": "F-001", "from_repo": "entry", "to_repo": refs["relay"]["repo"], "kind": "api", "status": "reviewed",
             "description": "入口事件交給 relay 原樣傳遞。", "evidence": [entry, refs["relay"]]},
            {"id": "F-002", "from_repo": refs["relay"]["repo"], "to_repo": refs["consumer"]["repo"], "kind": "event", "status": "reviewed",
             "description": "consumer 仍讀取 amount 欄位。", "evidence": [refs["relay"], refs["consumer"]]},
        ]
        draft["code_risks"] = [{"id": "R-001", "priority": "P1", "title": "事件欄位改名使未變更消費端失敗",
                                "trigger": "入口發出 total 事件並由既有 consumer 處理。", "impact": "consumer 讀取 amount 失敗。",
                                "recommendation": "保留舊欄位或安排相容版本部署。", "scenario_source": "code-derived",
                                "evidence": [entry, refs["consumer"]]}]
        return draft, refs

    def test_three_repo_chain_detects_unchanged_consumer_contract_and_ignores_unrelated(self):
        context, manifest = self.group_capture()
        draft, refs = self.group_draft(context, manifest)
        result = release_report.validate_result(draft, manifest, context)
        self.assertTrue(result["review_complete"])
        self.assertEqual(len(result["code_risks"]), 1)
        self.assertNotIn(refs["unrelated"]["repo"], {e["repo"] for e in result["code_risks"][0]["evidence"]})
        namespaces = {}
        for name in ("relay", "consumer"):
            code = evidence.read_blob(manifest, context, "supporting", "src/service.py", repo=refs[name]["repo"])
            namespaces[name] = {}
            exec(compile(code, f"fixture-{name}", "exec"), namespaces[name])
        base, final = {}, {}
        exec(compile(evidence.read_blob(manifest, context, "base", "src/contract.py"), "fixture-base", "exec"), base)
        exec(compile(evidence.read_blob(manifest, context, "merge-preview", "src/contract.py"), "fixture-final", "exec"), final)
        self.assertEqual(10, namespaces["consumer"]["consume"](namespaces["relay"]["forward"](base["emit"]())))
        with self.assertRaises(KeyError):
            namespaces["consumer"]["consume"](namespaces["relay"]["forward"](final["emit"]()))

    def test_local_default_branch_is_pinned_without_fetch_or_checkout(self):
        before = {name: (git(p, "status", "--porcelain"), git(p, "rev-parse", "HEAD")) for name, p in self.peers.items()}
        context, manifest = self.group_capture()
        self.assertEqual(manifest["fetches"], [])
        for repo in manifest["repositories"][1:]:
            path = Path(repo["path"])
            self.assertEqual(repo["ref"], git(path, "rev-parse", "main"))
            self.assertNotIn(b"LOCAL_", evidence.read_blob(manifest, context, "supporting", "src/service.py", repo=repo["id"]))
            self.assertEqual(before[repo["name"]], (git(path, "status", "--porcelain"), git(path, "rev-parse", "HEAD")))
        peer = self.peers["consumer"]
        git(peer, "switch", "main")
        write(peer, "src/service.py", "NEWER_BRANCH_CONTENT\n")
        git(peer, "add", ".")
        git(peer, "commit", "-m", "after capture")
        captured = next(r for r in manifest["repositories"] if r["name"] == "consumer")
        self.assertIn(b'payload["amount"]', evidence.read_blob(manifest, context, "supporting", "src/service.py", repo=captured["id"]))

    def test_missing_default_branch_no_fallback_and_missing_repo_are_gaps(self):
        hints = copy.deepcopy(self.hints)
        hints["repositories"][0]["defaultBranch"] = "production-missing"
        hints["repositories"].append({"path": str(self.root / "absent"), "defaultBranch": "main"})
        context, manifest = self.group_capture(hints)
        self.assertFalse(manifest["context_complete"])
        missing = next(r for r in manifest["repositories"] if r["name"] == "relay")
        self.assertEqual(missing["status"], "unavailable")
        self.assertIsNone(missing["ref"])
        self.assertTrue(any("absent" in gap for gap in manifest["context_gaps"]))

    def test_local_remote_head_fallback_and_no_main_guess(self):
        for peer in self.peers.values():
            git(peer, "remote", "add", "origin", str(self.root / "unreachable.git"))
            git(peer, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        context, manifest = self.group_capture({})
        self.assertTrue(manifest["context_complete"])
        self.assertTrue(all(r["branch_source"] == "local-origin-HEAD" for r in manifest["repositories"][1:]))
        git(self.peers["relay"], "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
        context, manifest = self.group_capture({})
        self.assertFalse(manifest["context_complete"])

    def test_multiroot_jsonc_workspace_and_git_worktree_use_only_declared_roots(self):
        worktree = self.root / "consumer-checkout"
        git(self.peers["consumer"], "worktree", "add", str(worktree), "main")
        workspace_file = self.root / "review.code-workspace"
        write(self.root, workspace_file.name, '// Explicit VSCode roots, not every Group child.\n' +
              json.dumps({"folders": [{"path": self.repo.name}, {"path": "relay"}, {"path": worktree.name}]})[:-1] + ',}\n')
        hints = {"repositories": [{"path": str(p), "defaultBranch": "main"}
                                    for p in (self.repo, self.peers["relay"], worktree)]}
        args = context_helper.parse_args(["--workspace", str(self.root), "--workspace-file", str(workspace_file),
                                          "--project", str(self.repo), "--base", "main", "--head", "feature",
                                          "--workspace-context-json", json.dumps(hints)])
        manifest = context_helper.build_manifest(args)
        context = Path(manifest["context_dir"])
        self.contexts.append(context)
        self.assertTrue(manifest["context_complete"])
        self.assertEqual({self.repo.name, "relay", "consumer-checkout"}, {r["name"] for r in manifest["repositories"]})
        peer = next(r for r in manifest["repositories"] if r["name"] == worktree.name)
        self.assertIn(b'payload["amount"]', evidence.read_blob(manifest, context, "supporting", "src/service.py", repo=peer["id"]))

    def test_cycles_terminate_and_unconnected_or_wrong_repo_evidence_is_rejected(self):
        context, manifest = self.group_capture()
        draft, refs = self.group_draft(context, manifest)
        draft["functional_chain"].append({"id": "F-003", "from_repo": refs["consumer"]["repo"], "to_repo": refs["relay"]["repo"],
                                          "kind": "event", "status": "reviewed", "description": "確認訊息回傳 relay。", "evidence": [refs["consumer"], refs["relay"]]})
        self.assertTrue(release_report.validate_result(draft, manifest, context)["chain_complete"])
        disconnected = copy.deepcopy(draft)
        disconnected["functional_chain"] = []
        with self.assertRaisesRegex(ValueError, "連回入口"):
            release_report.validate_result(disconnected, manifest, context)
        bad = copy.deepcopy(draft)
        bad["functional_chain"][0]["evidence"][1]["repo"] = "entry"
        with self.assertRaises(ValueError):
            release_report.validate_result(bad, manifest, context)

    def test_chain_gaps_and_unreviewed_peer_cannot_complete(self):
        context, manifest = self.group_capture()
        draft, refs = self.group_draft(context, manifest)
        draft["chain_gaps"] = ["動態路由到外部 Repo，尚未取得來源。"]
        self.assertFalse(release_report.validate_result(draft, manifest, context)["review_complete"])
        draft["chain_gaps"] = []
        draft["supporting_coverage"][0]["status"] = "not-reviewed"
        self.assertFalse(release_report.validate_result(draft, manifest, context)["review_complete"])

    def test_resource_already_used_by_peer_still_requires_entry_service_acl(self):
        context, manifest = self.group_capture()
        draft, refs = self.group_draft(context, manifest)
        peer_use = evidence.search(manifest, context, "supporting", "invoice.created", repo=refs["consumer"]["repo"])
        self.assertEqual(1, len(peer_use["matches"]))
        self.assertEqual([], evidence.search(manifest, context, "base", "invoice.created")["matches"])
        draft["external_changes"] = [{"id": "E-001", "kind": "kafka", "name": "invoice.created",
            "connection": "EntryPublisher", "resolution": "identified", "introduction": "first-use",
            "description": "入口服務首次對既有共用 Topic 發送事件。",
            "baseline_reason": "入口全基礎版本沒有使用；consumer 既有使用不代表入口帳號已授權。",
            "baseline_searches": [baseline_search(context, manifest, "invoice.created")],
            "recommendation": "確認入口服務 Producer ACL 與連線已開通。",
            "evidence": [citation(manifest, "merge-preview", "src/contract.py", 4, 5), refs["consumer"]]}]
        result = release_report.validate_result(draft, manifest, context)
        auth = next(c for c in result["checklist"] if c["operation"] == "authentication")
        self.assertEqual("EntryPublisher", auth["connection"])
        self.assertEqual("待確認", auth["status"])
        self.assertEqual("待人工核對", result["environment_status"])


class ResourceTests(RepoTest):
    def setUp(self):
        super().setUp()
        git(self.repo, "switch", "main")
        existing = ('def existing(ftp, sftp, redis, kafka, http):\n'
                    '    ftp.connect("legacy.ftp.internal", 21)\n'
                    '    sftp.connect("legacy.sftp.internal", 22)\n'
                    '    redis.set("existing:status", "ok", ex=60)\n'
                    '    kafka.send("orders.v1", {"amount": 1})\n'
                    '    http.post("https://legacy.internal/jobs")\n')
        write(self.repo, "src/resources.py", existing)
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "existing dependency targets")
        git(self.repo, "switch", "feature")
        git(self.repo, "merge", "--no-edit", "main")
        self.new_names = ("billing.ftp.internal", "billing.sftp.internal", "invoice:status", "invoice.created", "https://billing.internal/jobs")
        added = ('\ndef added(ftp, sftp, redis, kafka, http):\n'
                 '    ftp.connect("billing.ftp.internal", 21)\n'
                 '    sftp.connect("billing.sftp.internal", 22)\n'
                 '    redis.set("invoice:status", "ready", ex=300)\n'
                 '    kafka.send("invoice.created", {"total": 10})\n'
                 '    http.post("https://billing.internal/jobs")\n'
                 '\ndef alternate_accounts(BillingRead, BillingWrite):\n'
                 '    BillingRead.execute("SELECT tax_rate FROM BillingDb.dbo.InvoiceTax")\n'
                 '    BillingWrite.execute("UPDATE BillingDb.dbo.InvoiceTax SET tax_rate = 0")\n')
        write(self.repo, "src/resources.py", existing + added)
        write(self.repo, "migrations/002_audit.sql", "CREATE TABLE BillingDb.dbo.Audit (id BIGINT);\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "new dependency targets and deployed resource")

    def test_all_resource_classes_generate_pending_operational_checks(self):
        context, manifest = self.capture()
        draft = draft_for(manifest)
        draft["config_changes"] = [config_item(context, manifest)]
        draft["db_changes"] = [db_item(context, manifest)]
        for number, (kind, name) in enumerate(zip(("ftp", "sftp", "redis", "kafka", "other"), self.new_names), 1):
            draft["external_changes"].append({"id": f"E-{number:03}", "kind": kind, "name": name,
                "connection": f"{kind}Connection", "resolution": "identified", "introduction": "target-change",
                "description": "本次新增目標服務存取。", "baseline_reason": "全基礎 Repo 沒有此目標的實際使用。",
                "baseline_searches": [baseline_search(context, manifest, name)],
                "recommendation": "核對本服務帳號的上板準備與權限。",
                "evidence": [citation(manifest, "merge-preview", "src/resources.py", 8 + number)]})
        result = release_report.validate_result(draft, manifest, context)
        checks = result["checklist"]
        self.assertEqual({c["dependency_id"] for c in checks}, {"C-001", "D-001", "E-001", "E-002", "E-003", "E-004", "E-005"})
        self.assertTrue(all(c["status"] == "待確認" and not c["owner"] and not c["evidence"] for c in checks))
        for operation in ("configuration", "schema", "migration", "permissions", "network", "passive-channel", "host-key", "namespace", "topic", "consumer-group", "contract"):
            self.assertIn(operation, {c["operation"] for c in checks})
        self.assertTrue(result["review_complete"])
        self.assertEqual(result["environment_status"], "待人工核對")
        draft["external_changes"][0]["resolution"] = "unknown"
        self.assertFalse(release_report.validate_result(draft, manifest, context)["review_complete"])

    def test_new_deployed_definition_still_requires_environment_confirmation(self):
        context, manifest = self.capture()
        draft = draft_for(manifest)
        item = db_item(context, manifest)
        item["name"] = "BillingDb.dbo.Audit"
        item["description"] = "新增部署 migration 建立 Audit 表。"
        item["baseline_reason"] = "基礎版本沒有此表，部署會執行新增 DDL；環境尚未查證。"
        item["baseline_searches"] = [baseline_search(context, manifest, "BillingDb.dbo.Audit")]
        item["introduction"] = "resource-definition"
        item["evidence"] = [citation(manifest, "merge-preview", "migrations/002_audit.sql", 1)]
        draft["db_changes"] = [item]
        result = release_report.validate_result(draft, manifest, context)
        self.assertEqual(result["db_changes"][0]["introduction"], "resource-definition")
        self.assertTrue(any(c["operation"] == "permissions" for c in result["checklist"]))
        self.assertEqual("待人工核對", result["environment_status"])

    def test_existing_use_is_searchable_for_every_resource_and_new_targets_are_distinct(self):
        context, manifest = self.capture()
        existing = ("EXISTING_TIMEOUT", "CoreDb.dbo.Orders", "legacy.ftp.internal", "legacy.sftp.internal",
                    "existing:status", "orders.v1", "https://legacy.internal/jobs")
        for name in existing:
            with self.subTest(name=name):
                self.assertTrue(evidence.search(manifest, context, "base", name)["matches"])
        for name in self.new_names:
            with self.subTest(name=name):
                self.assertEqual([], evidence.search(manifest, context, "base", name)["matches"])
                self.assertTrue(evidence.search(manifest, context, "merge-preview", name)["matches"])
        draft = draft_for(manifest)
        first, second = db_item(context, manifest), db_item(context, manifest)
        first["connection"], second["connection"] = "BillingRead", "BillingWrite"
        first["introduction"], second["introduction"] = "first-use", "access-change"
        second["id"] = "D-002"
        second["description"] = "同名物件新增另一服務帳號的寫入權限。"
        first["evidence"] = [citation(manifest, "merge-preview", "src/resources.py", 16)]
        second["evidence"] = [citation(manifest, "merge-preview", "src/resources.py", 17)]
        draft["db_changes"] = [first, second]
        result = release_report.validate_result(draft, manifest, context)
        self.assertEqual({"BillingRead", "BillingWrite"}, {c["connection"] for c in result["checklist"]})
        self.assertEqual({"D-001", "D-002"}, {c["dependency_id"] for c in result["checklist"]})
