# 上版檢查: orders-service

## 審查結論

**產生時判斷**｜檢查未完成。已讀取固定提交與最終合併預覽，但 session helper 因路徑處理錯誤終止，未完成搜尋紀錄核驗、check 與 finish；本文件為中止紀錄及人工部署核對表，並非 helper 驗證通過的 ReleaseReviewReport/v3。

P0：0 項已確認；P1：0 項已確認；已確認程式阻擋：0；合併衝突：0；部署待確認：7 組；必要缺口：1 組（搜尋證據與最終驗證未完成）。以上問題數量不能作為完整審查無阻擋的結論。

入口 Repo：orders-service。比較方向：feature/review（bf16845e）→ main（c5872abe）。未驗證實際環境，未核准上版。

## 問題總攬

已讀範圍未確認有證據的直接 P0／P1 阻擋。正式靜態審查尚未完成，應保留未完成狀態。

## 問題詳情（工程師參考）

未建立 R-* 程式問題。此次 app.py 新增 publish_tax，依序寫入 BillingDb.dbo.InvoiceTax.tax_rate，呼叫 FTP upload，再呼叫 SFTP put；原 checkout → publish → consume 流程未改動。新增 migration 建立 InvoiceTax 與 tax_rate 欄位。

上述為固定程式的靜態閱讀結果，未執行或重現。不能由提供 migration 推定正式環境已建立資料表，也不能由抽象 ftp／sftp 參數推定網路、認證、檔案內容或重送行為已確認。

## 範圍與限制

本次已讀三個變更路徑的必要版本：README.md、app.py、deploy/migrations/20261029_invoice_tax.sql，涵蓋 head 與 merge-preview；另讀 base／最終版本的 bootstrap.py、schema.sql、settings.json 及完整 release.patch。擷取結果未列出適用 AGENTS.md 或 workspace instructions，catalog 僅包含入口 Repo，沒有 supporting Repo。

### 必要缺口與中止原因

1. 初次全 Repo baseline 搜尋在保存 searches/<hash>.json 時遇到 Windows 路徑長度限制；未形成可引用的搜尋紀錄。針對單一 InvoiceTax 查詢的嘗試也在保存階段失敗，不能用來證明 base 無既有使用。程序內長路徑相容處理其後觸發 maximum recursion depth exceeded，session 狀態為 failed／terminal。check、finish、正式依賴驗證、搜尋重播與發布均未完成。依技能終止規則停止本次 session，未重新 capture。已用 ownership helper 清理本次 owned context；固定 SHA 與操作摘要保留於此紀錄。

### 已辨識的部署核對範圍

下列項目來自已讀最終程式與 migration，是待人工核對的範圍；因 baseline 搜尋未成功保存，未宣稱已通過 first-use／resource-definition 新增判定驗證。

| 類型 | 已讀證據與判斷 | 尚待核對 |
| --- | --- | --- |
| 設定 | app.py:15 將 TAX_SFTP_KEY、TAX_SFTP_HOSTKEY 作為名稱傳給 SFTP adapter；settings.json 有 billing_db_connection 定義，但固定程式未顯示讀取與部署 binding。 | SFTP adapter 如何解析名稱；不把未使用 billing_db_connection 定義列為已確認首次使用。 |
| DB | app.py:11 寫入 BillingDb.dbo.InvoiceTax.tax_rate；migration:1 建立對應資料表與 DECIMAL(5, 2) NOT NULL 欄位。base schema.sql 定義 CoreDb.dbo.Orders，bootstrap.py 查詢 orders。 | db 參數的服務身分及連線、BillingDb 存取、migration 順序、寫入權限及資料精度。 |
| FTP | app.py:12–13 呼叫 upload，使用 port 21、passive 模式、tax-exporter 帳號用途、/incoming/tax 目錄及 daily-tax.csv 檔名。 | 目標服務、被動資料通道、認證、目錄與寫入／覆寫權限。 |
| SFTP | app.py:14–16 呼叫 put，使用 port 22、tax-importer 帳號用途、/archive/tax 目錄及 daily-tax.csv 檔名。 | 網路、認證部署、主機金鑰核對及目錄權限。 |
| Redis | 已讀變更與固定輔助程式未見 Redis 呼叫。 | 全 Repo 搜尋與正式依賴覆蓋核驗未完成，保留限制。 |
| Kafka | 已讀變更與固定輔助程式未見 Kafka client 或事件 broker；publish／consume 為本機函式。 | 不因函式名稱推定 Kafka；正式依賴覆蓋核驗未完成。 |
| 其他 | db、ftp、sftp 為呼叫端注入參數；未擷取其具體 provider／adapter 實作。 | 本機未查檔案產生、傳送確認、交易、失敗重試及冪等行為；不據此杜撰 P1。 |

### 可編輯部署 Checklist

初始均為待確認。已確認需填核對人、日期與套用／開通後的驗證證據；申請單或排程本身仍為待確認。不適用必須填具體理由。本區可編輯，人工填寫不會把上方靜態未完成狀態改成審查完成。

<!-- release-checklist:start -->
| ID | 核對項目 | 狀態 | 核對人 | 日期 | 驗證證據／不適用理由 |
| --- | --- | --- | --- | --- | --- |
| CK-001 | 確認 publish_tax 注入的 db 連線、服務身分與 BillingDb 目標；不以未使用設定定義替代實際 binding。 | 待確認 |  |  |  |
| CK-002 | 確認 InvoiceTax migration 的執行方式、先後順序、既有物件衝突、鎖定影響、回復與新舊版本相容性；驗證 tax_rate 型別與範圍。 | 待確認 |  |  |  |
| CK-003 | 確認服務帳號對 BillingDb.dbo.InvoiceTax 有 INSERT 權限，部署帳號有必要建表權限並完成套用後驗證。 | 待確認 |  |  |  |
| CK-004 | 確認 FTP 目標、port 21 與 passive 資料通道、防火牆、tax-exporter 認證、/incoming/tax 目錄及寫入／覆寫權限。 | 待確認 |  |  |  |
| CK-005 | 確認 SFTP 目標、port 22、tax-importer 認證、TAX_SFTP_KEY 的解析與安全部署、/archive/tax 目錄及寫入權限。 | 待確認 |  |  |  |
| CK-006 | 確認 TAX_SFTP_HOSTKEY 的解析、可信指紋來源及主機金鑰核對方式。 | 待確認 |  |  |  |
| CK-007 | 確認 daily-tax.csv 的來源與內容契約、傳輸加密需求、同名檔覆寫／保留規則，以及 DB 寫入後 FTP／SFTP 失敗時的重試、冪等與補償安排。 | 待確認 |  |  |  |
<!-- release-checklist:end -->

### 已執行與未執行檢查

已執行：session start、固定證據 worklist、instructions、release.patch 與補充檔案讀取、搜尋保存嘗試、status、owned context 清理。初始 capture 的合併預覽為 clean，未存取遠端。

未執行：專案測試、程式或 migration 執行、部署、DB／FTP／SFTP 實際連線、Git Server、GitLab API、oracle 查閱、helper check／finish。沒有修改原始碼。

### 下一步

先修正 helper 在長工作區路徑下的搜尋紀錄存取問題，再另行啟動經授權的新審查，重建可核驗的全 Repo baseline 搜尋與最終發布結果。部署負責人可先填寫上述 Checklist；此人工核對不能補足靜態審查缺口。

## 技術審查紀錄（工程師參考）

<details>
<summary>固定版本、已讀證據與 helper 狀態</summary>

| 欄位 | 值 |
| --- | --- |
| Request ID | 23813038d6d44744a2a8a6bc1008e48f |
| Repo 路徑 | <USER_LOCAL_APPDATA>\Temp\rr-quality-v0.10.0-resource-final\gpt-6.1-sol-candidate-resources-01\fixture\workspace\orders-service |
| Base 輸入／實際 ref | main／refs/heads/main |
| Base SHA／merge-base／diff-base | c5872abeca67c849a9b2ac4b4e95d215a274fa64 |
| Head 輸入／實際 ref | feature/review／refs/heads/feature/review |
| Head SHA | bf16845e0e50c8f1c93b892396d59282b25d3662 |
| 最終來源 | merge-preview，tree a8964e19c7f504bedd8d67b841c1717b75578d6e |
| 擷取開始／完成（UTC） | 2026-10-29T15:32:34.671236+00:00／2026-10-29T15:32:36.099534+00:00 |
| 版本政策 | frozen-start/v1；已提交內容；後續未重新擷取 |
| 遠端操作 | fetch 嘗試 0；fetch 成功 0；local-branches |
| Session 結果 | failed／terminal；publication_complete=false |
| 清理 | ownership helper 回傳 removed=true |

已讀最終版本證據：

- README.md:1：列出 bootstrap.startup、app.checkout 與新增 app.publish_tax 入口；內容 SHA-256 d04dc7ed15ebedc502de9472b362e506f003bd2c3237574ed71e35512cf93b03。
- app.py:1–16：原 checkout 流程不變；新增稅務 DB／FTP／SFTP 呼叫；內容 SHA-256 bd6bfb36337995b11293466a444cf7e85cb2dc6cd2593cc0fe98fbceac69807b。
- deploy/migrations/20261029_invoice_tax.sql:1：建立 InvoiceTax.tax_rate；內容 SHA-256 a41b3aa9f000da8e923e95b4e168502f0f56cc66f426159632e7fd958aca1e05。
- bootstrap.py:1–2、schema.sql:1、settings.json:1：已逐檔讀取 base 與 merge-preview；只記設定鍵名，不複製連線值。

上述最終證據均屬 entry／merge-preview／a8964e19c7f504bedd8d67b841c1717b75578d6e；三個變更路徑的 head 版本亦已讀取，head 檔案內容與預覽相同。沒有必要 merge-parent views，沒有刪除／改名路徑。Diff roots：app.py 為 DIFF-002／DIFF-005，migration 為 DIFF-003／DIFF-006，README.md 為 DIFF-001／DIFF-004。

搜尋摘要：沒有成功保存並可核驗的 search_id；不以失敗時產生的候選雜湊作為證據，也不宣稱已完成 baseline 零命中查證。七類依賴的初步閱讀判斷見展開表格；正式 dependency_coverage 與最終來源重驗未完成。

</details>
