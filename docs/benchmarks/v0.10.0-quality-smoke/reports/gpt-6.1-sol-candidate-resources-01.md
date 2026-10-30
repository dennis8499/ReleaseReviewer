# 上版檢查: orders-service

## 審查結論

**存在已確認阻擋**｜靜態檢查完成。本次移除既有結帳入口，沿用 app.checkout(amount) 的請求將失敗，列 1 項 P1；另有 BillingDb／InvoiceTax、FTP、SFTP 新增部署依賴待人工確認。已檢查全部提交差異及乾淨合併預覽，未驗證實際環境。

問題數量：P0 0／P1 1（僅列直接阻擋上版的程式風險）。

阻擋 1 項｜合併衝突 0 項｜待確認 20 項｜檢查缺口 0 項。
判斷固定於產生時；人工填寫 Checklist 不會自動核准上版。

- 入口 Repo：`orders-service`
- 合併方向：`feature/review`（`f06cec23`） → `main`（`c5872abe`）

## 問題總攬

| 項目 | 等級 | 說明 |
| --- | --- | --- |
| [R-001](#R-001) | P1 | 移除既有結帳入口，沿用原契約的請求無法處理 |

## 問題詳情（工程師參考）

<a id="R-001"></a>

### R-001｜[P1] 移除既有結帳入口，沿用原契約的請求無法處理

**問題描述**

沿用原結帳入口的請求無法完成結帳；新 publish_tax 的參數與副作用也不能直接替代原來只接收 amount 的契約。

影響範圍：仍依既有 app.checkout(amount) 契約呼叫的請求。bootstrap.startup 的 SQL 及函式保持原樣，沒有證據可據此宣稱啟動也失敗。實際外部呼叫端及流量未查驗。

上版阻擋依據：結帳是既有請求入口；以原有 app.checkout(amount) 契約呼叫時，模組缺少該屬性，請求在執行結帳前即失敗。這是本次移除公開入口造成的高影響相容性中斷，應在部署前恢復相容入口或完成呼叫端遷移。

**重現原因**

部署後，既有整合仍以 app.checkout(amount) 處理一筆結帳請求；原本回傳金額的呼叫改為在取得 checkout 函式時失敗。

觸發條件：既有呼叫端載入 app 後，執行 app.checkout(amount)。

依程式推導，未實際重現。

**預期結果**

保留可呼叫的 app.checkout(amount) 並維持原有回傳契約，或在已驗證呼叫端遷移後移除該入口。

**實際結果**

最終 app.py 僅定義 publish_tax(db, ftp, sftp, tax_rate)，不再提供 checkout。呼叫 app.checkout(amount) 會因模組不存在該屬性而拋出 AttributeError。

**證據**

base 的 checkout 呼叫 consume(publish(amount))，兩端以 amount 欄位傳遞並回傳金額。本次完整替換模組，三個函式均被移除；最終 7 行內容沒有 checkout 定義、匯入、別名或相容層。全庫追查只剩 README 的入口導覽，未發現遷移實作。

`orders-service/app.py:7-8`（來源：`base`；固定版本：`c5872abeca67c849a9b2ac4b4e95d215a274fa64`）

內容 SHA-256：`347691be148560ef0927b9494fffb5d5d3d156756a4d63ddd1b43d9f4d7cdb8e`

~~~text
7: def checkout(amount):
8:     return consume(publish(amount))
~~~

`orders-service/app.py:1-7`（來源：`merge-preview`；固定版本：`d639d4b7057a8f2525aebe952cf33ee97471ed95`）

內容 SHA-256：`2a4c1614f406a5c89557ca8e8b97196bfd7ede563c7e54679b81e19ef7e9b620`

~~~text
1: def publish_tax(db, ftp, sftp, tax_rate):
2:     db.execute("INSERT INTO BillingDb.dbo.InvoiceTax (tax_rate) VALUES (?)", (tax_rate,))
3: [含認證或連線設定的行已省略]
4:                remote_dir="/incoming/tax", filename="daily-tax.csv")
5: [含認證或連線設定的行已省略]
6:              key_name="TAX_SFTP_KEY", known_hosts="TAX_SFTP_HOSTKEY",
7:              remote_dir="/archive/tax", filename="daily-tax.csv")
~~~

Diff 起點：DIFF-001、DIFF-003；本次將 app.py 原有的 publish／consume／checkout 全部替換為 publish_tax；base 第 7–8 行存在的 checkout 在最終整個模組中已不存在，亦無相容轉接。

**建議改善方式**

上版前恢復結帳入口的相容性，或提供已完成的呼叫端遷移證據。

保留 checkout(amount) 相容介面並維持既有行為，將稅務發布另設入口；若確實要移除，須先提供並審查所有必要呼叫端的遷移與部署順序。

## 範圍與限制

本次只審查指定 Repo 的已提交差異；其他 Repo 的擷取時 HEAD 是功能鏈佐證。
結論只適用本次固定版本；擷取後分支移動不改寫本次結果。

- 僅審查本機已提交 base／head 與固定乾淨合併預覽；未存取 Git Server、GitLab 或 oracle，也未納入未提交原始碼。
- catalog 僅含入口 Repo，沒有額外上下游 Repo。README 僅供入口導覽；結帳相容性風險由被移除的原函式契約及最終模組推導，未實際重現，也未查驗部署中的外部呼叫端與流量。
- publish_tax 以注入參數作為函式契約；未檢查 DB driver 交易／commit、FTP／SFTP adapter 的檔案內容來源、認證解析、主機金鑰驗證內部行為、ACK、重送或冪等保證。此處不聲稱 adapter 已正確部署或環境依賴不存在。
- 實際 DB 主機與服務帳號、migration 套用、網路／DNS、FTP 被動資料通道、SFTP 金鑰及主機金鑰、目錄權限均未驗證，需在同份報告 Checklist 取得實際證據。
- Windows 路徑長度限制曾使 helper 搜尋紀錄寫入失敗；後續以僅限本次 Python 程序的長路徑 I/O 相容處理沿用同一 request 與封存來源，未修改 helper 或專案原始碼。

**已執行檢查**

- 無紀錄。

**未執行檢查**

- 依使用者要求，未執行專案測試、程式動態重現、建置、部署、DB 查詢或外部連線驗證。

### 新增部署依賴與人工核對

<a id="D-001"></a>

#### D-001｜BillingDb

新函式首次以完整 SQL 物件名稱寫入 BillingDb。實際伺服器、連線建立與服務身分由注入的 db 決定；固定來源未將該參數綁定至 billing_db_connection。（已辨識目標）
連線／用途：publish_tax.db（呼叫端注入）。
**上版準備**：確認注入 db 的連線目標可存取 BillingDb，確認網路、資料庫存在、服務身分及跨資料庫寫入權限；以套用後驗證證據填入清單。

**核對項**：[K-001](#K-001)、[K-002](#K-002)、[K-003](#K-003)、[K-004](#K-004)、[K-005](#K-005)

<a id="D-002"></a>

#### D-002｜BillingDb.dbo.InvoiceTax

首次寫入 InvoiceTax.tax_rate，並新增建表 SQL；欄位為 DECIMAL(5, 2) NOT NULL。版本內存在 migration 不代表正式環境已建立或已排入部署。（已辨識目標）
連線／用途：publish_tax.db（呼叫端注入）。
**上版準備**：確認建表腳本的部署責任與順序，在新程式寫入前建立正確 DB／schema／表與 tax_rate 欄位，核對 INSERT 權限、精度、相容性及重跑／回復策略；不要將此報告當成已套用 migration 的證明。

**核對項**：[K-006](#K-006)、[K-007](#K-007)、[K-008](#K-008)、[K-009](#K-009)、[K-010](#K-010)

<a id="E-001"></a>

#### E-001｜tax-exporter：/incoming/tax/daily-tax.csv

新增 FTP upload，指定 port 21、passive=True、tax-exporter 身分及 /incoming/tax 目錄。主機採固定程式識別，實際部署可達性與檔案內容來源未驗證。（已辨識目標）
連線／用途：publish_tax.ftp／tax-exporter。
**上版準備**：核對 FTP 目標、21 埠及被動資料通道、防火牆與認證；確認 tax-exporter 可寫入目錄及每日同名檔案的覆寫／重送策略，提供實際傳檔驗證。

**核對項**：[K-011](#K-011)、[K-012](#K-012)、[K-013](#K-013)、[K-014](#K-014)、[K-015](#K-015)

<a id="E-002"></a>

#### E-002｜tax-importer：/archive/tax/daily-tax.csv

新增 SFTP put，指定 port 22、tax-importer、/archive/tax；傳入 TAX_SFTP_KEY 與 TAX_SFTP_HOSTKEY 的識別名稱。程式未提供這些名稱的解析器，不能推定它們是已讀取的環境變數。（已辨識目標）
連線／用途：publish_tax.sftp／tax-importer。
**上版準備**：確認 22 埠連線、tax-importer 認證、TAX_SFTP_KEY 的部署與解析、TAX_SFTP_HOSTKEY 的可信指紋核對、目錄寫入權限及覆寫／重送策略；只記錄非機密驗證證據。

**核對項**：[K-016](#K-016)、[K-017](#K-017)、[K-018](#K-018)、[K-019](#K-019)、[K-020](#K-020)

核對狀態限「待確認／已確認／未通過／不適用」。已確認需核對人、日期及開通／套用後驗證證據；
僅有申請工單仍為待確認，不適用需具體理由。必要項目待確認或未通過時，上版準備未完成。
本段核對紀錄可人工更新；固定正文與產生時判斷保留原始快照。

<!-- release-checklist:start -->
### 人工核對紀錄

#### [D-001｜BillingDb](#D-001)

| 編號 | 核對事項 | 狀態 | 核對人 | 日期 | 工單／驗證證據 | 不適用理由 |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="K-001"></a>K-001 | 確認目標主機、連接埠、DNS／路由與防火牆已開通；本機依賴可記錄不適用理由。 | 待確認 | | | | |
| <a id="K-002"></a>K-002 | 確認 TLS／憑證／加密方式與目標環境相符；不適用時說明原因。 | 待確認 | | | | |
| <a id="K-003"></a>K-003 | 確認目標 DB／schema 與所需資料表、欄位、View 或預存程序已建立或套用。 | 待確認 | | | | |
| <a id="K-004"></a>K-004 | 確認 migration／DDL 執行順序、鎖定與資料回填，以及新舊程式版本相容性。 | 待確認 | | | | |
| <a id="K-005"></a>K-005 | 確認實際服務帳號具備本次操作所需 DB／物件權限；其他服務可用不代表本服務已授權。 | 待確認 | | | | |

#### [D-002｜BillingDb.dbo.InvoiceTax](#D-002)

| 編號 | 核對事項 | 狀態 | 核對人 | 日期 | 工單／驗證證據 | 不適用理由 |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="K-006"></a>K-006 | 確認目標主機、連接埠、DNS／路由與防火牆已開通；本機依賴可記錄不適用理由。 | 待確認 | | | | |
| <a id="K-007"></a>K-007 | 確認 TLS／憑證／加密方式與目標環境相符；不適用時說明原因。 | 待確認 | | | | |
| <a id="K-008"></a>K-008 | 確認目標 DB／schema 與所需資料表、欄位、View 或預存程序已建立或套用。 | 待確認 | | | | |
| <a id="K-009"></a>K-009 | 確認 migration／DDL 執行順序、鎖定與資料回填，以及新舊程式版本相容性。 | 待確認 | | | | |
| <a id="K-010"></a>K-010 | 確認實際服務帳號具備本次操作所需 DB／物件權限；其他服務可用不代表本服務已授權。 | 待確認 | | | | |

#### [E-001｜tax-exporter：/incoming/tax/daily-tax.csv](#E-001)

| 編號 | 核對事項 | 狀態 | 核對人 | 日期 | 工單／驗證證據 | 不適用理由 |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="K-011"></a>K-011 | 確認目標主機、連接埠、DNS／路由與防火牆已開通；本機依賴可記錄不適用理由。 | 待確認 | | | | |
| <a id="K-012"></a>K-012 | 確認 TLS／憑證／加密方式與目標環境相符；不適用時說明原因。 | 待確認 | | | | |
| <a id="K-013"></a>K-013 | 確認 FTP 主動／被動模式及資料通道連接埠的防火牆規則。 | 待確認 | | | | |
| <a id="K-014"></a>K-014 | 確認 FTP 帳號與憑證來源已配置且有效。 | 待確認 | | | | |
| <a id="K-015"></a>K-015 | 確認目錄存在及上傳、下載、搬移、刪除所需權限。 | 待確認 | | | | |

#### [E-002｜tax-importer：/archive/tax/daily-tax.csv](#E-002)

| 編號 | 核對事項 | 狀態 | 核對人 | 日期 | 工單／驗證證據 | 不適用理由 |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="K-016"></a>K-016 | 確認目標主機、連接埠、DNS／路由與防火牆已開通；本機依賴可記錄不適用理由。 | 待確認 | | | | |
| <a id="K-017"></a>K-017 | 確認 TLS／憑證／加密方式與目標環境相符；不適用時說明原因。 | 待確認 | | | | |
| <a id="K-018"></a>K-018 | 確認 SFTP 帳號／金鑰及憑證部署來源有效。 | 待確認 | | | | |
| <a id="K-019"></a>K-019 | 確認已核對 SFTP 主機金鑰及輪替安排。 | 待確認 | | | | |
| <a id="K-020"></a>K-020 | 確認遠端目錄及檔案操作權限符合服務帳號需求。 | 待確認 | | | | |

<!-- release-checklist:end -->

### 下一步

- 先處理 R-001：恢復 checkout 契約，或提交並審查必要呼叫端遷移與部署順序。
- 由部署負責人逐項核對 D-001／D-002／E-001／E-002 的 Checklist，填寫核對人、日期與實際驗證證據；不適用須說明理由。
- 確認新函式注入的 DB／傳檔 adapter 與目標環境設定，安排 migration 在寫入前完成；本次未執行這些環境操作。

## 技術審查紀錄（工程師參考）

### 比較資訊

- Repo：`orders-service`
- 本機路徑：`<USER_LOCAL_APPDATA>\Temp\rr-quality-v0.10.0-smoke-src\gpt-6.1-sol-candidate-resources-01\fixture\workspace\orders-service`

| 角色 | 輸入分支 | 實際 ref | Commit（前 8 碼） |
| --- | --- | --- | --- |
| 基礎分支 | `main` | `refs/heads/main` | `c5872abe` |
| 比較分支 | `feature/review` | `refs/heads/feature/review` | `f06cec23` |

- 合併方向：`feature/review` → `main`
- 主要差異範圍（共同祖先 → 比較分支）：`c5872abe` → `f06cec23`
- 最終受審版本：`merge-preview`；`d639d4b7057a8f2525aebe952cf33ee97471ed95`
- 快照擷取：2026-10-29T13:54:25.897733+00:00 → 2026-10-29T13:54:28.050086+00:00（UTC）。
- 適用版本：本報告僅適用上述固定 SHA 及本次擷取的相關 Repo；後續分支更新不改變本次結論。

<details>
<summary>固定版本、搜尋、功能鏈與覆蓋紀錄</summary>

- Repo：<USER_LOCAL_APPDATA>\Temp\rr-quality-v0.10.0-smoke-src\gpt-6.1-sol-candidate-resources-01\fixture\workspace\orders-service
- 報告目錄：<USER_LOCAL_APPDATA>\Temp\rr-quality-v0.10.0-smoke-src\gpt-6.1-sol-candidate-resources-01\fixture\workspace\orders-service\review-reports
- 基礎分支：main
- 比較分支：feature/review
- 基礎 SHA：c5872abeca67c849a9b2ac4b4e95d215a274fa64
- 比較 SHA：f06cec236b0686f9fb87d3568354def598fb0dba
- 共同起點：c5872abeca67c849a9b2ac4b4e95d215a274fa64
- 最終來源：merge-preview
- 最終 SHA：d639d4b7057a8f2525aebe952cf33ee97471ed95
- 擷取開始（UTC）：2026-10-29T13:54:25.897733+00:00
- 快照完成（UTC）：2026-10-29T13:54:28.050086+00:00
- 快照政策：frozen-start/v1
- 本機檔案與暫存區維持原狀：True

版本比對只包含已提交內容。靜態範圍完整不代表目標環境已驗證。

- Diff 起點 DIFF-001：app.py；c5872abeca67c849a9b2ac4b4e95d215a274fa64 → f06cec236b0686f9fb87d3568354def598fb0dba
- Diff 起點 DIFF-002：deploy/migrations/20261029_invoice_tax.sql；c5872abeca67c849a9b2ac4b4e95d215a274fa64 → f06cec236b0686f9fb87d3568354def598fb0dba
- Diff 起點 DIFF-003：app.py；c5872abeca67c849a9b2ac4b4e95d215a274fa64 → d639d4b7057a8f2525aebe952cf33ee97471ed95
- Diff 起點 DIFF-004：deploy/migrations/20261029_invoice_tax.sql；c5872abeca67c849a9b2ac4b4e95d215a274fa64 → d639d4b7057a8f2525aebe952cf33ee97471ed95
### D-001 新增判定與證據

新增類型：first-use；已閱讀 base 全部 5 個檔案並搜尋 BillingDb、billing_db_connection、execute、connect。base 只有 CoreDb.dbo.Orders 的 schema、startup 對 orders 的查詢及未使用的 billing_db_connection 定義；沒有 BillingDb 的執行使用或別名 mapping。既有 DB 執行能力不能證明本服務已可寫入 BillingDb。

Diff 起點：DIFF-001、DIFF-003；app.py 新增 INSERT INTO BillingDb.dbo.InvoiceTax，首次明確指定 BillingDb 寫入目標。

`orders-service/app.py:1-2`（來源：`merge-preview`；固定版本：`d639d4b7057a8f2525aebe952cf33ee97471ed95`）

內容 SHA-256：`2a4c1614f406a5c89557ca8e8b97196bfd7ede563c7e54679b81e19ef7e9b620`

~~~text
1: def publish_tax(db, ftp, sftp, tax_rate):
2:     db.execute("INSERT INTO BillingDb.dbo.InvoiceTax (tax_rate) VALUES (?)", (tax_rate,))
~~~

- 基礎全 Repo 搜尋：命中 0；紀錄 631cfc8f10afcde2be1eef99442d00bda0e1169bf81fb877d7f858e9a855d2dc
- 基礎全 Repo 搜尋：命中 1；紀錄 21724b2dd96dbcea289055c51e8865353fed4070e11eeb561cc3e246d6857e7a
- 基礎全 Repo 搜尋：命中 1；紀錄 1c8079fc05c5a6edb55d30401333a78e7078a45b7d417f7f1f7ca002b49d353c

### D-002 新增判定與證據

新增類型：first-use；base 全庫的 InvoiceTax 與 tax_rate 搜尋皆無命中，閱讀 schema.sql、bootstrap.py、app.py、settings.json 與 README 後未見同一表或欄位的正式使用、定義或 ORM 別名。最終新增 SQL 定義與 INSERT 使用互相對應。

Diff 起點：DIFF-001、DIFF-002、DIFF-003、DIFF-004；本次新增 InvoiceTax 建表 SQL，並在 publish_tax 直接 INSERT tax_rate。

`orders-service/app.py:2-2`（來源：`merge-preview`；固定版本：`d639d4b7057a8f2525aebe952cf33ee97471ed95`）

內容 SHA-256：`2a4c1614f406a5c89557ca8e8b97196bfd7ede563c7e54679b81e19ef7e9b620`

~~~text
2:     db.execute("INSERT INTO BillingDb.dbo.InvoiceTax (tax_rate) VALUES (?)", (tax_rate,))
~~~

`orders-service/deploy/migrations/20261029_invoice_tax.sql:1-1`（來源：`merge-preview`；固定版本：`d639d4b7057a8f2525aebe952cf33ee97471ed95`）

內容 SHA-256：`a41b3aa9f000da8e923e95b4e168502f0f56cc66f426159632e7fd958aca1e05`

~~~text
1: CREATE TABLE BillingDb.dbo.InvoiceTax (tax_rate DECIMAL(5, 2) NOT NULL);
~~~

- 基礎全 Repo 搜尋：命中 0；紀錄 4807d1982161286f3c516fe91483306526a5d4a01df66ae2e615180580cec938
- 基礎全 Repo 搜尋：命中 0；紀錄 c4ea345226119b765ba0a55d314c1eed3960448411d51051afb8a3900d6c4b84

### E-001 新增判定與證據

新增類型：first-use；已搜尋 base 全庫 ftp、upload、put 並閱讀所有來源，沒有 FTP client、目錄、服務身分、傳檔定義或等價別名使用。最終 ftp.upload 的明確傳檔參數是本次新增操作。

Diff 起點：DIFF-001、DIFF-003；publish_tax 新增 ftp.upload 呼叫，導入指定 FTP 目錄與帳號的寫入需求。

`orders-service/app.py:3-4`（來源：`merge-preview`；固定版本：`d639d4b7057a8f2525aebe952cf33ee97471ed95`）

內容 SHA-256：`2a4c1614f406a5c89557ca8e8b97196bfd7ede563c7e54679b81e19ef7e9b620`

~~~text
3: [含認證或連線設定的行已省略]
4:                remote_dir="/incoming/tax", filename="daily-tax.csv")
~~~

- 基礎全 Repo 搜尋：命中 0；紀錄 d909a229e72611007d52868c8950332015a78f61b0e52ed8fc2b99e56760319f
- 基礎全 Repo 搜尋：命中 0；紀錄 321274cb96709a28a3035315efc5d0685cd9f59afbedb016b6867a5be85fa731

### E-002 新增判定與證據

新增類型：first-use；base 全庫 sftp、put、TAX_SFTP_KEY、TAX_SFTP_HOSTKEY 搜尋無命中；閱讀全庫後沒有 SFTP adapter、金鑰來源、主機金鑰檢查或等價目錄使用。最終 sftp.put 明確引入此存取需求。

Diff 起點：DIFF-001、DIFF-003；publish_tax 新增 sftp.put 與金鑰、主機金鑰識別參數，首次需要該 SFTP 目錄與身分。

`orders-service/app.py:5-7`（來源：`merge-preview`；固定版本：`d639d4b7057a8f2525aebe952cf33ee97471ed95`）

內容 SHA-256：`2a4c1614f406a5c89557ca8e8b97196bfd7ede563c7e54679b81e19ef7e9b620`

~~~text
5: [含認證或連線設定的行已省略]
6:              key_name="TAX_SFTP_KEY", known_hosts="TAX_SFTP_HOSTKEY",
7:              remote_dir="/archive/tax", filename="daily-tax.csv")
~~~

- 基礎全 Repo 搜尋：命中 0；紀錄 ff6b1130048367fef83f3c76564359b18285ed0581ec91e2269b014ddd01c64a
- 基礎全 Repo 搜尋：命中 0；紀錄 460300a864e3b5e45e27dc015b419345396a9082f85f9118cb768bd755e4280a
- 基礎全 Repo 搜尋：命中 0；紀錄 5cbc92cbe0e5a7373034ab44e083f1d57118bf6bfb058d313b874342990103b9
- 基礎全 Repo 搜尋：命中 0；紀錄 e4aefeabcee8e697d2a49ebd09423d79768c44a8f74d2d3b8236d35d0bceb73c

### 關聯 Repo 與功能鏈

| Repo | 固定本機分支 | SHA | 狀態 |
| --- | --- | --- | --- |
| entry / orders-service | 入口指定分支 | 見入口版本／未取得 | captured |

**F-001｜entry → entry｜api**
reviewed：原結帳入口 checkout(amount) 以 publish→consume 回傳 amount；本次最終模組移除整條入口且未提供相容層，固定程式可證明舊呼叫契約失效，見 R-001。README 僅用來定位入口，判斷依據為 base 與最終模組。
Diff 起點：DIFF-001、DIFF-003；本次完整替換 app.py，刪除 checkout 及其 publish／consume 實作。

`orders-service/app.py:1-8`（來源：`base`；固定版本：`c5872abeca67c849a9b2ac4b4e95d215a274fa64`）

內容 SHA-256：`347691be148560ef0927b9494fffb5d5d3d156756a4d63ddd1b43d9f4d7cdb8e`

`orders-service/app.py:1-7`（來源：`merge-preview`；固定版本：`d639d4b7057a8f2525aebe952cf33ee97471ed95`）

內容 SHA-256：`2a4c1614f406a5c89557ca8e8b97196bfd7ede563c7e54679b81e19ef7e9b620`

~~~text
1: def publish_tax(db, ftp, sftp, tax_rate):
2:     db.execute("INSERT INTO BillingDb.dbo.InvoiceTax (tax_rate) VALUES (?)", (tax_rate,))
3: [含認證或連線設定的行已省略]
4:                remote_dir="/incoming/tax", filename="daily-tax.csv")
5: [含認證或連線設定的行已省略]
6:              key_name="TAX_SFTP_KEY", known_hosts="TAX_SFTP_HOSTKEY",
7:              remote_dir="/archive/tax", filename="daily-tax.csv")
~~~

**F-002｜entry → entry｜other**
reviewed：以明確新函式 publish_tax 為起點：先 INSERT tax_rate，再 FTP upload，最後 SFTP put；無本地 try／except、重送或補償，例外直接向呼叫端傳遞，正常路徑無明確 return。已核對 SQL 與建表欄位及兩個傳檔目標；DB 交易、adapter 內部行為與部署可用性未驗證，沒有據此推定資料毀損。catalog 沒有 supporting Repo。
Diff 起點：DIFF-001、DIFF-002、DIFF-003、DIFF-004；本次新增稅率 INSERT 與順序傳檔操作，並提供對應 InvoiceTax 建表定義。

`orders-service/app.py:1-7`（來源：`merge-preview`；固定版本：`d639d4b7057a8f2525aebe952cf33ee97471ed95`）

內容 SHA-256：`2a4c1614f406a5c89557ca8e8b97196bfd7ede563c7e54679b81e19ef7e9b620`

~~~text
1: def publish_tax(db, ftp, sftp, tax_rate):
2:     db.execute("INSERT INTO BillingDb.dbo.InvoiceTax (tax_rate) VALUES (?)", (tax_rate,))
3: [含認證或連線設定的行已省略]
4:                remote_dir="/incoming/tax", filename="daily-tax.csv")
5: [含認證或連線設定的行已省略]
6:              key_name="TAX_SFTP_KEY", known_hosts="TAX_SFTP_HOSTKEY",
7:              remote_dir="/archive/tax", filename="daily-tax.csv")
~~~

`orders-service/deploy/migrations/20261029_invoice_tax.sql:1-1`（來源：`merge-preview`；固定版本：`d639d4b7057a8f2525aebe952cf33ee97471ed95`）

內容 SHA-256：`a41b3aa9f000da8e923e95b4e168502f0f56cc66f426159632e7fd958aca1e05`

~~~text
1: CREATE TABLE BillingDb.dbo.InvoiceTax (tax_rate DECIMAL(5, 2) NOT NULL);
~~~

### 靜態覆蓋

| 路徑 | 狀態 | 未完成原因 |
| --- | --- | --- |
| app.py | reviewed |  |
| deploy/migrations/20261029_invoice_tax.sql | reviewed |  |

**依賴類型覆蓋**：

- config：reviewed；已讀取 base／最終設定與全部程式，並追查 connection 與兩個 SFTP 金鑰識別名稱。billing_db_connection 只有未使用定義，未變更、未見 loader 或部署 binding，故不列首次使用或必要外部 consumer 缺口。SFTP 字串是 adapter 的識別參數，未證明為環境變數或設定載入；部署認證事項列於 E-002。
- db：reviewed；新 INSERT 明確指向 BillingDb.dbo.InvoiceTax.tax_rate，新增 SQL 定義相符；base 的 Orders 與 startup 查詢為既有內容。DB 與表首次使用列 D-001／D-002，實際連線及套用情況由 Checklist 核對。
- ftp：reviewed；已沿 ftp.upload 的明確 host／port／passive／account／remote_dir 參數及全庫既有使用追查，新增 FTP 上傳依賴列 E-001。
- sftp：reviewed；已核對 sftp.put、帳號、金鑰識別、主機金鑰識別與目錄，對照 base 全庫並列新增 SFTP 依賴 E-002。
- redis：reviewed；已讀取全部 base／最終來源與差異並搜尋 Redis，未見 Redis client、命令、key、channel 或部署定義；本次新增流程只有明確 SQL、FTP、SFTP 操作。
- kafka：reviewed；已閱讀原 publish／consume 的本地 dict 呼叫與最終替代實作並搜尋 Kafka；原函式並無事件 broker，最終也沒有 Topic、consumer group、Kafka client 或部署定義，沒有新增 Kafka 依賴。
- other：reviewed；已核對全部固定檔案與差異、注入參數及明確操作；除已分類的 DB、FTP、SFTP 外未見新增 HTTP／事件／其他服務契約或部署資源。CSV 檔名列入傳檔依賴，未臆測額外 provider。

</details>

<!-- release-review-report:eyJiYXNlX2lucHV0IjoibWFpbiIsImJhc2VfcmVzb2x2ZWRfcmVmIjoicmVmcy9oZWFkcy9tYWluIiwiYmFzZV9zaGEiOiJjNTg3MmFiZWNhNjdjODQ5YTliMmFjNGI0ZTk1ZDIxNWEyNzRmYTY0IiwiYmxvY2tlcl9jb3VudCI6MSwiY2FwdHVyZV9jb21wbGV0ZWRfYXQiOiIyMDI2LTEwLTI5VDEzOjU0OjI4LjA1MDA4NiswMDowMCIsImNvbnRleHRfc2hhMjU2IjoiYjdmODQ2MTA5NjQ1MTAzZTdmMzM3ZjcxYTRhYWQ0NWUyZmYxNjEyYTIzZWZkZWJlMjMwYTQxMmI4MTA4Mjc3MSIsImNvdmVyYWdlX3BvbGljeSI6ImFuY2VzdG9yLW5vb3AvdjEiLCJkaWZmX2Jhc2UiOiJjNTg3MmFiZWNhNjdjODQ5YTliMmFjNGI0ZTk1ZDIxNWEyNzRmYTY0IiwiZGlmZl9yb290cyI6W3siYWZ0ZXIiOnsicmVmIjoiZjA2Y2VjMjM2YjA2ODZmOWZiODdkMzU2ODM1NGRlZjU5OGZiMGRiYSIsInNvdXJjZSI6ImhlYWQifSwiYmVmb3JlIjp7InJlZiI6ImM1ODcyYWJlY2E2N2M4NDlhOWIyYWM0YjRlOTVkMjE1YTI3NGZhNjQiLCJzb3VyY2UiOiJtZXJnZS1iYXNlIn0sImlkIjoiRElGRi0wMDEiLCJvbGRfcGF0aCI6bnVsbCwicGF0aCI6ImFwcC5weSIsInN0YXR1cyI6Ik0ifSx7ImFmdGVyIjp7InJlZiI6ImYwNmNlYzIzNmIwNjg2ZjlmYjg3ZDM1NjgzNTRkZWY1OThmYjBkYmEiLCJzb3VyY2UiOiJoZWFkIn0sImJlZm9yZSI6eyJyZWYiOiJjNTg3MmFiZWNhNjdjODQ5YTliMmFjNGI0ZTk1ZDIxNWEyNzRmYTY0Iiwic291cmNlIjoibWVyZ2UtYmFzZSJ9LCJpZCI6IkRJRkYtMDAyIiwib2xkX3BhdGgiOm51bGwsInBhdGgiOiJkZXBsb3kvbWlncmF0aW9ucy8yMDI2MTAyOV9pbnZvaWNlX3RheC5zcWwiLCJzdGF0dXMiOiJBIn0seyJhZnRlciI6eyJyZWYiOiJkNjM5ZDRiNzA1N2E4ZjI1MjVhZWJlOTUyY2YzM2VlOTc0NzFlZDk1Iiwic291cmNlIjoibWVyZ2UtcHJldmlldyJ9LCJiZWZvcmUiOnsicmVmIjoiYzU4NzJhYmVjYTY3Yzg0OWE5YjJhYzRiNGU5NWQyMTVhMjc0ZmE2NCIsInNvdXJjZSI6ImJhc2UifSwiaWQiOiJESUZGLTAwMyIsIm9sZF9wYXRoIjpudWxsLCJwYXRoIjoiYXBwLnB5Iiwic3RhdHVzIjoiTSJ9LHsiYWZ0ZXIiOnsicmVmIjoiZDYzOWQ0YjcwNTdhOGYyNTI1YWViZTk1MmNmMzNlZTk3NDcxZWQ5NSIsInNvdXJjZSI6Im1lcmdlLXByZXZpZXcifSwiYmVmb3JlIjp7InJlZiI6ImM1ODcyYWJlY2E2N2M4NDlhOWIyYWM0YjRlOTVkMjE1YTI3NGZhNjQiLCJzb3VyY2UiOiJiYXNlIn0sImlkIjoiRElGRi0wMDQiLCJvbGRfcGF0aCI6bnVsbCwicGF0aCI6ImRlcGxveS9taWdyYXRpb25zLzIwMjYxMDI5X2ludm9pY2VfdGF4LnNxbCIsInN0YXR1cyI6IkEifV0sImVudHJ5X3JlcG8iOiJlbnRyeSIsImZpbmFsX3JlZiI6ImQ2MzlkNGI3MDU3YThmMjUyNWFlYmU5NTJjZjMzZWU5NzQ3MWVkOTUiLCJmaW5hbF9zb3VyY2UiOiJtZXJnZS1wcmV2aWV3IiwiZ2FwX2NvdW50IjowLCJnZW5lcmF0ZWRfYXQiOiIyMDI2LTEwLTI5VDEzOjU0OjI1Ljg5NzczMyswMDowMCIsImhlYWRfaW5wdXQiOiJmZWF0dXJlL3JldmlldyIsImhlYWRfcmVzb2x2ZWRfcmVmIjoicmVmcy9oZWFkcy9mZWF0dXJlL3JldmlldyIsImhlYWRfc2hhIjoiZjA2Y2VjMjM2YjA2ODZmOWZiODdkMzU2ODM1NGRlZjU5OGZiMGRiYSIsIm1lcmdlX2Jhc2UiOiJjNTg3MmFiZWNhNjdjODQ5YTliMmFjNGI0ZTk1ZDIxNWEyNzRmYTY0IiwibW9kZSI6Im1lcmdlIiwicGVuZGluZ19jb3VudCI6MjAsInByb2plY3QiOiJvcmRlcnMtc2VydmljZSIsInJlbGVhc2VfYXNzZXNzbWVudCI6ImJsb2NrZWQiLCJyZXBvIjoiPFVTRVJfTE9DQUxfQVBQREFUQT5cXFRlbXBcXHJyLXF1YWxpdHktdjAuMTAuMC1zbW9rZS1zcmNcXGdwdC02LjEtc29sLWNhbmRpZGF0ZS1yZXNvdXJjZXMtMDFcXGZpeHR1cmVcXHdvcmtzcGFjZVxcb3JkZXJzLXNlcnZpY2UiLCJyZXBvcnRfcm9vdCI6IjxVU0VSX0xPQ0FMX0FQUERBVEE+XFxUZW1wXFxyci1xdWFsaXR5LXYwLjEwLjAtc21va2Utc3JjXFxncHQtNi4xLXNvbC1jYW5kaWRhdGUtcmVzb3VyY2VzLTAxXFxmaXh0dXJlXFx3b3Jrc3BhY2VcXG9yZGVycy1zZXJ2aWNlXFxyZXZpZXctcmVwb3J0cyIsInJlcG9zaXRvcmllcyI6W3siaWQiOiJlbnRyeSIsImluc3RydWN0aW9ucyI6W10sIm5hbWUiOiJvcmRlcnMtc2VydmljZSIsInBhdGgiOiI8VVNFUl9MT0NBTF9BUFBEQVRBPlxcVGVtcFxccnItcXVhbGl0eS12MC4xMC4wLXNtb2tlLXNyY1xcZ3B0LTYuMS1zb2wtY2FuZGlkYXRlLXJlc291cmNlcy0wMVxcZml4dHVyZVxcd29ya3NwYWNlXFxvcmRlcnMtc2VydmljZSIsInJvbGUiOiJlbnRyeSIsInNuYXBzaG90Ijp7ImNhcHR1cmVkX2F0IjoiMjAyNi0xMC0yOVQxMzo1NDoyNy41NDU0MjYrMDA6MDAiLCJnaXRfZGlyIjoicmVwb3MvZW50cnkvc25hcHNob3QuZ2l0Iiwib2JqZWN0X2Zvcm1hdCI6InNoYTEiLCJwYWNrIjp7ImNoZWNrc3VtIjoiNDNkNTFmMjcxMjlmMjU2MjA4NThjZGVlZjQ1MGJmZDE5Y2NmNjdhMCIsImZpbGUiOiJvYmplY3RzL3BhY2svc25hcHNob3QucGFjayIsImluZGV4Ijoib2JqZWN0cy9wYWNrL3NuYXBzaG90LmlkeCIsImluZGV4X3NoYTI1NiI6Ijg4ZTQwMzVkYzI2NzlhMzNiYTkzNGM3YWM1NjdjMDc3ZjNmNWE0Yzc4NDNiNDdjYTQ4N2I1ZTIwNjZkZjYyN2YiLCJzaGEyNTYiOiI2MDU1MDA2MmUzMTNiYjE3MDBlODIxOTdlMDBhYjU0NzFhMjE2ZjI5YzQ3Y2E2MjlhNjBjODI3YWZlZWFhYWVlIn0sInBvbGljeSI6ImZyb3plbi1zdGFydC92MSJ9LCJzdGF0dXMiOiJjYXB0dXJlZCJ9XSwicmV2aWV3X2NvbXBsZXRlIjp0cnVlLCJzY2hlbWEiOiJSZWxlYXNlUmV2aWV3UmVwb3J0L3YzIiwic2NvcGUiOiJncm91cCIsInNuYXBzaG90X3BvbGljeSI6ImZyb3plbi1zdGFydC92MSIsInN0YXRpY19ib2R5X3NoYTI1NiI6IjcwMGIxZmFkNjQxNzY0Y2E4ZTYyNmYwY2RlZjEzNzA3MzRlZjJiMjE4MDgxM2Y2ZjFlYjVkMWI1Zjk2NDk3MzAiLCJ3b3JraW5nX3RyZWVfdW5jaGFuZ2VkIjp0cnVlLCJ3b3Jrc3BhY2VfaW5zdHJ1Y3Rpb25zIjpbXX0= -->
