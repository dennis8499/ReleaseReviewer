# Release Reviewer 本機效能驗證

2026-10-16（Asia/Taipei），Windows、Python 3.14.7、Git 2.55.0.windows.5。保留完整審查範圍、固定來源與 v3 報告契約；本文件保留 0.4.0 發布前的本機優化量測；以下數字對應原始固定候選，未重新量測其他環境。

## Helper 前後比較

以 `d8d049e` 的原始技能為基準，每種情境前後各跑三次，交替順序、每次使用獨立 Python process，以下為中位數。全部五種情境的正規化 manifest、完整證據與驗證結果摘要一致。

| 情境 | 原版 | 優化後 | 耗時下降 | Git 呼叫 | Helper 命令 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 沒有差異 | 1.222 秒 | 1.130 秒 | 7.5% | 34 → 31 | 3 → 3 |
| 一般變更 | 3.958 秒 | 1.598 秒 | 59.6% | 104 → 47 | 18 → 3 |
| 重複引用（12 倍） | 31.284 秒 | 2.254 秒 | 92.8% | 830 → 47 | 183 → 3 |
| 大量搜尋（25,000 筆） | 26.616 秒 | 2.328 秒 | 91.3% | 105 → 48 | 19 → 3 |
| 多 Repo 功能鏈 | 5.007 秒 | 3.323 秒 | 33.6% | 142 → 75 | 25 → 3 |

計時包含擷取、證據／fixture 草稿準備、一次完整驗證及發布，排除 fixture 建立與 Python 啟動。Helper 命令數是同程序內的 main 呼叫數，無法直接當成 MCP 工具往返；未包含模型推理時間。搜尋保留全部命中，重複引用仍逐項核對，沒有以減少檢查範圍換取速度。

完整樣本、分階段時間、輸出量、語意摘要及候選檔案 SHA-256 見 [helper 原始數據](performance-helper-results.json)。單次探索 smoke 的數字未混入此三次中位數。

## Agent 完整審查演練

前後各三個獨立 Agent，沿用相同父模型與設定，未覆寫模型；每個 Agent 審查相同已提交來源的單 Repo 與 Group，產生兩份繁中報告。讀完技能入口與 job 後開始計時，至兩份報告完成與清理後結束。

| 指標（每次兩案，中位數） | 原版 | 優化後 |
| --- | ---: | ---: |
| 整體等待時間 | 363.796 秒 | 199.334 秒 |
| Helper wrapper 呼叫 | 89 次 | 13 次 |
| Git 呼叫 | 494 次 | 160 次 |
| Helper 輸出量 | 50,537 bytes | 28,050 bytes |

本次整體耗時下降 **45.2%**，helper 呼叫下降 **85.4%**。三次原版為 222.635／424.032／363.796 秒；最終候選版為 181.626／199.334／201.968 秒。正式新版使用同一份固定候選；BOM 容錯與 PowerShell UTF-8 指引加入前的 optimized-1 先導測試未列入。

十二份最終報告都檢出單 Repo 的兩項 P1（零筆統計除零、必要發票設定缺失）與 Group 的一項事件欄位相容性 P1；三項新增設定及 InvoiceTax 首次使用均有提醒，既有 Orders 未重複列入。七類依賴覆蓋、全部 supporting Repo、固定快照摘要與零缺口均通過核對；人工 Checklist 保持待確認。DB 提醒拆項粒度不同，單 Repo 待確認數為 8 或 13，未要求自然語言措辭或核對項數完全相同。

這是小型探索樣本，包含模型推理、工具傳輸及格式重試成本；原版曾因 PowerShell 引號／中文編碼重做，兩次早期試跑亦遇到 wrapper 未轉送 argparse 診斷，之後已修正量測工具。不能把 45.2% 全歸因於 Python，也不能推定所有實際 Repo 都有相同比例。模型 tokens 與服務排程耗時未獨立取得。Helper 呼叫只統計 wrapper，其他檔案編輯／MCP 往返沒有混稱為此數字。

逐次結果與品質核對見 [Agent 數據](performance-agent-results.json)。本機完整 traces、原始報告及先導紀錄保留在工作區 `.tmp/release-reviewer-performance/`。

## 正確性與交付

- 100 項不同測試通過：第一輪完整回歸 93 項，後續新增 7 項定向驗證；打包的兩項既有測試亦重新通過。
- 涵蓋批次／單筆等價、快取隔離、逐引用 SHA／行號、rename／delete、binary／gitlink、全部 merge parents、搜尋竄改、機密遮蔽、修稿重試與成功／錯誤／中止清理；BOM requests／draft 另有實測。
- 獨立 ZIP 解壓後通過擷取、8 筆工作清單批次、未審查骨架發布 incomplete、唯一 Markdown 與 owned context 清理冒煙。
- 已安裝版 18 個檔案與候選來源逐檔 SHA-256 一致，通過 skill quick_validate 與批次／BOM／發布／清理冒煙；只變更其中 11 檔，其他使用者內容保留。
- 原已安裝技能完整備份在 `.tmp/release-reviewer-performance/installed-backup/`；量測時未更新正式發行包或 GitlabWorkSpace 整合；0.4.0 發版另驗證獨立附件及工作台 0.24.4 的整合。

## 重跑 Helper benchmark

在 ReleaseReviewer Repo 執行，使用新的時間戳目錄保留歷史：

~~~powershell
$releasePerfRun = Join-Path '.tmp' ('performance-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $releasePerfRun | Out-Null
git archive --format=zip --output="$releasePerfRun/baseline.zip" d8d049e -- skills/release-reviewer
Expand-Archive -LiteralPath "$releasePerfRun/baseline.zip" -DestinationPath "$releasePerfRun/baseline"
python -X utf8 -B scripts/benchmark_review.py --baseline-skill-dir "$releasePerfRun/baseline/skills/release-reviewer" --runs 3 --output "$releasePerfRun/result.json"
~~~

可加 `--scenario normal` 或其他已列情境縮小量測。Worker 失敗或語意摘要不一致會回傳非零，不能將失敗樣本當成成功加速。

回歸命令：`python -X utf8 -B -m unittest discover -s tests -v`。測試與 benchmark 使用隔離 fixture，不連線實際 DB、Kafka 或部署環境。
