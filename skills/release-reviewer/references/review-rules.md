# 審查與報告規則

## 新增使用的判斷

先理解變更用途與最終受審版本，再搜尋 base_sha 的整個 Repo。比對的是執行程式的使用語意，不能只比較字串或檔名。必要時搜尋別名、實體 mapping、SQL fragment、連線鍵及其解析結果。

| 情境 | 判斷 |
| --- | --- |
| 新檔案使用原本其他執行程式已用的設定／同一 DB 物件 | 不列新增提醒 |
| base 有設定或 schema 定義，但此次才有程式使用 | 列首次使用提醒；說明定義已存在 |
| 同一連線鍵的目標由 DB A 改成 DB B | 列 DB B 新增使用提醒；不能只用連線鍵相同而略過 |
| 既有資料表此次多用一個欄位 | 列新欄位，既有資料表不重複列 |
| 同名資料表但連線、DB 或 schema 不同 | 分開判定 |
| 新增且會隨部署套用的資源定義 | 列 resource-definition；不要求已有執行程式呼叫 |
| 既有依賴新增存取操作或改指目標 | 列 access-change／target-change，核對新增權限與連線 |
| 搬檔、等價改名、註解、測試或範例 | 不誤列正式上板新增依賴；測試所需依賴可放限制 |
| 設定來自環境變數或部署平台 | 標需確認外部注入；不聲稱環境缺少 |
| 新 DB 物件只有程式使用證據 | 提醒核對上板準備；不聲稱尚未建立 |
| 動態 SQL 或 DB 目標由外部決定 | 列已知部分、待確認原因與核對方式 |

搜尋即使命中，也可能只有定義或註解；零命中也可能有別名或 ORM mapping。baseline_reason 必須記錄具體推論，baseline_searches 保存實際全 Repo 搜尋的 search_id。沒有搜尋紀錄不得發佈新增提醒。

## 程式風險分級

| 等級 | 行動 | 依據 |
| --- | --- | --- |
| P0 | 立即處理 | 一般情況即造成大範圍服務不可用、重大安全暴露或資料毀損 |
| P1 | 上板前優先修正 | 常見流程有高影響正確性、相容性或資料問題 |
| P2 | 排程修正 | 有具體觸發條件的較局部缺陷 |
| P3 | 後續改善 | 影響較低但可證明的缺陷 |

缺少必要設定只有在能證明觸發失敗時才列程式風險；新增設定／DB 清單仍保留上板準備資訊，兩者用途不同。同一根因風險合併列出。每個風險至少引用一個 reviewed 的變更路徑，並確認問題在最終受審版本仍存在；基礎或 parent 證據可補充因果。

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
- supporting：catalog 指定關聯 Repo 的本機預設分支固定 SHA，不能引用該 Repo 的 checkout 或 remote-tracking 版本。

行號可省略（例如空檔案）；如指定就必須提供有效起訖行。同一路徑在多個比較範圍出現，coverage 的 evidence 必須包含每個對應版本：

| 比較範圍 | 一般新增／修改路徑 | 刪除／rename 舊路徑 |
| --- | --- | --- |
| changed_files（共同起點或 base → head） | head | merge-base 或 base |
| merge_preview.changed_files（head → 預覽） | merge-preview | head |
| release_changes（base → 最終版本） | merge-preview | base |

每個新舊路徑恰有一個 coverage 項目。status 可為 reviewed、metadata-only、not-reviewed；後兩者要有 reason，結果維持未完成。二進位或 gitlink 不用文字行號假裝內容已查證。不要將 context_complete（證據備妥）視為語意審查已完成。

## 草稿格式

頂層 schema 固定為 ReleaseReviewDraft/v2，必要欄位：

~~~json
{
  "schema": "ReleaseReviewDraft/v2",
  "summary": "一句話說明主要風險與上板提醒。",
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

以上是欄位示意，不能原樣當成完成草稿。coverage 只有沒有變更時可空白；dependency_coverage 必須逐類列 config、db、ftp、sftp、redis、kafka、other，欄位 kind、status（reviewed／not-reviewed）、reason、evidence。reason 說明已查範圍與沒有新增項目的依據；有變更時 reviewed 必須引用最終入口版本。未完成類型阻止完整狀態。Group 欄位依 group-review.md 填列。

### coverage 項目

~~~json
{"path":"src/service.cs","status":"reviewed","evidence":[{"repo":"entry","source":"head","ref":"完整 SHA","path":"src/service.cs","line_start":1,"line_end":10}]}
~~~

未完成項目可使用空 evidence，另附 reason。合併前模式通常也需對同一路徑附 merge-preview 證據，依上表處理。

### code_risks 項目

必要欄位為 id、priority、title、trigger、impact、recommendation、scenario_source、evidence。

- 編號從 R-001 連續排列，依 P0 至 P3 排序。
- trigger 描述能實際觸發問題的輸入或操作；impact 描述使用者、資料或服務受到的影響。
- scenario_source 為 code-derived 或 reproduced；後者必須在 tests_executed 記錄實際執行及結果。
- evidence 為上述固定證據陣列，不可只引用舊版而未確認最終行為。
- 跨 Repo 風險仍須引用入口 Repo 的已審查變更及最終版本；關聯證據補充影響，不可將無關 Repo 的既有問題列為本次風險。

### config_changes 項目

必要欄位為 id、kind、name、description、baseline_reason、baseline_searches、recommendation、evidence、definition_status、definition_evidence。

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
- reminder 只要求確認所列依賴的上板準備；不把缺少 SQL／migration 判為環境物件不存在。

### 外部資源與新增判定

external_changes 必要欄位與 db_changes 相同，編號 E-001 起，kind 為 ftp、sftp、redis、kafka、other。connection 是邏輯連線／服務帳號用途識別，name 是非機密資源名稱，例如 Kafka 的 Topic、Redis DB／key prefix、SFTP 目錄。完整新增辨識與核對項見 resource-checks.md。

三類提醒皆可帶 introduction：first-use（預設）、resource-definition、target-change、access-change。此次新增且會隨部署套用的定義，即使尚無執行程式呼叫，也要以 resource-definition 提醒，引用最終定義及 base 全 Repo 搜尋；不能把定義存在誤認為既有程式使用或環境已建立。

不要在草稿預填 checklist；helper 依每項已驗證提醒產生非空 checklist_ids 與初始待確認核對項。人工在獨立 Checklist 記錄核對人、日期、工單／開通驗證證據及不適用理由，主報告不因人工更新而改寫。

## 發佈結果與判讀

發佈使用 ReleaseReviewReport/v2；保留各類結果、coverage、依賴類型覆蓋、功能鏈與關聯覆蓋、搜尋摘要、所有 Repo 版本 identity、review_complete、chain_complete、context_gaps、限制及執行紀錄。固定輸出主報告與獨立 Checklist Markdown。

review_complete 只代表此靜態範圍的覆蓋完整。context／功能鏈缺口、未覆蓋路徑、未檢查依賴類型、設定或資源 unknown 使結果為「檢查未完成」；明確的 external 設定注入仍可完成靜態檢查。environment_status 與靜態完成分開，初始不宣告上板準備完成。沒有差異時明確說沒有差異。完成且沒有缺陷時說「已檢查範圍未發現有證據的程式缺陷」，不宣告保證安全上板。

報告含獨立 release-review-report 中繼資料及正文摘要，供後續 adapter 使用；本階段沒有 MR 身分，不冒用 MergeReviewReport/v1 或已有的 GitlabWorkSpace 匯入格式。成功或失敗後清理 owned context，報告保留所有必要版本、路徑與搜尋摘要。
