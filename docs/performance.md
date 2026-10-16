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

---

## 本輪基本功優化（以 0.4.1 為基準）

本節獨立記錄 2026-10-16～17（Asia/Taipei）的本輪結果；上方 0.4.0 歷史量測原樣保留。基準為 `d66fad153ec2d316c1fb6f2d0ff45a0244327e3b` 的 0.4.1，環境為 Windows、Python 3.14.7、Git 2.55.0.windows.5。本輪縮減重複文字輸出，並重用單次驗證內的整檔分析；沒有減少受審範圍。

### 實作與相容性

- `evidence.py batch --shared-content` 明確選用 `ReleaseReviewEvidenceBatch/v1`：相同原始 bytes 的全文只解碼及輸出一次，每筆結果保留自己的 Repo、source、ref、path 與 content_id。各來源仍分別驗證；content_id 使用原始 bytes 的 SHA-256，非法 UTF-8 經 replacement 解碼後文字相同也不誤併。
- 發佈的單次驗證共用相同內容及路徑分類的整檔分析，減少不同引用區間的重複解碼、私鑰掃描與設定解析。每筆引用仍核對固定來源及行號；全檔私鑰優先判斷、JSON／BOM／無效 JSON、YAML／`.env` 設定值省略及認證遮蔽規則保持原樣，快取不跨驗證。
- 原本單筆 reader 與未帶旗標的 batch 格式、逐筆錯誤與結束碼、完整搜尋、v3 Context／Draft／Report、Checklist、預設 patch 產物及 owned context 清理流程均保留。`diff.patch`／`release.patch` 仍依既有條件生成。

### 修正後的 Helper 正式比較

每個情境前後各五次、交替順序，每次使用獨立 worker，以下為中位數。計時包含 helper main()、輸出序列化與 JSON 解析、證據／草稿準備及一次發布驗證；fixture 建立與直譯器啟動不計入。這些是 helper 演練，草稿由 fixture 提供，沒有包含模型審查時間；三次 helper main() 也不代表三次 MCP 往返。

| 情境 | 基準秒數 | 本輪秒數 | 總耗時變化 | stdout bytes（基準 → 本輪） | 輸出變化 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 沒有差異 | 1.161 | 1.237 | 增加 6.55% | 1,836 → 1,891 | 增加 3.00% |
| 一般變更 | 1.615 | 1.613 | 減少 0.07% | 18,205 → 7,870 | 減少 56.77% |
| 重複引用（12 倍） | 1.742 | 1.729 | 減少 0.76% | 186,630 → 45,175 | 減少 75.79% |
| 大量搜尋（25,000 筆） | 1.786 | 1.868 | 增加 4.63% | 1,532,394 → 1,522,059 | 減少 0.67% |
| 多 Repo 功能鏈 | 2.733 | 3.024 | 增加 10.66% | 20,407 → 12,845 | 減少 37.06% |

五種情境的固定來源、完整證據、正規化報告及摘要均等價；Git 呼叫數前後分別維持 31／47／47／48／75，helper main() 各三次。本輪主要可確認的收益是重複全文輸出減量；一般變更及重複引用的總耗時僅微幅下降，沒有差異、大量搜尋與 Group 在本次修正量測中較慢。大量搜尋保留全部命中，shared-content 只減少 read 全文的重複輸出。

擷取階段程式及 Git 次數未變，量測仍有漂移，例如沒有差異的擷取中位數為 1.086 → 1.185 秒，Group 為 1.797 → 1.932 秒。環境或排程波動是可能解釋，尚無獨立歸因證據。上述 helper 結果不支持「所有情境整體更快」或「任意 Agent 審查都在十分鐘內完成」的結論。

### 比對器修正與證據保留

初次比對器只在候選版為還原共享格式而 deep-copy 整棵輸出，連大量 search matches 也被複製，這項比對成本曾被誤計入 evidence 時間。修正後只展開 read wrapper，將為等價比對而做的格式展開成本另記 `oracle_seconds` 並排除；兩版的 helper main()、序列化及 JSON 解析仍計時，報告／摘要正規化仍在計時外。兩次量測之間沒有修改產品 runtime。

原始完整 helper 矩陣 70 筆（其中 Release 50 筆）及修正後 Release 50 筆均保留，沒有用新結果覆寫舊資料。初版大量搜尋候選版慢 8.93%，修正後仍慢 4.63%；其他初版與修正版數字一併保留於[本輪精簡數據](performance-basic-results.json)。原始檔位於工作區 `.tmp/reviewer-basic-20261016-215439/helper-formal/`，包含 `top.json`、`release-existing.json`、`release-existing-oracle-corrected.json` 及 `release-oracle-correction-summary.json` 與對應產物。

### 功能驗證界線

本輪不重複測試共 **112 項通過**：完整模組回歸 110 項，最後 benchmark 模組九項包含兩項新增 oracle 案例，重疊七項只計一次；靜態測試清單亦核對為 112 個方法。套件解壓後獨立執行驗證包含於回歸。最後九項於所有正式計時完成後執行，exit 0，紀錄為 `release-final-benchmark-tests.log`。另以封存版與新版 `preview_snapshot` 比較 8 種原始內容、19 種路徑情境及 5 種引用範圍，共 **760 組結果完全相同**，涵蓋空檔、CRLF、巢狀 JSON、無效／含 BOM JSON、引用區間外的私鑰、認證及非法 UTF-8。這些檢查驗證固定案例的功能相容性；本輪獨立 Agent 結果另列於下。

### 本輪大型案：固定草稿各五次 Helper 重播

使用下方第一份實際 Agent 的完整草稿、相同固定來源及 129 筆實際順序的證據請求。重播時請求已全部已知，合併為單次 batch；與 Agent 邊發現邊追查的分輪操作不同。前後各五次、交替順序、循序執行，沿用上方校正後計時方式。

| 階段／指標（中位數） | 基準 | 本輪 |
| --- | ---: | ---: |
| 擷取 | 3.9002 秒 | 4.3676 秒 |
| 證據／草稿準備 | 4.7620 秒 | 4.4836 秒 |
| 發布內的一次驗證 | 3.1924 秒 | 3.1425 秒 |
| 發布其餘部分 | 0.0164 秒 | 0.0149 秒 |
| Helper 總計 | 12.0805 秒 | 11.7951 秒 |
| 最大總耗時 | 12.4342 秒 | 13.6088 秒 |
| 600 秒內完成 | 5/5 | 5/5 |
| Helper main() | 3 | 3 |
| Git subprocess | 262 | 262 |
| stdout bytes | 319,461 | 325,346 |

總計獨立取中位數，並非各階段中位數相加。本例耗時下降約 **2.36%**；共享格式 stdout 反而增加 **1.84%**，此短檔案例的 content_id／共享表成本高於省下的文字，不能宣稱新格式總會減量。十次完整 manifest、證據、實際驗證、原始 patch、Markdown 及其 metadata／雜湊全部等價。Git 與 main() 次數不變，未把省略比較器格式展開成本稱為產品加速。

逐次樣本及完整 artifact 保留於 `helper-formal/release-large.json` 與 `release-large-artifacts/`；跨專案彙總為 `helper-formal/large-helper-summary.json`。完整 Agent 的時間另列於下，沒有用 helper 秒數代替。

### 本輪大型案：完整 Agent 各三次

Fixture 含 12 個 Repo，各 1,000 個追蹤檔案及 20 個分支變更。本次唯一入口審查 20 個變更：16 個文字註記及四個事件 publisher，追查四條 publisher → relay → consumer 鏈，另排除三個無關 Repo。Supporting Repo 使用各自固定本機 main，工作檔仍在 feature 分支。此案例測試大量檔案與契約追查，不代表相同檔案數的任意程式複雜度。

前後各三個 fresh Agent，沿用相同父模型／設定、單一 reviewer、交替順序、循序執行。Agent 只取得來源與對應技能，未提供預期結論或其他輪次草稿；自行審查並發布真實報告。計時從 spawn 前開始，包含技能載入，至發布、報告複製及 owned context 清理後的 completion marker；最後聊天回覆不計入。未與 helper benchmark 或測試並行。

| 指標（中位數） | 0.4.1 基準 | 本輪 |
| --- | ---: | ---: |
| 完整耗時 | 249.638 秒 | 269.378 秒 |
| 最大耗時 | 253.144 秒 | 282.357 秒 |
| 600 秒內完成／完整審查／清理 | 各 3/3 | 各 3/3 |
| Helper 執行時間 | 19.380 秒 | 19.122 秒 |
| Helper CLI | 6 | 7 |
| Git subprocess | 373 | 359 |
| Helper stdout bytes | 366,526 | 364,096 |

基準三次為 253.144／240.097／249.638 秒，本輪為 282.357／252.558／269.378 秒。完整 Agent 中位耗時增加約 **7.91%**，本組樣本未證明 Agent 加速；六次均達本例的 600 秒軟目標。各 Agent 的請求數、批次分輪與操作修正不同，表中操作／輸出差異不當成純程式改善比例；相同請求的 helper 重播另列。UTF-8 管線／路徑顯示的修正時間均保留，未排除較慢樣本。

六份報告都完成 20 個路徑、八個功能連結、11 個 supporting Repo 與七類依賴對帳。每份將四條 `amount` → `total` 造成的 consumer 失敗合併為一項 P1，逐鏈引用 publisher、固定 main relay 及 consumer；沒有把泛用 bus 臆測為新增 Kafka，也沒有對未提供的 broker ack／重試行為做判斷。無新增部署依賴、無證據缺口，Checklist 保持未確認；靜態審查完成但上版判斷為 blocked。Reviewer 未執行應用／測試，問題均為 `code-derived`。

已逐份核對固定 main refs、必要路徑及功能邊、原始 report SHA-256、v3 metadata 的固定正文與 context SHA-256，以及 owned context 缺席。`model_review_seconds` 為 null；其他耗時仍包含模型／服務排程、閱讀、工具往返、程序啟動與草稿準備，不能當作純模型推理時間。CLI／stdout 只統計 wrapper 記錄的 reviewer helpers，不包含草稿與檔案處理的其他 shell／工具呼叫；其他操作時間仍在完整耗時內。

[本輪精簡數據](performance-basic-results.json)保留每次結果及品質核對；完整 trace、實際請求／草稿／manifest／報告、`agent-results.json` 與 `release-agent-quality-audit.json` 位於工作區 `.tmp/reviewer-basic-20261016-215439/`。本輪未更新全域安裝或發布版本。
