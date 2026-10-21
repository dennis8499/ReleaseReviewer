# 審查與報告規則

## 新增使用的判斷

先理解變更用途與最終受審版本，再搜尋 base_sha 的整個 Repo。比對的是執行程式的使用語意，不能只比較字串或檔名。必要時搜尋別名、實體 mapping、SQL fragment、連線鍵及其解析結果。

| 情境 | 判斷 |
| --- | --- |
| 新檔案使用原本其他執行程式已用的設定／同一 DB 物件 | 不列新增提醒 |
| 只有未使用的設定定義，或該既有定義的值變更；沒有本次實際讀取、部署 binding 或套用定義證據 | 交代已查範圍；不列 first-use／必要外部 consumer／靜態缺口 |
| base 有設定或 schema 定義，但此次才有程式使用 | 列首次使用提醒；說明定義已存在 |
| 同一連線鍵的目標由 DB A 改成 DB B | 列 DB B 新增使用提醒；不能只用連線鍵相同而略過 |
| 既有資料表此次多用一個欄位 | 列新欄位，既有資料表不重複列 |
| 同名資料表但連線、DB 或 schema 不同 | 分開判定 |
| 新增且會隨部署套用的資源定義 | 列 resource-definition；不要求已有執行程式呼叫 |
| 既有依賴新增存取操作或改指目標 | 列 access-change／target-change，核對新增權限與連線 |
| 搬檔、等價改名、註解、測試或範例 | 不誤列正式上版新增依賴；測試所需依賴可放限制 |
| 設定來自環境變數或部署平台 | 標需確認外部注入；不聲稱環境缺少 |
| 新 DB 物件只有程式使用證據 | 提醒核對上版準備；不聲稱尚未建立 |
| 動態 SQL 或 DB 目標由外部決定 | 列已知部分、待確認原因與核對方式 |

設定是否被使用與設定值是否改變分開判斷。正例：base 只有 `worker_count` 定義，head 新增固定 loader 並以該鍵建立工作池，列首次使用提醒且引用實際讀取與 base 全 Repo 搜尋。反例：定義檔的 `worker_count` 從 4 改 8，固定範圍沒有讀取或部署 binding，搜尋 loader 零命中只表示未找到使用，不證明存在必要外部 consumer；不新增提醒或 gap。若固定部署 manifest 明確綁定該檔／鍵，或此次新增會套用的部署資源定義，即使讀取者在外仍按該確切證據判定提醒與必要缺口。

搜尋即使命中，也可能只有定義或註解；零命中也可能有別名或 ORM mapping。baseline_reason 必須記錄具體推論，baseline_searches 保存實際全 Repo 搜尋的 search_id。沒有搜尋紀錄不得發佈新增提醒。

## 程式風險分級

| 等級 | 行動 | 依據 |
| --- | --- | --- |
| P0 | 立即處理 | 一般情況即造成大範圍服務不可用、重大安全暴露或資料毀損 |
| P1 | 上版前優先修正 | 常見流程有高影響正確性、相容性或資料問題 |

只收錄直接阻止上版的 P0／P1：啟動／建置失敗、必要流程中斷、資料毀損、安全暴露或無法相容的契約等。一般局部缺陷與 P2／P3 改善不列入。blocking_reason 必須說明為何本次部署會失敗或不可接受，不能只重述等級。

缺少必要設定只有在能證明觸發失敗時才列程式風險；新增設定／DB 清單仍保留上版準備資訊，兩者用途不同。同一根因風險合併列出。每個風險至少引用一個 reviewed 的變更路徑，並確認問題在最終受審版本仍存在；基礎或 parent 證據可補充因果。

## 靜態缺口與環境核對

以本次變更的必要判斷為界：已知需要的 caller／consumer 固定版本、欄位 mapping 或動態目的地無法取得／解析，影響行為或相容性判斷時，保留 coverage／chain_gaps 或設定、資源的 unknown，結果維持未完成。明確入口可作為功能鏈起點；不能只因未取得任意外部 caller，就推定本次必要鏈路缺失。

已知程式使用、契約與目標足以判斷，僅無法查證實際部署、ACL、broker ACK／retry 或 provider 內部實作時，在 limitations 說明未查範圍，新增依賴仍用既有 Checklist 核對；這些未知本身不新增靜態 gap。若 provider 的必要回應／契約本身缺失，仍保留真正缺口。helper 原樣保留草稿 gap，由 Agent 根據證據判斷，不能為完成率刪掉真缺口。

例如固定程式以 `Redis.from_url` 讀取部署注入的 URL，並操作明確的 `invoice:` key prefix：Redis provider、連線鍵與邏輯資源已辨識，`resolution` 為 `identified`。現場 URL、主機、DB index、ACL 與可用性留 Checklist／limitations，不因未連線現場而建立 `chain_gaps`。真正未知的動態表名、未取得的必要 mapping 或消費契約仍保留缺口。

條件式設定在可證明的觸發條件下造成 P1 時，保留條件與風險；新增事件 Topic／payload schema 仍依[資源分類](resource-checks.md#外部類型與-provider-證據)提醒，不因 provider 未知省略已辨識的契約。

## 搜尋引用對帳

每次引用搜尋結論時，從同一筆真實 helper 紀錄取得 `search_id`、Repo、source、完整 ref、query 與命中數；先比對該筆紀錄，再將 ID 帶入 `baseline_searches` 或必要的正文引用。零命中與有命中都按此規則，不能把另一個查詢的 ID 套在同一段推論。優先保留發布器自動產生的搜尋摘要，不另手寫第二份 ID／命中數對照表。若正文需要提搜尋 ID，發布前逐筆對帳；搜尋 query 留在 owned 紀錄，不將機密值或命中內容複製到報告。

## 確切 Repo 的 ownership 修復

修復限首次完整快照及語意分析以前的本機查核。先對使用者明確宣告的候選 Git 根目錄執行本機唯讀 ownership preflight，保存首次 `dubious ownership` 原始診斷並核對報錯 Repo 確在授權範圍，再把所有實際報錯的候選確切路徑一次列入同一 helper 子程序的 `GIT_CONFIG_COUNT`／`GIT_CONFIG_KEY_n=safe.directory`／`GIT_CONFIG_VALUE_n=<exact-path>`，讓 capture 一次完成；原環境已有 count 時在其後追加並保留既有值。設定只限該 helper 及其 Git 子程序；不得修改全域、使用萬用路徑或擴張工作區。單次直接 Git 查詢可用 `git -c safe.directory=<exact-path>`。語意分析已開始或遠端已完成同步時，維持固定 SHA 與缺口，不為 ownership 自動再 fetch、重取或切換快照；真正缺失的必要 HEAD／物件仍是缺口。獨立審查原有的自行同步政策不變。

發布 helper 的 stdout 接回呼叫者或寫到本次 run 的交接位置，不重新導向進 owned context；發布器驗證、寫檔及清理時，避免 context 內開啟的輸出句柄阻擋清理。

## 固定證據與覆蓋

證據物件格式：

~~~json
{"repo":"entry","source":"head","ref":"完整 commit SHA","path":"src/service.cs","line_start":12,"line_end":16}
~~~

source 與 ref 必須相符：

- base：base_sha，新增判斷的唯一基準。
- head：head_sha。
- merge-base：merge_base，僅供分支變更與歷史判斷。
- merge-preview：乾淨預覽的 tree_sha，合併前模式的最終內容。
- merge-parent：manifest 已記錄 merge commit 的 parent SHA。
- supporting：catalog 指定關聯 Repo 擷取時已提交 HEAD 的固定 SHA，包含 detached HEAD；不納入工作檔或其他 ref 的新版本。

merge-preview 的文字證據由 helper 保存引用行的非機密片段與原始檔案 content_sha256，放入同一報告的可展開證據區，清理暫存合併物件後仍可閱讀。設定檔只記錄可解析鍵名，不保存設定值；認證、私鑰或完整連線設定行省略並明示。沒有行號時只保存摘要，不聲稱已保存完整來源。

行號可省略（例如空檔案）；如指定就必須提供有效起訖行。同一路徑在多個比較範圍出現，coverage 的 evidence 必須包含每個對應版本：

| 比較範圍 | 一般新增／修改路徑 | 刪除／rename 舊路徑 |
| --- | --- | --- |
| changed_files（共同起點或 base → head） | head | merge-base 或 base |
| merge_preview.changed_files（head → 預覽） | merge-preview | head |
| release_changes（base → 最終版本） | merge-preview | base |

每個新舊路徑恰有一個 coverage 項目。status 可為 reviewed、metadata-only、not-reviewed；後兩者要有 reason，結果維持未完成。二進位或 gitlink 不用文字行號假裝內容已查證。不要將 context_complete（證據備妥）視為語意審查已完成。

## 草稿格式

頂層 schema 固定為 ReleaseReviewDraft/v3，必要欄位：

~~~json
{
  "schema": "ReleaseReviewDraft/v3",
  "summary": "一句話說明主要風險與上版提醒。",
  "coverage": [],
  "code_risks": [],
  "config_changes": [],
  "db_changes": [],
  "external_changes": [],
  "supporting_coverage": [],
  "functional_chain": [],
  "chain_gaps": [],
  "dependency_coverage": [],
  "next_steps": ["開發核對本次風險；維運確認外部設定及 DB 準備事項。"],
  "tests_executed": [],
  "tests_not_executed": ["本次只做靜態審查，未執行專案測試、部署或 DB 查詢。"],
  "limitations": []
}
~~~

以上是欄位示意，不能原樣當成完成草稿。擷取產生的草稿骨架與待讀清單共用相同覆蓋計算；預填引用只表示必要來源，status 初始為 not-reviewed。coverage 只有沒有變更時可空白；dependency_coverage 必須逐類列 config、db、ftp、sftp、redis、kafka、other，欄位 kind、status（reviewed／not-reviewed）、reason、evidence。reason 說明已查範圍與沒有新增項目的依據；有變更時 reviewed 必須引用最終入口版本。未完成類型阻止完整狀態。Group 欄位依 group-review.md 填列。

### coverage 項目

~~~json
{"path":"src/service.cs","status":"reviewed","evidence":[{"repo":"entry","source":"head","ref":"完整 SHA","path":"src/service.cs","line_start":1,"line_end":10}]}
~~~

未完成項目可使用空 evidence，另附 reason。合併前模式通常也需對同一路徑附 merge-preview 證據，依上表處理。

### code_risks 項目

新草稿保留骨架的 `report_layout=engineering-review/v2`。每項風險必要欄位為 id、priority、title、blocking_reason、trigger、impact、expected、actual、recommendation、scenario_source、evidence、scenario、technical_details；缺少面向讀者或工程師的說明須先完成，驗證器不讓新草稿用「未提供」發布。

`expected`、`actual` 由審查者依證據分別撰寫預期行為與固定版本的實際行為；靜態推導仍標未重現。`scenario` 是操作情境，`trigger` 是技術觸發條件；`technical_details` 的 `explanation`、`impact_scope`、`engineering_fix` 分別說明程式原因、確切影響邊界與工程修正。`impact`、`recommendation` 是面向讀者的影響與建議。歷史 v1／無 layout 的 v3 草稿仍可讀取，缺少 expected／actual 時顯示「未提供」；v1 仍要求原有 scenario／technical_details 完整。

- 編號從 R-001 連續排列，依 P0、P1 排序。
- trigger 描述能實際觸發問題的輸入或操作；impact 描述使用者、資料或服務受到的影響。
- scenario_source 為 code-derived 或 reproduced；後者必須在 tests_executed 記錄實際執行及結果。
- evidence 為上述固定證據陣列，不可只引用舊版而未確認最終行為。
- v2 問題證據的非空文字檔須附 line_start／line_end（含設定／私鑰），保留所有來源的安全程式片段；設定／私鑰內容仍依原有規則省略。只有二進位及空檔可無行號並保留明確說明。歷史未指定行號的一般文字證據標「未指定行號範圍，未擷取程式片段」。此要求不套用至覆蓋或依賴證據。
- 跨 Repo 風險仍須引用入口 Repo 的已審查變更及最終版本；關聯證據補充影響，不可將無關 Repo 的既有問題列為本次風險。

### 完整風險示例

以下只示範欄位與不同讀者所需的說明，來源路徑、行號及 SHA 必須替換為本次實際證據：

~~~json
{
  "id":"R-001", "priority":"P1", "title":"新匯出流程可讀取其他租戶的資料",
  "blocking_reason":"正式匯出端點可能跨租戶洩漏客戶資料，上版前必須修正。",
  "impact":"使用者可能取得不屬於自己公司的匯出內容。",
  "expected":"下載端點只交付登入租戶擁有的工作，拒絕其他租戶的工作編號。",
  "actual":"端點只依 job_id 取回檔案，未比對 tenant_id，可能交付其他公司的內容。",
  "scenario":"A 公司員工以另一份匯出工作的編號下載檔案，系統卻交付 B 公司的內容。",
  "recommendation":"先修正下載的租戶權限核對，再開放新匯出功能。",
  "trigger":"已登入使用者呼叫新下載端點，job_id 指向另一個 tenant 的工作。",
  "technical_details":{
    "explanation":"新增查詢只以 job_id 取回工作；回傳檔案前沒有比對登入身分的 tenant_id。",
    "impact_scope":"新下載端點可存取的跨租戶匯出工作；一般同租戶下載不受此差異影響。",
    "engineering_fix":"以 job_id 與登入 tenant_id 共同限制查詢，拒絕跨租戶存取並驗證回應狀態。"
  },
  "scenario_source":"code-derived",
  "evidence":[{"repo":"entry","source":"merge-preview","ref":"本次最終 tree 完整 SHA","path":"src/export.py","line_start":18,"line_end":24}]
}
~~~

### config_changes 項目

必要欄位為 id、kind、name、description、baseline_reason、baseline_searches、recommendation、evidence、definition_status、definition_evidence。

新擷取中的每筆 config／DB／external 新增依賴與每條 functional_chain 均須填 `diff_roots`（manifest 的 `DIFF-...` ID 陣列）及 `diff_reason`（與本次差異的因果說明）。完整 baseline 搜尋或未變更程式可作佐證，但不能讓無關既有依賴成為本次提醒。例：`"diff_roots":["DIFF-001"],"diff_reason":"本次新增發送呼叫，首次需要事件發布權限。"`。

- 編號 C-001 起；kind 為 config-key、environment-variable 或 config-file。
- name 是設定鍵／變數名／設定檔路徑，不放設定值；description 說明用途、必要性與預設值的行為。
- definition_status 為 defined、missing-definition、external、unknown。
- defined 必須在 definition_evidence 附最終版本的定義位置；其他狀態仍可附相關載入或部署證據。
- evidence 必須包含最終受審版本的使用、目標或此次部署定義位置。baseline_searches 是非空 search_id 陣列。
- missing-definition 表示未找到版本內定義；external 表示有依據的外部注入；unknown 表示解析／證據不足。不要因讀取環境變數就自動判 missing-definition。
- 同一設定不重複列出；移除既有設定造成的具體失敗放程式風險。

### db_changes 項目

必要欄位為 id、kind、name、connection、resolution、description、baseline_reason、baseline_searches、recommendation、evidence。

- 編號 D-001 起；kind 為 connection、database、table、column、view、procedure。
- name 使用可辨識的完整非機密物件名稱，例如 BillingDb.dbo.Invoice.tax_code；connection 是連線鍵／邏輯名稱，不放完整連線字串。
- resolution 為 identified 或 unknown；動態來源可用「待解析的 BillingDb 動態資料表」等忠實描述。
- evidence 包含最終版本的程式使用、連線目標或此次部署資源定義位置；必要時補 ORM mapping／設定定義證據。
- 同一 connection、kind、name 去重。不同 DB／schema 的同名物件不要錯誤合併。
- reminder 只要求確認所列依賴的上版準備；不把缺少 SQL／migration 判為環境物件不存在。

### 外部資源與新增判定

external_changes 必要欄位與 db_changes 相同，編號 E-001 起，kind 為 ftp、sftp、redis、kafka、other。connection 是邏輯連線／服務帳號用途識別，name 是非機密資源名稱，例如 Kafka 的 Topic、Redis DB／key prefix、SFTP 目錄。完整新增辨識與核對項見 resource-checks.md。

三類提醒皆可帶 introduction：first-use（預設）、resource-definition、target-change、access-change。此次新增且會隨部署套用的定義，即使尚無執行程式呼叫，也要以 resource-definition 提醒，引用最終定義及 base 全 Repo 搜尋；不能把定義存在誤認為既有程式使用或環境已建立。

不要在草稿預填 checklist；helper 依每項已驗證提醒產生非空 checklist_ids 與初始待確認核對項。人工在同一 Release Report 的 Checklist 區塊記錄核對人、日期、工單／開通驗證證據及不適用理由，產生時判斷不因人工填寫而改寫。

## 發佈結果與判讀

發佈使用 ReleaseReviewReport/v3；保留各類結果、coverage、依賴類型覆蓋、功能鏈與關聯覆蓋、搜尋摘要、所有 Repo 版本 identity、review_complete、chain_complete、context_gaps、限制及執行紀錄。新 Context 的 report_root 固定為入口 Repo/review-reports，只輸出一份含 Checklist 的 Markdown。內部 CLI 只回傳 markdown_report；歷史報告保留。

review_complete 只代表此靜態範圍的覆蓋完整。context／功能鏈缺口、未覆蓋路徑、未檢查依賴類型、設定或資源 unknown 使結果為「檢查未完成」；明確的 external 設定注入仍可完成靜態檢查。environment_status 與靜態完成分開，初始不宣告上版準備完成。沒有差異時明確說沒有差異。完成且沒有缺陷時說「已檢查範圍未發現有證據的程式缺陷」，不宣告保證安全上版。

report 的 release_assessment 由 helper 推導，優先序為 blocked（有直接程式阻擋或 Git 合併衝突）、incomplete（靜態缺口）、pending-confirmation（有必要待核對項）、no-blockers-found。另保存 blocker_count、pending_count、gap_count 與 review_gaps。草稿不指定判斷或預填人工確認。

報告含 release-review-report 中繼資料及 static_body_sha256，摘要排除唯一的 <!-- release-checklist:start --> 至 <!-- release-checklist:end --> 人工區塊（含邊界），仍涵蓋全部固定正文。只有此區塊可填寫，其他修改視為快照改變。v3 沒有 checklist_file／checklist_filename／body_sha256。版面依 report-style：結論與數量置頂，先比較資訊與六區塊問題，再範圍與限制、下一步，技術審查紀錄於末尾收合。依賴、Checklist、已執行／未執行檢查保持展開。搜尋字串留在 owned 搜尋紀錄，報告只顯示 ID 與命中數。

本階段沒有 MR 身分，不冒用 MergeReviewReport/v1 或已有的 GitlabWorkSpace 匯入格式。發布預設成功或失敗後清理 owned context；技能使用 --retain-on-validation-error 時，只在可修正草稿錯誤保留同次來源，修正後完整重驗。來源完整性、搜尋紀錄竄改、Git／逾時與輸出失敗仍終止清理；中止或放棄修稿也清理。報告保留所有必要版本、路徑與搜尋摘要。
