# ReleaseReviewer 0.12.0：可靠性與品質驗收

本版維持三個使用者輸入、Context／Draft／Report v3、固定 SHA、完整覆蓋、人工 Checklist 與跨 Reviewer 共用 Git 配額。請從 [v0.12.0 Release](https://github.com/dennis8499/ReleaseReviewer/releases/tag/v0.12.0) 取得本版 ZIP；已安裝技能與 GitlabWorkSpace 離線套件需另行更新。

## Session 行為

- `capabilities.runtime_paths` 揭露 session 與 Git network runtime 的實際路徑、設定來源。`--runtime-root` 不改寫 Git 鎖的 `GITLAB_WORKSPACE_RUNTIME_DIR`；工作台沿用既有共用位置。
- 預檢錯誤附 `preflight` 診斷，指出失敗位置及 fetch 尚未開始。完整性錯誤保留既有終止規則。
- 發布完成前的權限／檔案占用錯誤回傳 exit 3、`publication.io`、`publication-retry`，保留 context。修正指定位置後，以同一 ID 重跑 `finish`，不 fetch；成功或 `cancel` 清理證據。
- `check.summary_preview` 提供遮蔽後摘要；`finish` 拒絕完整審查保留預設摘要，真實未完成草稿仍可發布未完成結果。已保存 read metadata 可提前診斷已知的引用行號上限；最終仍獨立驗證來源。
- 唯讀批次 `view --requests-file` 與原單頁命令並存。整份回應含換行不超過 24 KiB；`next_index` 及各 artifact 游標獨立續讀，批次結束不表示內容讀完。
- 新 context 使用 `risk_baseline_policy=entry-base/v1`。每項 P0/P1 填 `baseline_reason`，附 base 引用或 catalog 證實的新路徑；歷史 context 沿用原規則。helpers 檢查結構，語意由 reviewer 對照 base／最終版本判斷。

## 可驗證的技能隔離

`--ignore-user-config` 只略過使用者 config，認證仍取自 `CODEX_HOME`，不足以證明無技能對照已隔離。[官方 CLI 文件](https://learn.chatgpt.com/docs/developer-commands?surface=cli)

品質 runner 現在使用獨立且具 ownership marker 的 Codex profile，透過官方支援的 `skills.config` 與 `skills.bundled.enabled=false` 設定選用技能；保留使用者原 profile 與認證檔。每次模型審查前後呼叫 `skills/list`、`forceReload=true`，驗證實際 enabled 清單：無技能對照為空，其他條件只允許指定的 ReleaseReviewer。記錄 config hash、技能 payload hash 和去除本機路徑的 inventory proof；發現其他技能、清單錯誤或 config／payload 漂移時，該樣本不通過。[技能設定](https://learn.chatgpt.com/docs/build-skills)、[App Server 協定](https://learn.chatgpt.com/docs/app-server)

可先執行零模型呼叫的 inventory probe：

```powershell
python -X utf8 -B scripts/quality_isolation.py --workspace "<來源工作區>" --profile "<專用 profile>" --output "<新的 probe.json>"
```

用 `--skill <來源工作區>/.agents/skills/release-reviewer/SKILL.md` 查驗有技能條件。Probe 僅證明 inventory 可隔離，輸出明示 `model_reviews_executed=0`，不能代替完整樣本或語意評分。

## 模型矩陣與獨立評分

`run_quality_matrix.py` 沿用既有三模型與 effort。預設條件為 `no-skill`、`baseline`、`candidate`；舊 `--condition v0.9.0` 轉為 `baseline`，實際版本及 payload hash 記在矩陣。輸出須使用新目錄；fixture scratch 必須在所有 Git Repo 外。歷史結果保留。

```powershell
python -X utf8 -B scripts/run_quality_matrix.py --baseline-skill-dir "<固定基準>" --candidate-skill-dir skills/release-reviewer --scratch-dir "<Repo 外短路徑>" --codex-home "<專用 profile>" --output-dir "<新的結果目錄>"
```

專用 profile 使用 CLI 認證環境或事先完成的專用登入，不自動複製使用者認證。執行 runner 時沿用原使用者環境，由 runner 對子程序指定專用 profile。Oracle 留在 fixture workspace 外；模型只取得來源、任務與當輪技能。保留失敗與修稿成本；`elapsed_seconds` 包含隔離探測，`review_process_seconds` 與 `isolation_seconds` 分開，未知 token／HTTP／SSH 指標保持 null。

新增 `preexisting`、`new-trigger`、`deleted-endpoint` 案例，加上既有 `gap`，分別驗證既有問題排除、新呼叫觸發舊函式、刪除仍有 consumer 的端點與必要缺口保留。Fixture 的可執行斷言只驗證案例成立，不產生模型品質分數。

獨立評審在完整矩陣產生後，比對固定來源、oracle 與實際報告，另存評分檔：

```json
{
  "schema": "ReleaseReviewerIndependentScores/v1",
  "matrix_sha256": "<原 quality-matrix.json 原始 bytes 的 SHA-256>",
  "reviewer": "<獨立評審識別>",
  "independent": true,
  "reviews": [{
    "id": "<矩陣中的樣本 ID>",
    "report_sha256": "<該保存報告的 SHA-256>",
    "criteria": {
      "no_false_blockers": true,
      "required_blockers_found": true,
      "coverage_honest": true,
      "required_gaps_preserved": true,
      "evidence_supported": true,
      "deployment_dependencies": true
    },
    "rationale": "<對照來源與 oracle 的根因、必要證據及缺口理由>"
  }]
}
```

每項準則都需判斷；案例沒有該義務時，確認未捏造問題後可填 true。以根因及必要證據評分，不要求固定問題數。`independent=true` 是評審的角色聲明，工具不能代替人員／執行程序的獨立性確認。

```powershell
python -X utf8 -B scripts/score_quality_matrix.py --matrix "<結果>/quality-matrix.json" --scores "<獨立評分.json>" --output "<新的 quality-scored.json>"
```

Importer 核對矩陣／報告 hash、完整三模型三條件、全部案例各至少三次、隔離與 payload 證據、原始發布收據／清理、機密外洩及六項語意準則。缺樣本、缺評分或隔離未知維持未驗收；candidate 任一關鍵準則失敗則不通過。exit 0 為通過，3 為合法結果但未完成／未通過，2 為輸入格式、評分綁定或檔案操作錯誤。原矩陣與評分檔不覆寫。`semantic_cells` 按模型／案例提供各條件的平均分、逐準則通過率，以及 candidate 對 baseline／無技能的分數差；缺評分時保留 null。

## 回歸命令

```powershell
python -X utf8 -B -m unittest discover -s tests -v
python -X utf8 -B scripts/test_release_ci.py -v
```

完整測試、封裝檢查與實際模型語意驗收分開記錄。未取得獨立實測結果前，模型分數保持未測量，不宣稱品質或端到端速度達標。

## 本輪本機驗證

- Windows／Python 3.14.7：完整回歸 318 項通過，後續新增的 4 個測試方法以補充回歸覆蓋，共 322 個不同方法通過。最終補充檢查另核對唯讀批次、已知引用行數與解壓後 session；重疊項目不重複計數。
- 發行契約測試 25 項通過；封裝模組 6 項與最後一輪品質工具 20 項通過，這些模組回歸與上述方法有重疊。
- 本機 Codex CLI 0.166.0 的無技能／指定技能 inventory probes 均通過；模型審查呼叫數為 0。
- 套件含 30 個檔案，封裝檢查逐檔核對來源的 canonical bytes。正式 ZIP 與 SHA-256 附件由 tag CI 提升已驗證的相同 bytes，發行頁保留來源 commit 與 CI proof。
- 本機未另裝 Python 3.10；正式發布流程要求 Windows／Python 3.10 與 3.14 完整 CI 通過。實際三模型矩陣及獨立語意評分尚未執行。
