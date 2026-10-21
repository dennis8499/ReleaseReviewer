# Release Reviewer｜上版檢查

獨立 Codex Skill **0.7.0**。只接受 Repo、基礎分支、比較分支，固定合併前審查。檢查直接阻止上版的 P0／P1、相關功能鏈與新增部署依賴，在 VSCode 受審 Repo/review-reports 產生**一份含可填寫 Checklist 的繁體中文 Release Report**。

## 安裝與使用

需要 Python 3.10+、Git 及可使用本機 Skills 的 Codex。Git 須支援 merge-tree --write-tree 與 --merge-base；缺少乾淨預覽時保留缺口，不改用直接比較。

將 skills/release-reviewer 複製到 Codex Skills 目錄，或解壓 dist/release-reviewer-0.7.0.zip。CODEX_HOME 有設定時使用其 skills 子目錄，否則使用使用者的 .codex/skills。

~~~text
$release-reviewer Repo=OrderService 基礎分支=origin/main 比較分支=origin/feature/payment
$release-reviewer Repo=C:\projects\OrderService 基礎分支=main 比較分支=feature/payment
~~~

三項均必填。短分支名代表本機分支；明確遠端分支會取得最新指定版本，失敗停止。不接受 tag、commit、HEAD、直接比較、JSON 或報告路徑選項。本機未提交修改不納入審查。

工作區範圍由 VSCode／工作台內部資料取得。Group 的相關上下游使用各自擷取時已提交 HEAD 固定 SHA，不 fetch、不改用 checkout；缺少版本或鏈路保留未完成。新增使用以入口 base_sha 整個 Repo 為基準，涵蓋首次使用、部署定義、目標改變及新增權限。

透過 GitlabWorkSpace 使用時，請同步更新包含本版原生 Skill 的離線套件；專案內另行安裝的技能也須更新。歷史報告保留。

同次審查只由主 agent 擷取一次，子 agent 共用固定 context；修稿沿保留的同一份快照完成。明確遠端 base/head 每個 remote 批次同步一次，關聯 Repo 使用本機 HEAD；終止錯誤回報後停止，不自動重跑。新增 `network_activity` 分別列 fetch 嘗試／成功與分支要求／同步數，不代表 HTTP 請求數。

新 context 使用 `coverage_policy=ancestor-noop/v1`。只有完整證明比較分支已含於基礎版本、乾淨合併不新增內容、全部 catalog 已擷取且沒有必要缺口時，agent 才將七類依賴與關聯 Repo 覆蓋明確判為「本次不適用」，保留理由與清單；初始草稿仍未審查。報告無新增 Checklist 時顯示「無本次新增核對項」，實際環境仍未驗證。其餘新增依賴判斷繼續以入口 base 整個 Repo 為基準。完整條件見[審查規則](skills/release-reviewer/references/review-rules.md#祖先分支無新增內容)。

祖先豁免另需 capture 封存的 `ancestor_path` commit 證據，由發布器驗證 hash 與 parent 連線；草稿不能自行宣告祖先關係。

## 0.7.0 更新

祖先分支確認沒有新增差異時，排除多餘待讀、覆蓋與功能鏈追查；七類依賴及關聯 Repo 保留明確的「本次不適用」判定，發布前重新驗證固定快照與真實 commit 祖先路徑，實際環境仍未驗證。

補齊同步前本機預檢、capture 專屬 fetch 計數、固定內容行段讀取與既有搜尋 artifact 篩選。Context／Draft／Report 維持 v3；舊 context 與未使用新參數的讀取保持相容。完整變更見 [CHANGELOG](CHANGELOG.md)。

## 0.6.0 更新（歷史）

一次只審查指定 Repo/base/head。由同一 Skill Agent 採 Code Review 方法審查，延伸項目連回固定 Diff；執行時沿用所選模型。擷取先封存入口版本、關聯 Repo 目前已提交 HEAD 與適用指示，後續閱讀及驗證只使用 owned Git 快照。明確遠端分支依 remote 批次同步一次；本機分支與關聯 Repo 不連線。

報告放在受審 Repo/review-reports，判斷與問題數量置頂，五節依序為比較資訊、問題、範圍與限制、下一步、技術審查紀錄。問題採六個粗體區塊，末尾技術紀錄收合，範圍與限制中的部署依賴及 Checklist 保持展開。新草稿使用 engineering-review/v2，完整填 expected／actual；歷史草稿缺值時標「未提供」。GPT-6 Luna MAX 僅用於獨立驗收，不是技能執行預設。

正式發版使用 GitHub JSON media type 下載 Actions 附件，Release 二進位附件仍使用 octet-stream，避免完整 CI 已通過卻無法發布安裝包。

## 0.3.1 更新（歷史）

精簡 Skill 入口與工作流程路由，統一進度、回答及文件的 Concise 規則，補上部署資源與審查範例。沿用 v3 context／draft／report 與單檔人工 Checklist。

## 使用步驟

1. 在 VSCode 開啟要檢查的資料夾或已儲存的 .code-workspace，並確認該環境可使用 release-reviewer 技能。
2. 確認入口 Repo 的基礎分支及比較分支已提交。本機未提交內容會保留，不列入本次審查。
3. 在 Codex 輸入下列三項資料；Repo 不可省略，基礎分支代表準備接收變更的版本，比較分支代表準備上版的變更版本。
4. 等待固定版本、合併預覽、相關功能鏈與新增依賴審查完成，再開啟回覆中的唯一 Release Report 連結。
5. 先處理報告的直接阻擋與檢查缺口，再由負責人逐項填寫同文件內的 Checklist。修正程式或分支版本改變後，重新執行審查。

| 必填輸入 | 說明 | 範例 |
| --- | --- | --- |
| Repo | 明確的專案名稱或 Git Repo 路徑；重名時使用完整路徑 | OrderService |
| 基礎分支 | 接收本次變更的本機或明確遠端分支 | main、origin/main |
| 比較分支 | 本次準備上版的本機或明確遠端分支 | feature/payment、origin/feature/payment |

### 可直接複製的範例

單 Repo：在 VSCode 開啟該 Repo，再使用明確路徑及本機分支：

~~~text
$release-reviewer Repo="C:\projects\OrderService" 基礎分支=main 比較分支=feature/payment
~~~

Group：在 VSCode 開啟包含多個 Repo 的 Group，指定其中一個入口 Repo。其他 Repo 只用於查證相關上下游，不額外指定比較分支：

~~~text
$release-reviewer Repo=OrderService 基礎分支=origin/main 比較分支=origin/feature/payment
~~~

Multi-root：先儲存 .code-workspace，再使用相同三項輸入。工作區檔案與根目錄由環境提供，不需要在請求中新增輸出路徑選項。

若 Git 遠端無法取得指定分支、Repo 名稱有歧義、分支不存在，或工作區根目錄無法辨識，先解決問題再重試；不會改用過期版本、同名 ref 或猜測分支。

### 報告會放在哪裡

| VSCode 開啟的環境 | 入口 Repo | 報告目錄 |
| --- | --- | --- |
| 單一 Repo 資料夾 | C:\projects\OrderService | C:\projects\OrderService\review-reports |
| Group 資料夾 C:\projects\BillingGroup | BillingGroup 內的 OrderService | OrderService\review-reports |
| 儲存在 C:\workspaces\billing.code-workspace 的工作區 | 任一宣告的 Repo | 該 Repo\review-reports |
| 未儲存 multi-root，沒有唯一根目錄 | 任一 Repo | 先釐清／儲存工作區，不猜位置 |

工作區位置只界定可讀範圍，報告一律留在受審 Repo。檔名例子為 `release-review-20261014T063454Z-12345678-abcdef12.md`；時間是 UTC，兩段短 SHA 對應本次基礎與比較版本。

### 如何填寫 Checklist

開啟報告的「範圍與限制 → 新增部署依賴與人工核對 → 人工核對紀錄」。每個新增資源有對應核對項與文件內連結；只更新 `release-checklist:start` 與 `release-checklist:end` 之間的紀錄。

| 狀態 | 使用方式 |
| --- | --- |
| 待確認 | 初始狀態；尚未實際驗證，或只有申請工單／預定執行時間 |
| 已確認 | 已完成開通或套用；填核對人、日期及實際驗證證據 |
| 未通過 | 實際核對失敗；記錄原因並安排修正 |
| 不適用 | 本次部署不需要此項；填具體理由 |

例如 Kafka Topic 已建立且服務帳號已通過發送測試，才可把對應項目改為「已確認」，填負責人、日期及測試／開通證據。人工填寫保留產生時判斷，不能把原始結論自行改成部署核准。

新版本重新審查會產生另一份報告，核對項重新設為待確認；歷史報告及舊的人工紀錄保留。

## 單一 Release Report

- 一般資料夾、Group 或已儲存工作區：一律存於受審 Repo 的 review-reports。
- 未儲存 multi-root 或根目錄不明：先釐清，不推測位置。
- 每次只產生 release-review-<UTC時間>-<base8>-<head8>.md；重名續號，保留歷史與人工紀錄。

進度、問答及文件共用[報告風格](skills/release-reviewer/references/report-style.md)：首句給判斷、靜態檢查狀態及摘要，接問題數量；比較資訊表列輸入分支、實際 ref 及短 SHA，問題依序使用「問題描述、重現原因、預期結果、實際結果、證據、建議改善方式」。範圍、已執行／未執行檢查及依賴 Checklist 展開顯示，下一步獨立成節，完整技術審查紀錄於末尾收合。

| 產生時判斷 | 條件 |
| --- | --- |
| 存在已確認阻擋 | 有直接 P0／P1 或合併衝突 |
| 檢查未完成 | 無已確認阻擋，但證據、覆蓋或功能鏈有缺口 |
| 待核對上版條件 | 靜態範圍完整、無阻擋，但有必要 Checklist |
| 已檢查範圍未發現阻擋 | 靜態範圍完整、無阻擋及新增核對項 |

Checklist 包含設定、DB、FTP、SFTP、Redis、Kafka 與其他資源的防火牆、權限、建立／套用及相容性核對。初始全部待確認；已確認需核對人、日期及開通／套用後證據，不適用需理由，僅有申請工單不算完成。只在 release-checklist:start／end 邊界內填寫紀錄，固定正文摘要及產生時判斷保持不變；新版本重新核對。

預設靜態檢查，不連 GitLab API、DB 或實際環境；靜態完成不等於部署核准。語意由執行技能的 agent 審查，helpers 驗證固定來源、證據、覆蓋與核對結構。問題證據保留各固定來源有引用行號的安全程式片段及內容摘要，暫存物件清理後仍可閱讀；設定值與認證內容省略。v2 的非空文字問題證據（含設定／私鑰）必須指定行號；只有二進位及空檔可無行號。歷史草稿的一般文字證據無行號時顯示未擷取說明。

參考 [v3 單檔示範](docs/demo/v3/README.md)、[0.3.0 驗證](docs/verification-v3.md)；[歷史 v2](docs/demo/v2/README.md)與 v1 報告保留。

## 開發與契約

~~~powershell
python -X utf8 -B -m unittest discover -s tests -v
python -X utf8 -B scripts/build_demo.py
python -X utf8 -B scripts/package_release.py
~~~

打包輸出 0.7.0 ZIP 與 SHA-256，ZIP 只有可安裝的 release-reviewer 目錄，不覆寫舊發行包。測試不需要第三方套件；CI 在 Linux／Windows 的 Python 3.10、3.14 執行測試與打包。

main 與手動驗證會保存完整矩陣證據及 Linux／Python 3.14 的正式 ZIP／SHA-256，附件保留十四天。推送版本 tag 時，只重用七天內且最終 commit、工作流程、依賴及矩陣識別相同的 main 證據；證據或附件缺漏就重跑完整矩陣。發布直接使用已驗證的附件，已有相同 tag／附件可重試，內容不同則停止；PR 與本機測試紀錄不能取代正式 CI 證據。

Context、Draft、Report 統一使用 v3。Context 保存 report_root；report 的 release_assessment 由 helper 推導。publish_report(context, draft_path) 回傳一個 Path；CLI 只回傳 markdown_report。v3 移除 --report-dir、checklist_report、checklist_file 與整份正文 body_sha256，改用排除人工區塊的 static_body_sha256。缺少 coverage_policy 的舊 v3 context 沿用原覆蓋規則，不能使用新的 not-applicable 狀態；未知 policy 拒絕。v1／v2 context／draft 不適用此 v3 流程，舊報告不改寫。

Git 與 owned-session 程式內附，無 MergeReviewer 執行期依賴；預設在報告成功、失敗或中止後清理本次 owned 暫存證據。啟用下節修稿重試時，可修正草稿錯誤會保留同次 context。

## 0.4.0 批次證據與修稿重試

本版新增批次固定證據操作、單次驗證快取、待讀清單與未審查草稿骨架。審查範圍、完整搜尋、v3 報告與人工 Checklist 契約保持一致；逐項語意審查後才標為 reviewed。

擷取完成後可直接執行 `evidence.py batch --context-dir <context> --requests <context>/review-worklist.json`。後續讀取與搜尋沿用同一批次介面；批次逐項回傳結果及錯誤，搜尋仍保存完整命中與各自 search_id。細節見[證據操作](skills/release-reviewer/references/evidence-workflow.md)。

已知必要行段可用 `read --line-start/--line-end` 或批次 `line_start`／`line_end`，保留原始行號及全文 hash。搜尋結果用 `view --search-id <64位hex>` 篩選原 artifact 再分頁，保留批次錯誤與完整搜尋紀錄，無須重跑搜尋；未使用新參數時沿用原輸出格式。

批次可明確加上 `--shared-content`，選用版本化的共享全文輸出：相同原始內容只輸出一次，每筆固定來源與覆蓋仍分別保留；省略旗標時沿用原格式。發佈在單次驗證內共用整檔快照分析，各引用的來源、行號及機密遮蔽照常驗證；Context／Draft／Report v3、patch 暫存產物及清理流程不變。

可相容選用 `git_review_context.py --summary`，只回傳身分、版本、catalog、合併狀態、完整缺口、範圍計數及證據檔路徑；完整 manifest 照常落盤。`evidence.py batch --auto-content` 執行證據一次，再按 compact UTF-8 JSON bytes 選較小的 normal／shared 格式，相等選 normal；它與 `--shared-content` 互斥。既有無旗標 CLI 行為保持原樣，技能採用這兩個選項。

靜態必要的 caller／consumer／mapping／動態目標缺證據時仍保留未完成；僅部署、ACL、ACK／retry 或 provider 內部實作未知，列限制及既有 Checklist。條件式設定 P1、新增事件 schema 提醒及真正的 unknown 均保留，詳見[靜態缺口邊界](skills/release-reviewer/references/review-rules.md#靜態缺口與環境核對)。

外部類型按固定 provider 證據判斷；抽象 bus／Topic 不能直接認定為 Kafka。provider 未知但 Topic／schema 已辨識時，用既有 `other` 類型保留契約提醒與限制，不產生無依據的 Kafka 專屬核對項。

技能發布命令增加 `--retain-on-validation-error`：可修正草稿錯誤以 exit 3 保留同次 context，修正後沿原 context 完整重驗；來源／搜尋完整性、Git、逾時及輸出失敗仍終止清理、回報後停止，不自動重取。成功路徑只驗證一次，舊呼叫的失敗清理行為不變；中止或放棄修稿須清理保留的 context。

可重跑的效能比較與驗證界線見[效能驗證](docs/performance.md)。0.4.0 提供獨立 ZIP 與 SHA-256，並由 GitlabWorkSpace 0.24.4 整合正式附件；量測界線及歷史樣本保留於該文件。

操作計數、完整證據分頁及清理規則見[操作與負載](skills/release-reviewer/references/runtime-controls.md)；既有量測界線見[執行期優化](docs/runtime-optimization.md)。本輪驗證 helper 回歸，未新增模型品質 benchmark。
