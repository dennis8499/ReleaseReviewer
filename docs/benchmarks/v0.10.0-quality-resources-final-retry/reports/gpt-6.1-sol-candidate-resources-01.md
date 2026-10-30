# 上版檢查: orders-service

## 審查結論

**待核對上版條件**｜靜態檢查完成。已檢查範圍未發現有證據的 P0／P1 直接上版阻擋；稅務匯出新增 BillingDb.dbo.InvoiceTax 寫入、FTP 上傳與 SFTP 傳送，部署前仍須人工確認 migration、服務帳號權限、連線與認證準備。

問題數量：P0 0／P1 0（僅列直接阻擋上版的程式風險）。

阻擋 0 項｜合併衝突 0 項｜待確認 15 項｜檢查缺口 0 項。
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

- 審查範圍為 main 與 feature/review 的已提交變更及固定合併預覽；catalog 僅有入口 Repo，沒有 supporting Repo 或適用 AGENTS.md。未存取 Git Server、GitLab 或 oracle，兩個本機分支未 fetch。
- 已以固定 helper 讀取全部 base／最終來源、初始 worklist 與 diff，並核對新增入口、既有啟動與 checkout；未將未執行的專案測試記為已執行。
- db、ftp、sftp 由函式參數提供，未查實際注入、driver／adapter 內部、交易提交、網路重試或檔案來源機制。程式中目的 DB／schema／表、協定與目錄足以辨識部署依賴；未查環境不等於資源不存在。沒有既定原子性或重送契約可證明其缺少構成本次 P0／P1。
- SQL、FTP、SFTP 依序執行且沒有局部例外處理；靜態可見失敗會中止後續步驟，但未重現外部錯誤、認證、同名檔覆寫或資料精度邊界，不推定 client 的具體行為。
- 主機解析、實際 DB 連線、DDL 套用、ACL、帳號與金鑰來源／主機金鑰均未連線驗證，留部署 Checklist。靜態審查完整不代表環境就緒或核准上版。
- 先前含分隔符的兩筆搜尋依 helper 的 literal 規則零命中，不用作依賴排除證據；已補做逐項 literal 全 Repo 搜尋並逐檔查核。

**已執行檢查**

- 無紀錄。

**未執行檢查**

- 依使用者限制，未執行專案測試、程式重現、部署、migration、DB 查詢或任何實際環境連線。

### 新增部署依賴與人工核對

<a id="D-001"></a>

#### D-001｜BillingDb.dbo.InvoiceTax

稅務匯出首次 INSERT 至 BillingDb.dbo.InvoiceTax.tax_rate；本次新增 migration 建立該表，tax_rate 為 DECIMAL(5,2) NOT NULL。既有 CoreDb.dbo.Orders 與此目標不同；不能以既有 DB 可查詢推定已具備跨 DB 寫入權限。（已辨識目標）
連線／用途：publish_tax 的 db 參數（SQL 明確指定 BillingDb.dbo）。
**上版準備**：在啟用 publish_tax 前確認實際 db 綁定可存取 BillingDb.dbo.InvoiceTax，核對 migration deploy/migrations/20261029_invoice_tax.sql 已依順序套用、tax_rate 型別與既有物件相容，以及服務帳號 INSERT 權限。確認執行 DDL 的帳號權限、鎖與回復安排；如有既有資料，核對回填與新舊版本相容性。未查詢現場，不能斷言物件尚未建立。

**核對項**：[K-001](#K-001)、[K-002](#K-002)、[K-003](#K-003)、[K-004](#K-004)、[K-005](#K-005)

<a id="E-001"></a>

#### E-001｜稅務 FTP /incoming/tax/daily-tax.csv

新增 ftp.upload，指定 TCP 21、passive=True、tax-exporter 帳號及 /incoming/tax 目錄，檔名 daily-tax.csv。程式內的目的地主機識別為固定參數，需核對部署時的解析與目標；報告不保存完整連線設定。（已辨識目標）
連線／用途：ftp 參數／tax-exporter。
**上版準備**：確認 FTP 目的地主機解析、TCP 21 控制連線及被動模式資料通道防火牆；部署 tax-exporter 認證，確認 /incoming/tax 存在並具有上傳／覆寫 daily-tax.csv 所需權限。核對實際 adapter 的檔案來源與同名檔處理契約；讀取、搬移、刪除等未使用操作如不適用，於 Checklist 註明理由。

**核對項**：[K-006](#K-006)、[K-007](#K-007)、[K-008](#K-008)、[K-009](#K-009)、[K-010](#K-010)

<a id="E-002"></a>

#### E-002｜稅務 SFTP /archive/tax/daily-tax.csv

新增 sftp.put，指定 TCP 22、tax-importer、/archive/tax 與 daily-tax.csv；key_name 使用 TAX_SFTP_KEY，known_hosts 使用 TAX_SFTP_HOSTKEY，兩者為非機密識別名稱，不能僅由名稱推定為環境變數或已部署的秘密。（已辨識目標）
連線／用途：sftp 參數／tax-importer。
**上版準備**：確認 SFTP 目的地主機解析及 TCP 22 防火牆；核對 tax-importer 的 TAX_SFTP_KEY 實際來源、部署方式與檔案存取權限，並以可信管道驗證 TAX_SFTP_HOSTKEY 主機金鑰綁定。確認 /archive/tax 及 daily-tax.csv 的寫入／覆寫權限與檔案來源契約；未使用的讀取、搬移或刪除權限如不適用，填寫理由。

**核對項**：[K-011](#K-011)、[K-012](#K-012)、[K-013](#K-013)、[K-014](#K-014)、[K-015](#K-015)

核對狀態限「待確認／已確認／未通過／不適用」。已確認需核對人、日期及開通／套用後驗證證據；
僅有申請工單仍為待確認，不適用需具體理由。必要項目待確認或未通過時，上版準備未完成。
本段核對紀錄可人工更新；固定正文與產生時判斷保留原始快照。

<!-- release-checklist:start -->
### 人工核對紀錄

#### [D-001｜BillingDb.dbo.InvoiceTax](#D-001)

| 編號 | 核對事項 | 狀態 | 核對人 | 日期 | 工單／驗證證據 | 不適用理由 |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="K-001"></a>K-001 | 確認目標主機、連接埠、DNS／路由與防火牆已開通；本機依賴可記錄不適用理由。 | 待確認 | | | | |
| <a id="K-002"></a>K-002 | 確認 TLS／憑證／加密方式與目標環境相符；不適用時說明原因。 | 待確認 | | | | |
| <a id="K-003"></a>K-003 | 確認目標 DB／schema 與所需資料表、欄位、View 或預存程序已建立或套用。 | 待確認 | | | | |
| <a id="K-004"></a>K-004 | 確認 migration／DDL 執行順序、鎖定與資料回填，以及新舊程式版本相容性。 | 待確認 | | | | |
| <a id="K-005"></a>K-005 | 確認實際服務帳號具備本次操作所需 DB／物件權限；其他服務可用不代表本服務已授權。 | 待確認 | | | | |

#### [E-001｜稅務 FTP /incoming/tax/daily-tax.csv](#E-001)

| 編號 | 核對事項 | 狀態 | 核對人 | 日期 | 工單／驗證證據 | 不適用理由 |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="K-006"></a>K-006 | 確認目標主機、連接埠、DNS／路由與防火牆已開通；本機依賴可記錄不適用理由。 | 待確認 | | | | |
| <a id="K-007"></a>K-007 | 確認 TLS／憑證／加密方式與目標環境相符；不適用時說明原因。 | 待確認 | | | | |
| <a id="K-008"></a>K-008 | 確認 FTP 主動／被動模式及資料通道連接埠的防火牆規則。 | 待確認 | | | | |
| <a id="K-009"></a>K-009 | 確認 FTP 帳號與憑證來源已配置且有效。 | 待確認 | | | | |
| <a id="K-010"></a>K-010 | 確認目錄存在及上傳、下載、搬移、刪除所需權限。 | 待確認 | | | | |

#### [E-002｜稅務 SFTP /archive/tax/daily-tax.csv](#E-002)

| 編號 | 核對事項 | 狀態 | 核對人 | 日期 | 工單／驗證證據 | 不適用理由 |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="K-011"></a>K-011 | 確認目標主機、連接埠、DNS／路由與防火牆已開通；本機依賴可記錄不適用理由。 | 待確認 | | | | |
| <a id="K-012"></a>K-012 | 確認 TLS／憑證／加密方式與目標環境相符；不適用時說明原因。 | 待確認 | | | | |
| <a id="K-013"></a>K-013 | 確認 SFTP 帳號／金鑰及憑證部署來源有效。 | 待確認 | | | | |
| <a id="K-014"></a>K-014 | 確認已核對 SFTP 主機金鑰及輪替安排。 | 待確認 | | | | |
| <a id="K-015"></a>K-015 | 確認遠端目錄及檔案操作權限符合服務帳號需求。 | 待確認 | | | | |

<!-- release-checklist:end -->

### 下一步

- 依同一報告的可編輯 Checklist 核對 DB migration／INSERT 權限、FTP 被動資料通道與上傳權限，以及 SFTP 金鑰、主機金鑰與目錄權限。
- 人工確認須填核對人、日期及實際套用／開通後的驗證證據；不適用項須寫理由。

## 技術審查紀錄（工程師參考）

### 比較資訊

- Repo：`orders-service`
- 本機路徑：`<USER_LOCAL_APPDATA>\Temp\rr-quality-v0.10.0-resource-final-retry\gpt-6.1-sol-candidate-resources-01\fixture\workspace\orders-service`

| 角色 | 輸入分支 | 實際 ref | Commit（前 8 碼） |
| --- | --- | --- | --- |
| 基礎分支 | `main` | `refs/heads/main` | `c5872abe` |
| 比較分支 | `feature/review` | `refs/heads/feature/review` | `bf16845e` |

- 合併方向：`feature/review` → `main`
- 主要差異範圍（共同祖先 → 比較分支）：`c5872abe` → `bf16845e`
- 最終受審版本：`merge-preview`；`a8964e19c7f504bedd8d67b841c1717b75578d6e`
- 快照擷取：2026-10-29T15:46:39.784769+00:00 → 2026-10-29T15:46:41.149010+00:00（UTC）。
- 適用版本：本報告僅適用上述固定 SHA 及本次擷取的相關 Repo；後續分支更新不改變本次結論。

<details>
<summary>固定版本、搜尋、功能鏈與覆蓋紀錄</summary>

- Repo：<USER_LOCAL_APPDATA>\Temp\rr-quality-v0.10.0-resource-final-retry\gpt-6.1-sol-candidate-resources-01\fixture\workspace\orders-service
- 報告目錄：<USER_LOCAL_APPDATA>\Temp\rr-quality-v0.10.0-resource-final-retry\gpt-6.1-sol-candidate-resources-01\fixture\workspace\orders-service\review-reports
- 基礎分支：main
- 比較分支：feature/review
- 基礎 SHA：c5872abeca67c849a9b2ac4b4e95d215a274fa64
- 比較 SHA：bf16845e0e50c8f1c93b892396d59282b25d3662
- 共同起點：c5872abeca67c849a9b2ac4b4e95d215a274fa64
- 最終來源：merge-preview
- 最終 SHA：a8964e19c7f504bedd8d67b841c1717b75578d6e
- 擷取開始（UTC）：2026-10-29T15:46:39.784769+00:00
- 快照完成（UTC）：2026-10-29T15:46:41.149010+00:00
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

新增類型：first-use；入口 base 全 Repo 搜尋 InvoiceTax、BillingDb、tax_rate 均無命中；execute 僅命中 bootstrap 的 orders 計數查詢。已讀 base 所有檔案，schema 只有 CoreDb.dbo.Orders；settings 中連線鍵僅為未使用定義，沒有 loader 或別名 mapping 證明同一 BillingDb 寫入已存在。

Diff 起點：DIFF-005、DIFF-006；本次 app.py 新增 INSERT，並新增建立其目標表的 migration，直接引入該表與欄位的部署準備。

`orders-service/app.py:11-11`（來源：`merge-preview`；固定版本：`a8964e19c7f504bedd8d67b841c1717b75578d6e`）

內容 SHA-256：`bd6bfb36337995b11293466a444cf7e85cb2dc6cd2593cc0fe98fbceac69807b`

~~~text
11:     db.execute("INSERT INTO BillingDb.dbo.InvoiceTax (tax_rate) VALUES (?)", (tax_rate,))
~~~

`orders-service/deploy/migrations/20261029_invoice_tax.sql:1-1`（來源：`merge-preview`；固定版本：`a8964e19c7f504bedd8d67b841c1717b75578d6e`）

內容 SHA-256：`a41b3aa9f000da8e923e95b4e168502f0f56cc66f426159632e7fd958aca1e05`

~~~text
1: CREATE TABLE BillingDb.dbo.InvoiceTax (tax_rate DECIMAL(5, 2) NOT NULL);
~~~

- 基礎全 Repo 搜尋：命中 0；紀錄 4807d1982161286f3c516fe91483306526a5d4a01df66ae2e615180580cec938
- 基礎全 Repo 搜尋：命中 0；紀錄 631cfc8f10afcde2be1eef99442d00bda0e1169bf81fb877d7f858e9a855d2dc
- 基礎全 Repo 搜尋：命中 0；紀錄 c4ea345226119b765ba0a55d314c1eed3960448411d51051afb8a3900d6c4b84
- 基礎全 Repo 搜尋：命中 1；紀錄 1c8079fc05c5a6edb55d30401333a78e7078a45b7d417f7f1f7ca002b49d353c

### E-001 新增判定與證據

新增類型：first-use；入口 base 全 Repo 搜尋 ftp 與 upload 均無命中，逐檔檢查程式、設定、schema 與文件也沒有 FTP client、別名或既有上傳用途。

Diff 起點：DIFF-005；新增 publish_tax 的 FTP 上傳呼叫，使入口服務首次需要該帳號、目錄與被動資料通道。

`orders-service/app.py:12-13`（來源：`merge-preview`；固定版本：`a8964e19c7f504bedd8d67b841c1717b75578d6e`）

內容 SHA-256：`bd6bfb36337995b11293466a444cf7e85cb2dc6cd2593cc0fe98fbceac69807b`

~~~text
12: [含認證或連線設定的行已省略]
13:                remote_dir="/incoming/tax", filename="daily-tax.csv")
~~~

- 基礎全 Repo 搜尋：命中 0；紀錄 d909a229e72611007d52868c8950332015a78f61b0e52ed8fc2b99e56760319f
- 基礎全 Repo 搜尋：命中 0；紀錄 321274cb96709a28a3035315efc5d0685cd9f59afbedb016b6867a5be85fa731

### E-002 新增判定與證據

新增類型：first-use；入口 base 全 Repo 搜尋 sftp、put、TAX_SFTP_KEY 與 TAX_SFTP_HOSTKEY 均無命中；已讀全部 base 檔案，未見既有 SFTP 傳送、認證載入或其他別名使用。

Diff 起點：DIFF-005；新增 publish_tax 的 SFTP put 呼叫與金鑰／主機金鑰識別，首次引入該外部目的地及認證要求。

`orders-service/app.py:14-16`（來源：`merge-preview`；固定版本：`a8964e19c7f504bedd8d67b841c1717b75578d6e`）

內容 SHA-256：`bd6bfb36337995b11293466a444cf7e85cb2dc6cd2593cc0fe98fbceac69807b`

~~~text
14: [含認證或連線設定的行已省略]
15:              key_name="TAX_SFTP_KEY", known_hosts="TAX_SFTP_HOSTKEY",
16:              remote_dir="/archive/tax", filename="daily-tax.csv")
~~~

- 基礎全 Repo 搜尋：命中 0；紀錄 ff6b1130048367fef83f3c76564359b18285ed0581ec91e2269b014ddd01c64a
- 基礎全 Repo 搜尋：命中 0；紀錄 460300a864e3b5e45e27dc015b419345396a9082f85f9118cb768bd755e4280a
- 基礎全 Repo 搜尋：命中 0；紀錄 5cbc92cbe0e5a7373034ab44e083f1d57118bf6bfb058d313b874342990103b9
- 基礎全 Repo 搜尋：命中 0；紀錄 e4aefeabcee8e697d2a49ebd09423d79768c44a8f74d2d3b8236d35d0bceb73c

### 關聯 Repo 與功能鏈

| Repo | 固定本機分支 | SHA | 狀態 |
| --- | --- | --- | --- |
| entry / orders-service | 入口指定分支 | 見入口版本／未取得 | captured |

**F-001｜entry → entry｜database**
reviewed：publish_tax 是明確函式入口：將 tax_rate 參數綁定 INSERT 至 BillingDb.dbo.InvoiceTax；migration 建立相同表與欄位。既有 startup 的 orders 查詢與 checkout 的 amount 契約均未改變。db.execute 若拋例外，後續傳送不執行；程式沒有自行補償或重試。
Diff 起點：DIFF-005、DIFF-006；新增匯出函式首次寫入本次 migration 建立的表。

`orders-service/app.py:11-11`（來源：`merge-preview`；固定版本：`a8964e19c7f504bedd8d67b841c1717b75578d6e`）

內容 SHA-256：`bd6bfb36337995b11293466a444cf7e85cb2dc6cd2593cc0fe98fbceac69807b`

~~~text
11:     db.execute("INSERT INTO BillingDb.dbo.InvoiceTax (tax_rate) VALUES (?)", (tax_rate,))
~~~

`orders-service/deploy/migrations/20261029_invoice_tax.sql:1-1`（來源：`merge-preview`；固定版本：`a8964e19c7f504bedd8d67b841c1717b75578d6e`）

內容 SHA-256：`a41b3aa9f000da8e923e95b4e168502f0f56cc66f426159632e7fd958aca1e05`

~~~text
1: CREATE TABLE BillingDb.dbo.InvoiceTax (tax_rate DECIMAL(5, 2) NOT NULL);
~~~

**F-002｜entry → entry｜other**
reviewed：publish_tax 完成 db.execute 後呼叫 ftp.upload 至 /incoming/tax/daily-tax.csv，再呼叫 sftp.put 至 /archive/tax/daily-tax.csv。任一步拋例外即向呼叫端傳遞；全部呼叫正常返回時函式隱含返回 None。沒有新增回傳 payload、事件 consumer 或其他已知跨 Repo 契約。
Diff 起點：DIFF-005；本次新增依序呼叫 FTP 與 SFTP 的處理鏈。

`orders-service/app.py:10-16`（來源：`merge-preview`；固定版本：`a8964e19c7f504bedd8d67b841c1717b75578d6e`）

內容 SHA-256：`bd6bfb36337995b11293466a444cf7e85cb2dc6cd2593cc0fe98fbceac69807b`

~~~text
10: def publish_tax(db, ftp, sftp, tax_rate):
11:     db.execute("INSERT INTO BillingDb.dbo.InvoiceTax (tax_rate) VALUES (?)", (tax_rate,))
12: [含認證或連線設定的行已省略]
13:                remote_dir="/incoming/tax", filename="daily-tax.csv")
14: [含認證或連線設定的行已省略]
15:              key_name="TAX_SFTP_KEY", known_hosts="TAX_SFTP_HOSTKEY",
16:              remote_dir="/archive/tax", filename="daily-tax.csv")
~~~

`orders-service/app.py:12-13`（來源：`merge-preview`；固定版本：`a8964e19c7f504bedd8d67b841c1717b75578d6e`）

內容 SHA-256：`bd6bfb36337995b11293466a444cf7e85cb2dc6cd2593cc0fe98fbceac69807b`

~~~text
12: [含認證或連線設定的行已省略]
13:                remote_dir="/incoming/tax", filename="daily-tax.csv")
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

- config：reviewed；已讀 base／最終 settings、bootstrap、app 及全 Repo 使用搜尋。既有 billing_db_connection／BILLING_DB_CONNECTION 僅出現在設定定義，沒有本次 loader 或部署 binding；不推定與 db 參數相連，也不列首次使用或必要 caller 缺口。TAX_SFTP_KEY／TAX_SFTP_HOSTKEY 是新增 adapter 呼叫的認證識別，併入 SFTP 提醒；未證實為環境變數，不另重複列設定鍵。
- db：reviewed；已核對完整 base 程式與 schema，既有 orders 查詢不等價於新 BillingDb 表寫入；新增 InvoiceTax 表及 tax_rate 欄位已列 D-001，實際套用與跨 DB 權限待人工核對。
- ftp：reviewed；新增 ftp.upload 明確指定協定、21 埠、被動模式、帳號、目錄及檔名，base 無既有等價使用，已列 E-001。
- sftp：reviewed；新增 sftp.put 明確指定 22 埠、帳號、認證識別、主機金鑰識別與目的目錄，已列 E-002。
- redis：reviewed；固定 base 與最終 Repo 所有文字檔均已閱讀；變更僅有 SQL INSERT、migration、FTP 與 SFTP，沒有 Redis client、key、channel、命令或部署定義。
- kafka：reviewed；完整程式已讀：既有 publish／consume 為本地字典往返，沒有事件 broker 或 Kafka adapter；本次也未改該契約或引入 Kafka 定義。
- other：reviewed；完整 base／最終來源與差異已讀，未新增其他服務、資源或套件部署定義；所有新增外部呼叫皆已依明確 FTP／SFTP 介面列項。

</details>

<!-- release-review-report:eyJiYXNlX2lucHV0IjoibWFpbiIsImJhc2VfcmVzb2x2ZWRfcmVmIjoicmVmcy9oZWFkcy9tYWluIiwiYmFzZV9zaGEiOiJjNTg3MmFiZWNhNjdjODQ5YTliMmFjNGI0ZTk1ZDIxNWEyNzRmYTY0IiwiYmxvY2tlcl9jb3VudCI6MCwiY2FwdHVyZV9jb21wbGV0ZWRfYXQiOiIyMDI2LTEwLTI5VDE1OjQ2OjQxLjE0OTAxMCswMDowMCIsImNvbnRleHRfc2hhMjU2IjoiNTNkMDEyODdkNGM0ZGU3NmQ2MDY3MTlmY2RkMGQ0MmUyYzJiMzAwZTVjOWFhZjJhYzljZjk5NzJiNWQxMGFlYiIsImNvdmVyYWdlX3BvbGljeSI6ImFuY2VzdG9yLW5vb3AvdjEiLCJkaWZmX2Jhc2UiOiJjNTg3MmFiZWNhNjdjODQ5YTliMmFjNGI0ZTk1ZDIxNWEyNzRmYTY0IiwiZGlmZl9yb290cyI6W3siYWZ0ZXIiOnsicmVmIjoiYmYxNjg0NWUwZTUwYzhmMWM5M2I4OTIzOTZkNTkyODJiMjVkMzY2MiIsInNvdXJjZSI6ImhlYWQifSwiYmVmb3JlIjp7InJlZiI6ImM1ODcyYWJlY2E2N2M4NDlhOWIyYWM0YjRlOTVkMjE1YTI3NGZhNjQiLCJzb3VyY2UiOiJtZXJnZS1iYXNlIn0sImlkIjoiRElGRi0wMDEiLCJvbGRfcGF0aCI6bnVsbCwicGF0aCI6IlJFQURNRS5tZCIsInN0YXR1cyI6Ik0ifSx7ImFmdGVyIjp7InJlZiI6ImJmMTY4NDVlMGU1MGM4ZjFjOTNiODkyMzk2ZDU5MjgyYjI1ZDM2NjIiLCJzb3VyY2UiOiJoZWFkIn0sImJlZm9yZSI6eyJyZWYiOiJjNTg3MmFiZWNhNjdjODQ5YTliMmFjNGI0ZTk1ZDIxNWEyNzRmYTY0Iiwic291cmNlIjoibWVyZ2UtYmFzZSJ9LCJpZCI6IkRJRkYtMDAyIiwib2xkX3BhdGgiOm51bGwsInBhdGgiOiJhcHAucHkiLCJzdGF0dXMiOiJNIn0seyJhZnRlciI6eyJyZWYiOiJiZjE2ODQ1ZTBlNTBjOGYxYzkzYjg5MjM5NmQ1OTI4MmIyNWQzNjYyIiwic291cmNlIjoiaGVhZCJ9LCJiZWZvcmUiOnsicmVmIjoiYzU4NzJhYmVjYTY3Yzg0OWE5YjJhYzRiNGU5NWQyMTVhMjc0ZmE2NCIsInNvdXJjZSI6Im1lcmdlLWJhc2UifSwiaWQiOiJESUZGLTAwMyIsIm9sZF9wYXRoIjpudWxsLCJwYXRoIjoiZGVwbG95L21pZ3JhdGlvbnMvMjAyNjEwMjlfaW52b2ljZV90YXguc3FsIiwic3RhdHVzIjoiQSJ9LHsiYWZ0ZXIiOnsicmVmIjoiYTg5NjRlMTljN2Y1MDRiZWRkOGQ2N2I4NDFjMTcxN2I3NTU3OGQ2ZSIsInNvdXJjZSI6Im1lcmdlLXByZXZpZXcifSwiYmVmb3JlIjp7InJlZiI6ImM1ODcyYWJlY2E2N2M4NDlhOWIyYWM0YjRlOTVkMjE1YTI3NGZhNjQiLCJzb3VyY2UiOiJiYXNlIn0sImlkIjoiRElGRi0wMDQiLCJvbGRfcGF0aCI6bnVsbCwicGF0aCI6IlJFQURNRS5tZCIsInN0YXR1cyI6Ik0ifSx7ImFmdGVyIjp7InJlZiI6ImE4OTY0ZTE5YzdmNTA0YmVkZDhkNjdiODQxYzE3MTdiNzU1NzhkNmUiLCJzb3VyY2UiOiJtZXJnZS1wcmV2aWV3In0sImJlZm9yZSI6eyJyZWYiOiJjNTg3MmFiZWNhNjdjODQ5YTliMmFjNGI0ZTk1ZDIxNWEyNzRmYTY0Iiwic291cmNlIjoiYmFzZSJ9LCJpZCI6IkRJRkYtMDA1Iiwib2xkX3BhdGgiOm51bGwsInBhdGgiOiJhcHAucHkiLCJzdGF0dXMiOiJNIn0seyJhZnRlciI6eyJyZWYiOiJhODk2NGUxOWM3ZjUwNGJlZGQ4ZDY3Yjg0MWMxNzE3Yjc1NTc4ZDZlIiwic291cmNlIjoibWVyZ2UtcHJldmlldyJ9LCJiZWZvcmUiOnsicmVmIjoiYzU4NzJhYmVjYTY3Yzg0OWE5YjJhYzRiNGU5NWQyMTVhMjc0ZmE2NCIsInNvdXJjZSI6ImJhc2UifSwiaWQiOiJESUZGLTAwNiIsIm9sZF9wYXRoIjpudWxsLCJwYXRoIjoiZGVwbG95L21pZ3JhdGlvbnMvMjAyNjEwMjlfaW52b2ljZV90YXguc3FsIiwic3RhdHVzIjoiQSJ9XSwiZW50cnlfcmVwbyI6ImVudHJ5IiwiZmluYWxfcmVmIjoiYTg5NjRlMTljN2Y1MDRiZWRkOGQ2N2I4NDFjMTcxN2I3NTU3OGQ2ZSIsImZpbmFsX3NvdXJjZSI6Im1lcmdlLXByZXZpZXciLCJnYXBfY291bnQiOjAsImdlbmVyYXRlZF9hdCI6IjIwMjYtMTAtMjlUMTU6NDY6MzkuNzg0NzY5KzAwOjAwIiwiaGVhZF9pbnB1dCI6ImZlYXR1cmUvcmV2aWV3IiwiaGVhZF9yZXNvbHZlZF9yZWYiOiJyZWZzL2hlYWRzL2ZlYXR1cmUvcmV2aWV3IiwiaGVhZF9zaGEiOiJiZjE2ODQ1ZTBlNTBjOGYxYzkzYjg5MjM5NmQ1OTI4MmIyNWQzNjYyIiwibWVyZ2VfYmFzZSI6ImM1ODcyYWJlY2E2N2M4NDlhOWIyYWM0YjRlOTVkMjE1YTI3NGZhNjQiLCJtb2RlIjoibWVyZ2UiLCJwZW5kaW5nX2NvdW50IjoxNSwicHJvamVjdCI6Im9yZGVycy1zZXJ2aWNlIiwicmVsZWFzZV9hc3Nlc3NtZW50IjoicGVuZGluZy1jb25maXJtYXRpb24iLCJyZXBvIjoiPFVTRVJfTE9DQUxfQVBQREFUQT5cXFRlbXBcXHJyLXF1YWxpdHktdjAuMTAuMC1yZXNvdXJjZS1maW5hbC1yZXRyeVxcZ3B0LTYuMS1zb2wtY2FuZGlkYXRlLXJlc291cmNlcy0wMVxcZml4dHVyZVxcd29ya3NwYWNlXFxvcmRlcnMtc2VydmljZSIsInJlcG9ydF9yb290IjoiPFVTRVJfTE9DQUxfQVBQREFUQT5cXFRlbXBcXHJyLXF1YWxpdHktdjAuMTAuMC1yZXNvdXJjZS1maW5hbC1yZXRyeVxcZ3B0LTYuMS1zb2wtY2FuZGlkYXRlLXJlc291cmNlcy0wMVxcZml4dHVyZVxcd29ya3NwYWNlXFxvcmRlcnMtc2VydmljZVxccmV2aWV3LXJlcG9ydHMiLCJyZXBvc2l0b3JpZXMiOlt7ImlkIjoiZW50cnkiLCJpbnN0cnVjdGlvbnMiOltdLCJuYW1lIjoib3JkZXJzLXNlcnZpY2UiLCJwYXRoIjoiPFVTRVJfTE9DQUxfQVBQREFUQT5cXFRlbXBcXHJyLXF1YWxpdHktdjAuMTAuMC1yZXNvdXJjZS1maW5hbC1yZXRyeVxcZ3B0LTYuMS1zb2wtY2FuZGlkYXRlLXJlc291cmNlcy0wMVxcZml4dHVyZVxcd29ya3NwYWNlXFxvcmRlcnMtc2VydmljZSIsInJvbGUiOiJlbnRyeSIsInNuYXBzaG90Ijp7ImNhcHR1cmVkX2F0IjoiMjAyNi0xMC0yOVQxNTo0Njo0MC44NzE5NjQrMDA6MDAiLCJnaXRfZGlyIjoicmVwb3MvZW50cnkvc25hcHNob3QuZ2l0Iiwib2JqZWN0X2Zvcm1hdCI6InNoYTEiLCJwYWNrIjp7ImNoZWNrc3VtIjoiNzY1MWMxNzQ2OTFiNjM1ZmY5MzlmZGRkOTcwODVmOGY5ZDhkYWRjYyIsImZpbGUiOiJvYmplY3RzL3BhY2svc25hcHNob3QucGFjayIsImluZGV4Ijoib2JqZWN0cy9wYWNrL3NuYXBzaG90LmlkeCIsImluZGV4X3NoYTI1NiI6IjU0NTM2ODc2ZWIxODlhY2M0YjVmN2EwZmI3NWYwMDI2OGUyMGEyZWQzZDIxZTkyZWVhOTI0NTRmY2ZkODQyYzgiLCJzaGEyNTYiOiIxNmMwZDIxMzBkYjc2YjkyOGY1NmE4NGEzZWE0ZGU1YzQ3MjljYmFjNTZhOGY2YWI3NDQ0MmQ3OTJiMGFmY2U5In0sInBvbGljeSI6ImZyb3plbi1zdGFydC92MSJ9LCJzdGF0dXMiOiJjYXB0dXJlZCJ9XSwicmV2aWV3X2NvbXBsZXRlIjp0cnVlLCJzY2hlbWEiOiJSZWxlYXNlUmV2aWV3UmVwb3J0L3YzIiwic2NvcGUiOiJncm91cCIsInNuYXBzaG90X3BvbGljeSI6ImZyb3plbi1zdGFydC92MSIsInN0YXRpY19ib2R5X3NoYTI1NiI6IjZkZTEwMDNlZmJkNWMyYTEwNWIzMzYwYzhjYjdjNmViMDYxNDEyNmFkNGU0YjI4ODlmNjhiYjM3ZjlhMWU0OWUiLCJ3b3JraW5nX3RyZWVfdW5jaGFuZ2VkIjp0cnVlLCJ3b3Jrc3BhY2VfaW5zdHJ1Y3Rpb25zIjpbXX0= -->
