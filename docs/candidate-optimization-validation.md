# ReleaseReviewer 候選優化驗證

比較基準為 0.8.0 的 `da8796bfa7569bf7320dcb0100df7eaddbd9c267`。本候選保留 v3 Context／Draft／Report 與單一繁中報告，尚未通過完整模型、跨平台及端到端驗收，不得宣稱「優化達標」。使用者已知悉限制並要求發布 0.9.0；正式發布仍須通過完整 CI 矩陣。

## Helper 量測

`scripts/benchmark_session.py` 直接執行 session CLI 的 `start → step → step → check → finish`。單 Repo 和 Group 各執行兩輪、每輪 24 次讀取及基礎搜尋；Group 的入口與 supporting Repo 讀取交錯排列，另搜尋 supporting Repo。每個樣本使用新 fixture 和新 request。基準與候選各五次並交替順序，保留每次時間、Git 指令數、輸出 bytes、exit code、網路活動與全部失敗樣本。完整資料寫入 [session helper 原始樣本](performance-session-candidate.json)。

修正重複 operation ID 的預讀前綴、補入搜尋及補上舊 CLI 的收據政策檢查前，曾分別中斷量測；已完成的樣本保存在[第一輪先導樣本](performance-session-preliminary.json)、[第二輪先導樣本](performance-session-preliminary-2.json)及[第三輪先導樣本](performance-session-preliminary-3.json)，不併入最終比較。最終量測會在每筆完成後寫入尚未完成的結果，並核對開始與結束時兩份 payload 的 SHA-256。

先前已完成的五組比較保留為[第一版候選樣本](performance-session-candidate-v1.json)及[第二版候選樣本](performance-session-candidate-v2.json)。後續分別新增跨交錯 Repo 讀取的批次合併及精簡報告 metadata；以下表格使用最新 payload 的正式比較。

輸出將 shared 內容還原後比對完整讀取結果與固定來源身分；這只是合成 fixture 的 helper 一致性檢查，草稿是測試工具產生，不構成語意品質盲測。CLI 時間包含 Python 啟動、序列化與程序往返，不包含模型推理；無法取得模型 token、HTTP／SSH 實際請求數或 Agent 時間的欄位為 `null`。Git subprocess 計數不是 HTTP／SSH 請求數。每個 fixture 的本機分支不應有 fetch；遠端新鮮度、失敗及同 request 重試另由功能回歸測試驗證。

2026-10-28（Asia/Taipei），Windows／Python 3.14.7 的正式量測共 20 個成功樣本，兩類情境的讀取與搜尋結果摘要皆一致，本機分支 fetch 次數均為零。以下為五次中位數：

量測時 Skill 的 `VERSION` 尚為 0.8.0；發版準備只將該檔改為 0.9.0。以 0.8.0 內容代入 `VERSION` 重新計算整個 Skill payload 摘要，與正式量測的候選摘要一致。

| 情境 | 完整 helper：0.8.0 → 候選 | 完整耗時比 | 兩輪讀取／搜尋：0.8.0 → 候選 | 讀取／搜尋耗時比 | 兩輪 Git 指令 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 單 Repo | 37.52 → 29.90 秒 | 0.7968 | 12.59 → 3.77 秒 | 0.2996 | 65 → 3 |
| Group | 32.18 → 24.35 秒 | 0.7568 | 11.13 → 3.64 秒 | 0.3267 | 76 → 6 |

兩類讀取／搜尋分別降低約 70.0% 與 67.3%，達 helper 的 30% 目標。這不能推論完整 Agent 達到 15% 目標；來源資料為合成 fixture，測試期間機器可能另有其他使用者程序。

可用乾淨基準重跑：

```powershell
$releaseBenchmark = Join-Path $env:TEMP ('release-benchmark-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $releaseBenchmark | Out-Null
git archive --format=zip --output="$releaseBenchmark/baseline.zip" da8796b -- skills/release-reviewer
Expand-Archive -LiteralPath "$releaseBenchmark/baseline.zip" -DestinationPath "$releaseBenchmark/baseline"
python -X utf8 -B scripts/benchmark_session.py --baseline-skill-dir "$releaseBenchmark/baseline/skills/release-reviewer" --runs 5 --output "$releaseBenchmark/results.json"
```

舊 `scripts/benchmark_review.py` 仍量測原 capture／evidence／report CLI，與 session CLI 的結果分開。無差異案例的一組探索結果保留於[舊 CLI 先導紀錄](performance-legacy-candidate-final-smoke.json)：兩邊 helper 都成功，證據、patch 與正規化報告一致，但 manifest／result 新增政策與網路紀錄欄位，原工具因此以整體摘要不符停止，不產生速度結論。另保留精簡報告 metadata 前的[先導紀錄](performance-legacy-candidate-smoke.json)；單次探索不可當作五次中位數或 Agent 效能。

## 品質與發布門檻

`tests/create_quality_fixture.py` 可獨立建立 P0、P1、相容變更、最終無差異、必要 consumer 缺失、未使用設定、provider 不明及機密遮蔽案例；`oracle.json` 在來源工作區之外。建立來源及斷言 fixture 本身正確，不代表模型完成盲測。獨立驗收必須不向 reviewer 提供 oracle，按根因、固定來源、必要證據、缺口及假阻擋評分，並記錄每次完整報告、失敗及修稿。

Windows／Python 3.14.7 最終 payload 以 27 個獨立模組執行的 253 項測試全部通過，包括 19 項既有 session、13 項新 session、4 項獨立套件及 10 項長路徑測試；完整紀錄見 [Windows 回歸紀錄](validation-candidate-windows-py314.log)。`scripts/test_release_ci.py` 的 25 項也通過。

先前一輪長路徑模組有 3 項測試失敗：舊格式模擬仍帶有新收據，且兩項錯誤訊息先由收據檢查回報。修正測試前提並保留原本防竄改及無來源回讀斷言後重跑通過；初次失敗記錄也保留在上述 Windows 回歸紀錄。交錯 Repo 讀取加入後，受影響的 7 個模組亦曾在系統暫存目錄 `rr-parallel-tests-58vwqq5f` 全部通過；早期紀錄不充作最終版本的完整回歸。

GitlabWorkSpace 的相關 8 項隔離單元測試及完整 TypeScript `--noEmit` 檢查通過。最初整套 unit 執行曾因其他程序同時清理共享編譯輸出而中斷，未將該次視為通過；後續相關測試使用獨立輸出目錄。實測 0.8.0 session help 可供安全回退，而全域 0.3.1 的 cleanup-only 入口會被拒絕。

仍需執行：

- `gpt-6.1-sol/high`、`gpt-6-astra/high`、`gpt-6-luna/max` 各比較無技能、0.8.0、候選；關鍵案例各三次，保留完整模型及工具記錄。此環境沒有可用 Codex CLI、OpenAI SDK 或 API 憑證，尚未執行。
- 單 Repo 與 Group 各六組完整 Agent 有效配對；全部配對耗時比中位數 ≤ 0.85，各類中位數不得退步超過 5%。失敗與修稿成本均記錄，helper 秒數不能替代 Agent 秒數。
- Windows／Linux 各以 Python 3.10／3.14 執行完整回歸與獨立套件測試；本次驗證環境僅確認 Windows Python 3.14，WSL 不可用。
- WorkflowKit payload、工作台安裝來源與實際安裝的 Skill 本次未自動更新；若後續更新，須依正常發布流程逐檔核對內容。

此工作區核對時，全域安裝版本為 0.3.1，其 `review_session.py` 只有 cleanup；WorkflowKit 離線 payload 為 0.8.0，具有 start／step session 但沒有 capabilities 命令。封存包的 27 個檔案均與 `da8796b` 的正規化來源一致，其中 9 個檔案與本次候選不同。WorkflowKit 的既有安裝測試確認安裝內容與封存包一致，既有兩種 Reviewer 的 session 續跑、發布重試及取消測試也通過；這不代表候選已安裝。工作台先探測已安裝入口：能力命令不存在時檢查 session help，支援舊版介面才回退；cleanup-only 版拒絕。對已宣告時間預算能力的 ReleaseReviewer 使用 `unlimited`，未宣告能力的舊版不傳該選項。
