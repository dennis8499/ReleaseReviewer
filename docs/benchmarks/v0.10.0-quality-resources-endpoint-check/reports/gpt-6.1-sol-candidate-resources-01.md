# 上版檢查: orders-service

## 審查結論

**待核對上版條件**｜靜態檢查完成。已檢查範圍未發現有證據的 P0／P1 直接阻擋；稅務匯出首次使用 BillingDb／InvoiceTax、FTP 與 SFTP，部署條件仍待人工核對。

問題數量：P0 0／P1 0（僅列直接阻擋上版的程式風險）。

阻擋 0 項｜合併衝突 0 項｜待確認 20 項｜檢查缺口 0 項。
判斷固定於產生時；人工填寫 Checklist 不會自動核准上版。

- 入口 Repo：`orders-service`
- 合併方向：`feature/review`（`bf16845e`） → `main`（`c5872abe`）

## 問題總攬

| 項目 | 等級 | 說明 |
| --- | --- | --- |
| — | — | 已檢查範圍未發現已確認的直接上版阻擋。 |

## 問題詳情（工程師參考）

已檢查範圍未發現有證據的直接阻擋；待確認條件與缺口見後文。

## 範圍與限制

本次只審查指定 Repo 的已提交差異；其他 Repo 的擷取時 HEAD 是功能鏈佐證。
結論只適用本次固定版本；擷取後分支移動不改寫本次結果。

- 審查僅限本次已提交的固定 base／head 與乾淨合併預覽；沒有工作檔變更納入。catalog 只有入口 Repo，沒有 supporting Repo；沒有已知必要的跨 Repo consumer。
- app.publish_tax 是文件明示的功能入口，db／ftp／sftp 均由外部注入；固定範圍不含 adapter 內部實作、現場認證／服務帳號、產檔機制、交易提交、覆寫、ACK 或重送策略。已可辨識呼叫及邏輯目的地，這些環境／provider 核對不推定為程式阻擋。
- 未查證真實 BillingDb／表／欄位、migration 是否已套用、FTP／SFTP 端點可達性、DNS、防火牆、認證、ACL、金鑰或目錄狀態；Checklist 全部初始待確認。
- 沒有將 settings.json 的 billing_db_connection 推定為 publish_tax(db) 的實際連線來源，也沒有把 TAX_SFTP_KEY／TAX_SFTP_HOSTKEY 識別推定為環境變數；由部署負責人核對注入 client 與材料解析。
- 僅使用本機分支與同一 request 的已封存證據；未存取 Git Server、GitLab 或任何 oracle。靜態完成不代表環境就緒或核准上版。

**已執行檢查**

- 無紀錄。

**未執行檢查**

- 依使用者要求，未執行任何專案測試、程式流程重現、部署、DB 查詢或外部端點驗證。

### 新增部署依賴與人工核對

<a id="D-001"></a>

#### D-001｜BillingDb

新稅務匯出第一次以完整限定 SQL 寫入 BillingDb；migration 建立資料表但不建立資料庫。（已辨識目標）
連線／用途：publish_tax(db) 外部注入的 DB client；明確邏輯目標 BillingDb，實際主機、連線與服務帳號待環境核對。。
**上版準備**：確認 BillingDb 已備妥、DB client 所連伺服器可存取該 DB、服務帳號具有必要連線與存取權限；實際連線不得只由未使用的設定鍵推定。

**核對項**：[K-001](#K-001)、[K-002](#K-002)、[K-003](#K-003)、[K-004](#K-004)、[K-005](#K-005)

<a id="D-002"></a>

#### D-002｜BillingDb.dbo.InvoiceTax

新增 migration 建立 InvoiceTax，tax_rate 為 DECIMAL(5, 2) NOT NULL；新 publish_tax 寫入該欄位。（已辨識目標）
連線／用途：publish_tax(db) 外部注入的 DB client；明確邏輯目標 BillingDb，實際主機、連線與服務帳號待環境核對。。
**上版準備**：先核對 deploy/migrations/20261029_invoice_tax.sql 的套用順序與結果，再啟用稅務匯出；確認表及 tax_rate 欄位型別／非空限制、部署帳號 DDL 與執行帳號 INSERT 權限，並核對重複套用及回復方案。未查現場，不推定資料表尚未建立。

**核對項**：[K-006](#K-006)、[K-007](#K-007)、[K-008](#K-008)、[K-009](#K-009)、[K-010](#K-010)

<a id="E-001"></a>

#### E-001｜/incoming/tax/daily-tax.csv

publish_tax 在 DB 寫入後呼叫 ftp.upload，將 daily-tax.csv 上傳到 /incoming/tax。固定主機、port、模式及帳號皆來自呼叫參數。（已辨識目標）
連線／用途：FTP ftp.tax.example.invalid:21；passive=True（被動模式）；帳號 tax-exporter。
**上版準備**：確認 tax-exporter 對 ftp.tax.example.invalid:21 的 DNS／網路／認證可用；核對被動模式資料通道與防火牆，/incoming/tax 目錄存在並允許上傳 daily-tax.csv。確認固定檔名覆寫及傳輸保護政策適用，並保留實際驗證證據。

**核對項**：[K-011](#K-011)、[K-012](#K-012)、[K-013](#K-013)、[K-014](#K-014)、[K-015](#K-015)

<a id="E-002"></a>

#### E-002｜/archive/tax/daily-tax.csv

publish_tax 在 FTP 呼叫後以 sftp.put 上傳 daily-tax.csv 到 /archive/tax，使用明確的金鑰及 known_hosts 識別；未取得金鑰內容。（已辨識目標）
連線／用途：SFTP sftp.tax.example.invalid:22；帳號 tax-importer；金鑰識別 TAX_SFTP_KEY；主機金鑰識別 TAX_SFTP_HOSTKEY。
**上版準備**：確認 tax-importer 可連線 sftp.tax.example.invalid:22，部署 TAX_SFTP_KEY 所指金鑰，並以可信管道核對 TAX_SFTP_HOSTKEY 所指 known_hosts／主機金鑰；確認 /archive/tax 存在且可寫入 daily-tax.csv，檢查覆寫及失敗重送的操作約定。

**核對項**：[K-016](#K-016)、[K-017](#K-017)、[K-018](#K-018)、[K-019](#K-019)、[K-020](#K-020)

核對狀態限「待確認／已確認／未通過／不適用」。已確認需核對人、日期及開通／套用後驗證證據；
僅有申請工單仍為待確認，不適用需具體理由。必要項目待確認或未通過時，上版準備未完成。
本段核對紀錄可人工更新；固定正文與產生時判斷保留原始快照。

<!-- release-checklist:start -->
### 人工核對紀錄

#### [D-001｜BillingDb](#D-001)

| 編號 | 核對事項 | 狀態 | 核對人 | 日期 | 工單／驗證證據 | 不適用理由 |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="K-001"></a>K-001 | 確認目標「BillingDb」及連線／服務身分「publish_tax(db) 外部注入的 DB client；明確邏輯目標 BillingDb，實際主機、連線與服務帳號待環境核對。」的主機、連接埠、DNS／路由與防火牆已開通；未知或環境提供值須核對實際設定並記錄驗證證據。 | 待確認 | | | | |
| <a id="K-002"></a>K-002 | 確認目標「BillingDb」及連線／服務身分「publish_tax(db) 外部注入的 DB client；明確邏輯目標 BillingDb，實際主機、連線與服務帳號待環境核對。」的 TLS／憑證／加密設定符合目標環境；不適用時記錄理由。 | 待確認 | | | | |
| <a id="K-003"></a>K-003 | 確認目標 DB／schema 與所需資料表、欄位、View 或預存程序已建立或套用。 | 待確認 | | | | |
| <a id="K-004"></a>K-004 | 確認 migration／DDL 執行順序、鎖定與資料回填，以及新舊程式版本相容性。 | 待確認 | | | | |
| <a id="K-005"></a>K-005 | 確認實際服務帳號具備本次操作所需 DB／物件權限；其他服務可用不代表本服務已授權。 | 待確認 | | | | |

#### [D-002｜BillingDb.dbo.InvoiceTax](#D-002)

| 編號 | 核對事項 | 狀態 | 核對人 | 日期 | 工單／驗證證據 | 不適用理由 |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="K-006"></a>K-006 | 確認目標「BillingDb.dbo.InvoiceTax」及連線／服務身分「publish_tax(db) 外部注入的 DB client；明確邏輯目標 BillingDb，實際主機、連線與服務帳號待環境核對。」的主機、連接埠、DNS／路由與防火牆已開通；未知或環境提供值須核對實際設定並記錄驗證證據。 | 待確認 | | | | |
| <a id="K-007"></a>K-007 | 確認目標「BillingDb.dbo.InvoiceTax」及連線／服務身分「publish_tax(db) 外部注入的 DB client；明確邏輯目標 BillingDb，實際主機、連線與服務帳號待環境核對。」的 TLS／憑證／加密設定符合目標環境；不適用時記錄理由。 | 待確認 | | | | |
| <a id="K-008"></a>K-008 | 確認目標 DB／schema 與所需資料表、欄位、View 或預存程序已建立或套用。 | 待確認 | | | | |
| <a id="K-009"></a>K-009 | 確認 migration／DDL 執行順序、鎖定與資料回填，以及新舊程式版本相容性。 | 待確認 | | | | |
| <a id="K-010"></a>K-010 | 確認實際服務帳號具備本次操作所需 DB／物件權限；其他服務可用不代表本服務已授權。 | 待確認 | | | | |

#### [E-001｜/incoming/tax/daily-tax.csv](#E-001)

| 編號 | 核對事項 | 狀態 | 核對人 | 日期 | 工單／驗證證據 | 不適用理由 |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="K-011"></a>K-011 | 確認目標「/incoming/tax/daily-tax.csv」及連線／服務身分「FTP ftp.tax.example.invalid:21；passive=True（被動模式）；帳號 tax-exporter」的主機、連接埠、DNS／路由與防火牆已開通；未知或環境提供值須核對實際設定並記錄驗證證據。 | 待確認 | | | | |
| <a id="K-012"></a>K-012 | 確認目標「/incoming/tax/daily-tax.csv」及連線／服務身分「FTP ftp.tax.example.invalid:21；passive=True（被動模式）；帳號 tax-exporter」的 TLS／憑證／加密設定符合目標環境；不適用時記錄理由。 | 待確認 | | | | |
| <a id="K-013"></a>K-013 | 確認 FTP 主動／被動模式及資料通道連接埠的防火牆規則。 | 待確認 | | | | |
| <a id="K-014"></a>K-014 | 確認 FTP 帳號與憑證來源已配置且有效。 | 待確認 | | | | |
| <a id="K-015"></a>K-015 | 確認目錄存在及上傳、下載、搬移、刪除所需權限。 | 待確認 | | | | |

#### [E-002｜/archive/tax/daily-tax.csv](#E-002)

| 編號 | 核對事項 | 狀態 | 核對人 | 日期 | 工單／驗證證據 | 不適用理由 |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="K-016"></a>K-016 | 確認目標「/archive/tax/daily-tax.csv」及連線／服務身分「SFTP sftp.tax.example.invalid:22；帳號 tax-importer；金鑰識別 TAX_SFTP_KEY；主機金鑰識別 TAX_SFTP_HOSTKEY」的主機、連接埠、DNS／路由與防火牆已開通；未知或環境提供值須核對實際設定並記錄驗證證據。 | 待確認 | | | | |
| <a id="K-017"></a>K-017 | 確認目標「/archive/tax/daily-tax.csv」及連線／服務身分「SFTP sftp.tax.example.invalid:22；帳號 tax-importer；金鑰識別 TAX_SFTP_KEY；主機金鑰識別 TAX_SFTP_HOSTKEY」的 TLS／憑證／加密設定符合目標環境；不適用時記錄理由。 | 待確認 | | | | |
| <a id="K-018"></a>K-018 | 確認 SFTP 帳號／金鑰及憑證部署來源有效。 | 待確認 | | | | |
| <a id="K-019"></a>K-019 | 確認已核對 SFTP 主機金鑰及輪替安排。 | 待確認 | | | | |
| <a id="K-020"></a>K-020 | 確認遠端目錄及檔案操作權限符合服務帳號需求。 | 待確認 | | | | |

<!-- release-checklist:end -->

### 下一步

- 維運與 DB 負責人核對 D-001／D-002，確認目標 BillingDb、migration 順序、欄位與帳號權限。
- 傳檔服務負責人核對 E-001／E-002 所列實際主機、port、模式、帳號、目錄及金鑰；在同份 Checklist 填寫核對人、日期與開通後的驗證證據。不適用項填理由。

## 技術審查紀錄（工程師參考）

### 比較資訊

- Repo：`orders-service`
- 本機路徑：`<USER_LOCAL_APPDATA>\Temp\q-rr-resource-endpoint-1030\gpt-6.1-sol-candidate-resources-01\fixture\workspace\orders-service`

| 角色 | 輸入分支 | 實際 ref | Commit（前 8 碼） |
| --- | --- | --- | --- |
| 基礎分支 | `main` | `refs/heads/main` | `c5872abe` |
| 比較分支 | `feature/review` | `refs/heads/feature/review` | `bf16845e` |

- 合併方向：`feature/review` → `main`
- 主要差異範圍（共同祖先 → 比較分支）：`c5872abe` → `bf16845e`
- 最終受審版本：`merge-preview`；`a8964e19c7f504bedd8d67b841c1717b75578d6e`
- 快照擷取：2026-10-29T16:36:03.316222+00:00 → 2026-10-29T16:36:04.656451+00:00（UTC）。
- 適用版本：本報告僅適用上述固定 SHA 及本次擷取的相關 Repo；後續分支更新不改變本次結論。

<details>
<summary>固定版本、搜尋、功能鏈與覆蓋紀錄</summary>

- Repo：<USER_LOCAL_APPDATA>\Temp\q-rr-resource-endpoint-1030\gpt-6.1-sol-candidate-resources-01\fixture\workspace\orders-service
- 報告目錄：<USER_LOCAL_APPDATA>\Temp\q-rr-resource-endpoint-1030\gpt-6.1-sol-candidate-resources-01\fixture\workspace\orders-service\review-reports
- 基礎分支：main
- 比較分支：feature/review
- 基礎 SHA：c5872abeca67c849a9b2ac4b4e95d215a274fa64
- 比較 SHA：bf16845e0e50c8f1c93b892396d59282b25d3662
- 共同起點：c5872abeca67c849a9b2ac4b4e95d215a274fa64
- 最終來源：merge-preview
- 最終 SHA：a8964e19c7f504bedd8d67b841c1717b75578d6e
- 擷取開始（UTC）：2026-10-29T16:36:03.316222+00:00
- 快照完成（UTC）：2026-10-29T16:36:04.656451+00:00
- 快照政策：frozen-start/v1
- 本機檔案與暫存區維持原狀：True

版本比對只包含已提交內容。靜態範圍完整不代表目標環境已驗證。

- Diff 起點 DIFF-001：README.md；c5872abeca67c849a9b2ac4b4e95d215a274fa64 → bf16845e0e50c8f1c93b892396d59282b25d3662
- Diff 起點 DIFF-002：app.py；c5872abeca67c849a9b2ac4b4e95d215a274fa64 → bf16845e0e50c8f1c93b892396d59282b25d3662
- Diff 起點 DIFF-003：deploy/migrations/20261029_invoice_tax.sql；c5872abeca67c849a9b2ac4b4e95d215a274fa64 → bf16845e0e50c8f1c93b892396d59282b25d3662
- Diff 起點 DIFF-004：README.md；c5872abeca67c849a9b2ac4b4e95d215a274fa64 → a8964e19c7f504bedd8d67b841c1717b75578d6e
- Diff 起點 DIFF-005：app.py；c5872abeca67c849a9b2ac4b4e95d215a274fa64 → a8964e19c7f504bedd8d67b841c1717b75578d6e
- Diff 起點 DIFF-006：deploy/migrations/20261029_invoice_tax.sql；c5872abeca67c849a9b2ac4b4e95d215a274fa64 → a8964e19c7f504bedd8d67b841c1717b75578d6e
### D-001 新增判定與證據

新增類型：first-use；入口 base 全 Repo 的 BillingDb、InvoiceTax 與 tax_rate 搜尋均未命中；逐檔檢查確認只有 CoreDb.dbo.Orders 定義與 startup 的 orders 查詢。settings.json 的 billing_db_connection 僅為未使用定義，沒有 loader 或部署 binding，不代表既有 BillingDb 正式使用。

Diff 起點：DIFF-005；本次新增 publish_tax 的 BillingDb.dbo.InvoiceTax INSERT，首次需要 BillingDb 的可達性及寫入存取。

`orders-service/app.py:10-11`（來源：`merge-preview`；固定版本：`a8964e19c7f504bedd8d67b841c1717b75578d6e`）

內容 SHA-256：`bd6bfb36337995b11293466a444cf7e85cb2dc6cd2593cc0fe98fbceac69807b`

~~~text
10: def publish_tax(db, ftp, sftp, tax_rate):
11:     db.execute("INSERT INTO BillingDb.dbo.InvoiceTax (tax_rate) VALUES (?)", (tax_rate,))
~~~

`orders-service/deploy/migrations/20261029_invoice_tax.sql:1-1`（來源：`merge-preview`；固定版本：`a8964e19c7f504bedd8d67b841c1717b75578d6e`）

內容 SHA-256：`a41b3aa9f000da8e923e95b4e168502f0f56cc66f426159632e7fd958aca1e05`

~~~text
1: CREATE TABLE BillingDb.dbo.InvoiceTax (tax_rate DECIMAL(5, 2) NOT NULL);
~~~

- 基礎全 Repo 搜尋：命中 0；紀錄 631cfc8f10afcde2be1eef99442d00bda0e1169bf81fb877d7f858e9a855d2dc
- 基礎全 Repo 搜尋：命中 0；紀錄 4807d1982161286f3c516fe91483306526a5d4a01df66ae2e615180580cec938
- 基礎全 Repo 搜尋：命中 0；紀錄 c4ea345226119b765ba0a55d314c1eed3960448411d51051afb8a3900d6c4b84
- 基礎全 Repo 搜尋：命中 1；紀錄 21724b2dd96dbcea289055c51e8865353fed4070e11eeb561cc3e246d6857e7a

### D-002 新增判定與證據

新增類型：resource-definition；全 Repo 的物件名、欄位與 DB 搜尋皆無既有使用或定義；逐檔確認 base 只有 CoreDb.dbo.Orders，沒有等價 alias、ORM mapping 或稅務寫入。

Diff 起點：DIFF-005、DIFF-006；本次新增 CREATE TABLE 定義與 INSERT 呼叫，部署須準備新表及欄位。

`orders-service/deploy/migrations/20261029_invoice_tax.sql:1-1`（來源：`merge-preview`；固定版本：`a8964e19c7f504bedd8d67b841c1717b75578d6e`）

內容 SHA-256：`a41b3aa9f000da8e923e95b4e168502f0f56cc66f426159632e7fd958aca1e05`

~~~text
1: CREATE TABLE BillingDb.dbo.InvoiceTax (tax_rate DECIMAL(5, 2) NOT NULL);
~~~

`orders-service/app.py:10-11`（來源：`merge-preview`；固定版本：`a8964e19c7f504bedd8d67b841c1717b75578d6e`）

內容 SHA-256：`bd6bfb36337995b11293466a444cf7e85cb2dc6cd2593cc0fe98fbceac69807b`

~~~text
10: def publish_tax(db, ftp, sftp, tax_rate):
11:     db.execute("INSERT INTO BillingDb.dbo.InvoiceTax (tax_rate) VALUES (?)", (tax_rate,))
~~~

- 基礎全 Repo 搜尋：命中 0；紀錄 631cfc8f10afcde2be1eef99442d00bda0e1169bf81fb877d7f858e9a855d2dc
- 基礎全 Repo 搜尋：命中 0；紀錄 4807d1982161286f3c516fe91483306526a5d4a01df66ae2e615180580cec938
- 基礎全 Repo 搜尋：命中 0；紀錄 c4ea345226119b765ba0a55d314c1eed3960448411d51051afb8a3900d6c4b84
- 基礎全 Repo 搜尋：命中 1；紀錄 21724b2dd96dbcea289055c51e8865353fed4070e11eeb561cc3e246d6857e7a

### E-001 新增判定與證據

新增類型：first-use；入口 base 全 Repo 搜尋 ftp、sftp 與 publish_tax 均無命中；已讀全部 base 檔案，沒有傳檔 client、包裝器、主機 alias 或同一目錄的既有傳送。

Diff 起點：DIFF-005；新增 ftp.upload 呼叫首次引入 FTP endpoint、服務帳號與目錄寫入依賴。

`orders-service/app.py:12-13`（來源：`merge-preview`；固定版本：`a8964e19c7f504bedd8d67b841c1717b75578d6e`）

內容 SHA-256：`bd6bfb36337995b11293466a444cf7e85cb2dc6cd2593cc0fe98fbceac69807b`

~~~text
12: [含認證或連線設定的行已省略]
13:                remote_dir="/incoming/tax", filename="daily-tax.csv")
~~~

- 基礎全 Repo 搜尋：命中 0；紀錄 d909a229e72611007d52868c8950332015a78f61b0e52ed8fc2b99e56760319f
- 基礎全 Repo 搜尋：命中 0；紀錄 ff6b1130048367fef83f3c76564359b18285ed0581ec91e2269b014ddd01c64a
- 基礎全 Repo 搜尋：命中 0；紀錄 ee18c2195d38a19da10c4befe75e0569ff337bff8ce9edd24633fbc6e797719f

### E-002 新增判定與證據

新增類型：first-use；入口 base 全 Repo 搜尋 sftp、ftp、TAX_、TAX_SFTP_KEY 與 TAX_SFTP_HOSTKEY 均無命中；已閱讀所有固定 base 檔案，沒有既有 SFTP adapter、別名或對應目錄操作。

Diff 起點：DIFF-005；新增 sftp.put 呼叫首次需要 SFTP endpoint、帳號、金鑰、主機金鑰與目錄寫入依賴。

`orders-service/app.py:14-16`（來源：`merge-preview`；固定版本：`a8964e19c7f504bedd8d67b841c1717b75578d6e`）

內容 SHA-256：`bd6bfb36337995b11293466a444cf7e85cb2dc6cd2593cc0fe98fbceac69807b`

~~~text
14: [含認證或連線設定的行已省略]
15:              key_name="TAX_SFTP_KEY", known_hosts="TAX_SFTP_HOSTKEY",
16:              remote_dir="/archive/tax", filename="daily-tax.csv")
~~~

- 基礎全 Repo 搜尋：命中 0；紀錄 ff6b1130048367fef83f3c76564359b18285ed0581ec91e2269b014ddd01c64a
- 基礎全 Repo 搜尋：命中 0；紀錄 d909a229e72611007d52868c8950332015a78f61b0e52ed8fc2b99e56760319f
- 基礎全 Repo 搜尋：命中 0；紀錄 a7d2fb2c0244ed0cf5a2192ba284c968ab0a4098bf289b0850cf2251a40b6871
- 基礎全 Repo 搜尋：命中 0；紀錄 5cbc92cbe0e5a7373034ab44e083f1d57118bf6bfb058d313b874342990103b9
- 基礎全 Repo 搜尋：命中 0；紀錄 e4aefeabcee8e697d2a49ebd09423d79768c44a8f74d2d3b8236d35d0bceb73c

### 關聯 Repo 與功能鏈

| Repo | 固定本機分支 | SHA | 狀態 |
| --- | --- | --- | --- |
| entry / orders-service | 入口指定分支 | 見入口版本／未取得 | captured |

**F-001｜entry → entry｜database**
reviewed：明確入口 app.publish_tax 接收 tax_rate 與 DB client，直接 INSERT BillingDb.dbo.InvoiceTax.tax_rate；本次 migration 定義對應欄位。沒有本地 catch／補償，DB client 若拋錯則中止後續傳送；交易提交由注入 client 決定。
Diff 起點：DIFF-005、DIFF-006；新匯出入口引入稅務 INSERT 及對應建表定義。

`orders-service/app.py:10-11`（來源：`merge-preview`；固定版本：`a8964e19c7f504bedd8d67b841c1717b75578d6e`）

內容 SHA-256：`bd6bfb36337995b11293466a444cf7e85cb2dc6cd2593cc0fe98fbceac69807b`

~~~text
10: def publish_tax(db, ftp, sftp, tax_rate):
11:     db.execute("INSERT INTO BillingDb.dbo.InvoiceTax (tax_rate) VALUES (?)", (tax_rate,))
~~~

`orders-service/deploy/migrations/20261029_invoice_tax.sql:1-1`（來源：`merge-preview`；固定版本：`a8964e19c7f504bedd8d67b841c1717b75578d6e`）

內容 SHA-256：`a41b3aa9f000da8e923e95b4e168502f0f56cc66f426159632e7fd958aca1e05`

~~~text
1: CREATE TABLE BillingDb.dbo.InvoiceTax (tax_rate DECIMAL(5, 2) NOT NULL);
~~~

**F-002｜entry → entry｜other**
reviewed：同一明確入口在 DB execute 返回後呼叫 ftp.upload；若 FTP client 拋錯，後續 SFTP 不執行。固定版本未實作認證、產檔、覆寫政策或重送，相關現場行為隨 E-001 核對。
Diff 起點：DIFF-005；新增稅務 FTP 傳送依賴與順序。

`orders-service/app.py:10-13`（來源：`merge-preview`；固定版本：`a8964e19c7f504bedd8d67b841c1717b75578d6e`）

內容 SHA-256：`bd6bfb36337995b11293466a444cf7e85cb2dc6cd2593cc0fe98fbceac69807b`

~~~text
10: def publish_tax(db, ftp, sftp, tax_rate):
11:     db.execute("INSERT INTO BillingDb.dbo.InvoiceTax (tax_rate) VALUES (?)", (tax_rate,))
12: [含認證或連線設定的行已省略]
13:                remote_dir="/incoming/tax", filename="daily-tax.csv")
~~~

`orders-service/app.py:12-13`（來源：`merge-preview`；固定版本：`a8964e19c7f504bedd8d67b841c1717b75578d6e`）

內容 SHA-256：`bd6bfb36337995b11293466a444cf7e85cb2dc6cd2593cc0fe98fbceac69807b`

~~~text
12: [含認證或連線設定的行已省略]
13:                remote_dir="/incoming/tax", filename="daily-tax.csv")
~~~

**F-003｜entry → entry｜other**
reviewed：FTP 呼叫返回後執行 sftp.put，傳入固定主機、帳號及金鑰識別；無 catch 或明確回傳值，正常完成時函式隱含回傳 None。既有 checkout 的 amount 發布／消費契約未改變。
Diff 起點：DIFF-005；新增匯出流程最後的 SFTP 傳送呼叫。

`orders-service/app.py:12-16`（來源：`merge-preview`；固定版本：`a8964e19c7f504bedd8d67b841c1717b75578d6e`）

內容 SHA-256：`bd6bfb36337995b11293466a444cf7e85cb2dc6cd2593cc0fe98fbceac69807b`

~~~text
12: [含認證或連線設定的行已省略]
13:                remote_dir="/incoming/tax", filename="daily-tax.csv")
14: [含認證或連線設定的行已省略]
15:              key_name="TAX_SFTP_KEY", known_hosts="TAX_SFTP_HOSTKEY",
16:              remote_dir="/archive/tax", filename="daily-tax.csv")
~~~

`orders-service/app.py:14-16`（來源：`merge-preview`；固定版本：`a8964e19c7f504bedd8d67b841c1717b75578d6e`）

內容 SHA-256：`bd6bfb36337995b11293466a444cf7e85cb2dc6cd2593cc0fe98fbceac69807b`

~~~text
14: [含認證或連線設定的行已省略]
15:              key_name="TAX_SFTP_KEY", known_hosts="TAX_SFTP_HOSTKEY",
16:              remote_dir="/archive/tax", filename="daily-tax.csv")
~~~

### 靜態覆蓋

| 路徑 | 狀態 | 未完成原因 |
| --- | --- | --- |
| README.md | reviewed |  |
| app.py | reviewed |  |
| deploy/migrations/20261029_invoice_tax.sql | reviewed |  |

**依賴類型覆蓋**：

- config：reviewed；已讀 base／最終 settings.json、bootstrap.py、app.py 及全部部署定義，並搜尋設定別名。billing_db_connection 只有既有未使用定義，沒有此次 loader 或 binding，不列新增依賴。TAX_SFTP_KEY／TAX_SFTP_HOSTKEY 是實際傳入 SFTP 的識別，連同 E-002 核對金鑰部署；固定程式沒有證據把它們定義為環境變數。
- db：reviewed；已查 base 全 Repo SQL、schema 與設定別名及最終 INSERT／migration。BillingDb 與 InvoiceTax（含 tax_rate）為新增依賴，列 D-001／D-002；沒有新證據將既有設定鍵與注入 db 連接。
- ftp：reviewed；已對照 base 全 Repo 與最終 FTP upload；新增主機、21 埠、被動模式、tax-exporter 帳號與 /incoming/tax 目錄，列 E-001。
- sftp：reviewed；已對照 base 全 Repo 與最終 SFTP put；新增主機、22 埠、tax-importer 帳號、/archive/tax 與金鑰識別，列 E-002。
- redis：reviewed；逐檔閱讀 base 與最終版本的全部六個文字檔、差異與部署定義，未見 Redis client、連線、命令或 key 使用；本次沒有 Redis 新依賴。
- kafka：reviewed；已讀全部固定程式與部署來源，publish／consume 是既有程序內字典傳遞，沒有 broker、Kafka client、Topic 或 Consumer Group；本次傳檔與 SQL 不構成 Kafka 使用。
- other：reviewed；已檢視全部差異、固定檔案清單及相關程式，新增外部操作皆為已列 DB／FTP／SFTP，沒有其他新服務、API、事件 provider 或套件定義。

</details>

<!-- release-review-report:eyJiYXNlX2lucHV0IjoibWFpbiIsImJhc2VfcmVzb2x2ZWRfcmVmIjoicmVmcy9oZWFkcy9tYWluIiwiYmFzZV9zaGEiOiJjNTg3MmFiZWNhNjdjODQ5YTliMmFjNGI0ZTk1ZDIxNWEyNzRmYTY0IiwiYmxvY2tlcl9jb3VudCI6MCwiY2FwdHVyZV9jb21wbGV0ZWRfYXQiOiIyMDI2LTEwLTI5VDE2OjM2OjA0LjY1NjQ1MSswMDowMCIsImNvbnRleHRfc2hhMjU2IjoiYTIwZTcwMjhiMDZkZTE0MDRlYzc4ZjNjYjJkNzkwMDI3ZTFhOTI2YTJkMDBjYmNhYWUxNjcyNTAwNTA4Yzk4NyIsImNvdmVyYWdlX3BvbGljeSI6ImFuY2VzdG9yLW5vb3AvdjEiLCJkaWZmX2Jhc2UiOiJjNTg3MmFiZWNhNjdjODQ5YTliMmFjNGI0ZTk1ZDIxNWEyNzRmYTY0IiwiZGlmZl9yb290cyI6W3siYWZ0ZXIiOnsicmVmIjoiYmYxNjg0NWUwZTUwYzhmMWM5M2I4OTIzOTZkNTkyODJiMjVkMzY2MiIsInNvdXJjZSI6ImhlYWQifSwiYmVmb3JlIjp7InJlZiI6ImM1ODcyYWJlY2E2N2M4NDlhOWIyYWM0YjRlOTVkMjE1YTI3NGZhNjQiLCJzb3VyY2UiOiJtZXJnZS1iYXNlIn0sImlkIjoiRElGRi0wMDEiLCJvbGRfcGF0aCI6bnVsbCwicGF0aCI6IlJFQURNRS5tZCIsInN0YXR1cyI6Ik0ifSx7ImFmdGVyIjp7InJlZiI6ImJmMTY4NDVlMGU1MGM4ZjFjOTNiODkyMzk2ZDU5MjgyYjI1ZDM2NjIiLCJzb3VyY2UiOiJoZWFkIn0sImJlZm9yZSI6eyJyZWYiOiJjNTg3MmFiZWNhNjdjODQ5YTliMmFjNGI0ZTk1ZDIxNWEyNzRmYTY0Iiwic291cmNlIjoibWVyZ2UtYmFzZSJ9LCJpZCI6IkRJRkYtMDAyIiwib2xkX3BhdGgiOm51bGwsInBhdGgiOiJhcHAucHkiLCJzdGF0dXMiOiJNIn0seyJhZnRlciI6eyJyZWYiOiJiZjE2ODQ1ZTBlNTBjOGYxYzkzYjg5MjM5NmQ1OTI4MmIyNWQzNjYyIiwic291cmNlIjoiaGVhZCJ9LCJiZWZvcmUiOnsicmVmIjoiYzU4NzJhYmVjYTY3Yzg0OWE5YjJhYzRiNGU5NWQyMTVhMjc0ZmE2NCIsInNvdXJjZSI6Im1lcmdlLWJhc2UifSwiaWQiOiJESUZGLTAwMyIsIm9sZF9wYXRoIjpudWxsLCJwYXRoIjoiZGVwbG95L21pZ3JhdGlvbnMvMjAyNjEwMjlfaW52b2ljZV90YXguc3FsIiwic3RhdHVzIjoiQSJ9LHsiYWZ0ZXIiOnsicmVmIjoiYTg5NjRlMTljN2Y1MDRiZWRkOGQ2N2I4NDFjMTcxN2I3NTU3OGQ2ZSIsInNvdXJjZSI6Im1lcmdlLXByZXZpZXcifSwiYmVmb3JlIjp7InJlZiI6ImM1ODcyYWJlY2E2N2M4NDlhOWIyYWM0YjRlOTVkMjE1YTI3NGZhNjQiLCJzb3VyY2UiOiJiYXNlIn0sImlkIjoiRElGRi0wMDQiLCJvbGRfcGF0aCI6bnVsbCwicGF0aCI6IlJFQURNRS5tZCIsInN0YXR1cyI6Ik0ifSx7ImFmdGVyIjp7InJlZiI6ImE4OTY0ZTE5YzdmNTA0YmVkZDhkNjdiODQxYzE3MTdiNzU1NzhkNmUiLCJzb3VyY2UiOiJtZXJnZS1wcmV2aWV3In0sImJlZm9yZSI6eyJyZWYiOiJjNTg3MmFiZWNhNjdjODQ5YTliMmFjNGI0ZTk1ZDIxNWEyNzRmYTY0Iiwic291cmNlIjoiYmFzZSJ9LCJpZCI6IkRJRkYtMDA1Iiwib2xkX3BhdGgiOm51bGwsInBhdGgiOiJhcHAucHkiLCJzdGF0dXMiOiJNIn0seyJhZnRlciI6eyJyZWYiOiJhODk2NGUxOWM3ZjUwNGJlZGQ4ZDY3Yjg0MWMxNzE3Yjc1NTc4ZDZlIiwic291cmNlIjoibWVyZ2UtcHJldmlldyJ9LCJiZWZvcmUiOnsicmVmIjoiYzU4NzJhYmVjYTY3Yzg0OWE5YjJhYzRiNGU5NWQyMTVhMjc0ZmE2NCIsInNvdXJjZSI6ImJhc2UifSwiaWQiOiJESUZGLTAwNiIsIm9sZF9wYXRoIjpudWxsLCJwYXRoIjoiZGVwbG95L21pZ3JhdGlvbnMvMjAyNjEwMjlfaW52b2ljZV90YXguc3FsIiwic3RhdHVzIjoiQSJ9XSwiZW50cnlfcmVwbyI6ImVudHJ5IiwiZmluYWxfcmVmIjoiYTg5NjRlMTljN2Y1MDRiZWRkOGQ2N2I4NDFjMTcxN2I3NTU3OGQ2ZSIsImZpbmFsX3NvdXJjZSI6Im1lcmdlLXByZXZpZXciLCJnYXBfY291bnQiOjAsImdlbmVyYXRlZF9hdCI6IjIwMjYtMTAtMjlUMTY6MzY6MDMuMzE2MjIyKzAwOjAwIiwiaGVhZF9pbnB1dCI6ImZlYXR1cmUvcmV2aWV3IiwiaGVhZF9yZXNvbHZlZF9yZWYiOiJyZWZzL2hlYWRzL2ZlYXR1cmUvcmV2aWV3IiwiaGVhZF9zaGEiOiJiZjE2ODQ1ZTBlNTBjOGYxYzkzYjg5MjM5NmQ1OTI4MmIyNWQzNjYyIiwibWVyZ2VfYmFzZSI6ImM1ODcyYWJlY2E2N2M4NDlhOWIyYWM0YjRlOTVkMjE1YTI3NGZhNjQiLCJtb2RlIjoibWVyZ2UiLCJwZW5kaW5nX2NvdW50IjoyMCwicHJvamVjdCI6Im9yZGVycy1zZXJ2aWNlIiwicmVsZWFzZV9hc3Nlc3NtZW50IjoicGVuZGluZy1jb25maXJtYXRpb24iLCJyZXBvIjoiPFVTRVJfTE9DQUxfQVBQREFUQT5cXFRlbXBcXHEtcnItcmVzb3VyY2UtZW5kcG9pbnQtMTAzMFxcZ3B0LTYuMS1zb2wtY2FuZGlkYXRlLXJlc291cmNlcy0wMVxcZml4dHVyZVxcd29ya3NwYWNlXFxvcmRlcnMtc2VydmljZSIsInJlcG9ydF9yb290IjoiPFVTRVJfTE9DQUxfQVBQREFUQT5cXFRlbXBcXHEtcnItcmVzb3VyY2UtZW5kcG9pbnQtMTAzMFxcZ3B0LTYuMS1zb2wtY2FuZGlkYXRlLXJlc291cmNlcy0wMVxcZml4dHVyZVxcd29ya3NwYWNlXFxvcmRlcnMtc2VydmljZVxccmV2aWV3LXJlcG9ydHMiLCJyZXBvc2l0b3JpZXMiOlt7ImlkIjoiZW50cnkiLCJpbnN0cnVjdGlvbnMiOltdLCJuYW1lIjoib3JkZXJzLXNlcnZpY2UiLCJwYXRoIjoiPFVTRVJfTE9DQUxfQVBQREFUQT5cXFRlbXBcXHEtcnItcmVzb3VyY2UtZW5kcG9pbnQtMTAzMFxcZ3B0LTYuMS1zb2wtY2FuZGlkYXRlLXJlc291cmNlcy0wMVxcZml4dHVyZVxcd29ya3NwYWNlXFxvcmRlcnMtc2VydmljZSIsInJvbGUiOiJlbnRyeSIsInNuYXBzaG90Ijp7ImNhcHR1cmVkX2F0IjoiMjAyNi0xMC0yOVQxNjozNjowNC40MDEwMDErMDA6MDAiLCJnaXRfZGlyIjoicmVwb3MvZW50cnkvc25hcHNob3QuZ2l0Iiwib2JqZWN0X2Zvcm1hdCI6InNoYTEiLCJwYWNrIjp7ImNoZWNrc3VtIjoiNzY1MWMxNzQ2OTFiNjM1ZmY5MzlmZGRkOTcwODVmOGY5ZDhkYWRjYyIsImZpbGUiOiJvYmplY3RzL3BhY2svc25hcHNob3QucGFjayIsImluZGV4Ijoib2JqZWN0cy9wYWNrL3NuYXBzaG90LmlkeCIsImluZGV4X3NoYTI1NiI6IjU0NTM2ODc2ZWIxODlhY2M0YjVmN2EwZmI3NWYwMDI2OGUyMGEyZWQzZDIxZTkyZWVhOTI0NTRmY2ZkODQyYzgiLCJzaGEyNTYiOiIxNmMwZDIxMzBkYjc2YjkyOGY1NmE4NGEzZWE0ZGU1YzQ3MjljYmFjNTZhOGY2YWI3NDQ0MmQ3OTJiMGFmY2U5In0sInBvbGljeSI6ImZyb3plbi1zdGFydC92MSJ9LCJzdGF0dXMiOiJjYXB0dXJlZCJ9XSwicmV2aWV3X2NvbXBsZXRlIjp0cnVlLCJzY2hlbWEiOiJSZWxlYXNlUmV2aWV3UmVwb3J0L3YzIiwic2NvcGUiOiJncm91cCIsInNuYXBzaG90X3BvbGljeSI6ImZyb3plbi1zdGFydC92MSIsInN0YXRpY19ib2R5X3NoYTI1NiI6ImM4NDMyZjE0NzRiMjlkNDQ0ZWZhZWM3ODlhYThkNzk2N2Q5MGI4YmY0MzY5Y2M5M2EwYTQ3MmE4NWExYTljZTMiLCJ3b3JraW5nX3RyZWVfdW5jaGFuZ2VkIjp0cnVlLCJ3b3Jrc3BhY2VfaW5zdHJ1Y3Rpb25zIjpbXX0= -->
