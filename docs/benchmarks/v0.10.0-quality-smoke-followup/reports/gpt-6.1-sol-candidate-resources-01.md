# 上版檢查: orders-service

## 審查結論

**待核對上版條件**｜靜態檢查完成。已檢查範圍未發現有證據的 P0／P1 直接程式阻擋；稅務匯出首次使用 BillingDb.dbo.InvoiceTax、FTP、SFTP 及兩個認證／主機金鑰來源識別，上版條件仍待人工核對。

問題數量：P0 0／P1 0（僅列直接阻擋上版的程式風險）。

阻擋 0 項｜合併衝突 0 項｜待確認 17 項｜檢查缺口 0 項。
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

- 審查限定本機已提交 main → feature/review 的固定快照與乾淨合併結果；三個變更檔均已閱讀，另追查 startup、既有 schema 與 settings。無必要 merge-parent 檢視或 supporting Repo。
- README 已明確指定 app.publish_tax 為稅務匯出入口。固定 Repo 未包含實際注入之 db／ftp／sftp client、外部排程或部署平台；未驗證 client 內部提交、重試與檔案傳輸實作。未因任意外部 caller 缺席推定必要功能鏈缺口。
- SQL 已明確限定 BillingDb.dbo.InvoiceTax，不需推測動態資料表。實際連線、DB 存在、migration 是否套用與服務身分權限均未查；既有 billing_db_connection 定義與 db 注入參數的對應沒有版本內證據，報告不宣稱已載入該鍵。
- FTP／SFTP 呼叫含明確主機參數及目錄；固定程式的目標名稱使用 .invalid。未查實際 DNS／路由或 client 的目標處理方式，部署核對須確認指定目標是否可用並符合環境，不能視為網路已開通。
- TAX_SFTP_KEY 與 TAX_SFTP_HOSTKEY 只保存非機密識別名稱；本 Repo 未提供其定義或解析器。報告不推定這兩者一定是環境變數，也不推定現場缺少配置。
- 靜態完成僅代表本次固定範圍已審查；環境未驗證，Checklist 初始全部待確認，未作 MR 驗證或上版核准。
- Helper 原生檔案操作遇到 Windows 長路徑錯誤；後續僅在 helper 行程內將檔案 I/O 參數轉為 Windows extended path，沿用同一 request、固定 SHA 與所有原有證據驗證，未修改 source 或 helper 檔案。

**已執行檢查**

- 無紀錄。

**未執行檢查**

- 依使用者要求，未執行專案測試、部署、DB 查詢或對外服務連線；未存取 Git Server、GitLab 或 oracle。

### 新增部署依賴與人工核對

<a id="C-001"></a>

#### C-001｜TAX_SFTP_KEY

publish_tax 首次將此金鑰來源識別傳入 SFTP client 的 key_name；Repo 未定義其內容或預設值，需確認 client 可取得 tax-importer 的認證。（未找到定義）
連線／用途：入口服務設定。
**上版準備**：確認 TAX_SFTP_KEY 的實際解析及部署來源、tax-importer 私鑰配置與檔案權限；在 Checklist 記錄驗證人、日期與證據，勿填入金鑰內容。

**核對項**：[K-001](#K-001)

<a id="C-002"></a>

#### C-002｜TAX_SFTP_HOSTKEY

publish_tax 首次將此主機金鑰來源識別傳入 SFTP client 的 known_hosts；Repo 未提供定義或預設值。（未找到定義）
連線／用途：入口服務設定。
**上版準備**：確認 client 對 known_hosts 參數的解析方式、受信任主機金鑰的部署位置、指紋與輪替安排；在 Checklist 記錄實際核對證據。

**核對項**：[K-002](#K-002)

<a id="D-001"></a>

#### D-001｜BillingDb.dbo.InvoiceTax

本次首次 INSERT 至明確限定的 BillingDb.dbo.InvoiceTax.tax_rate，並新增 CREATE TABLE migration；欄位為 DECIMAL(5, 2) NOT NULL。涵蓋 BillingDb／dbo 的存取及此新表與欄位準備。（已辨識目標）
連線／用途：publish_tax.db（呼叫端注入的 DB 執行個體）。
**上版準備**：核對注入的 db 可連到並存取 BillingDb；先套用 deploy/migrations/20261029_invoice_tax.sql，再啟用稅務匯出。確認 dbo.InvoiceTax 與 tax_rate 定義、migration 執行身分及服務帳號 INSERT 權限、鎖與部署順序，並留存實際驗證證據；未查現場，不推定缺表。

**核對項**：[K-003](#K-003)、[K-004](#K-004)、[K-005](#K-005)、[K-006](#K-006)、[K-007](#K-007)

<a id="E-001"></a>

#### E-001｜稅務 FTP／incoming/tax／daily-tax.csv

新增 ftp.upload：明確 FTP 邏輯目標、port 21、passive=True、tax-exporter 帳號，將 daily-tax.csv 寫入 /incoming/tax。（已辨識目標）
連線／用途：publish_tax.ftp／tax-exporter（上傳用途）。
**上版準備**：核對程式指定的 FTP 目標主機可用且適合目標環境、21 埠與被動資料通道防火牆、tax-exporter 認證來源、/incoming/tax 目錄及上傳／同名檔案覆寫權限，並確認所需傳輸保護方式；以實際驗證證據完成 Checklist。

**核對項**：[K-008](#K-008)、[K-009](#K-009)、[K-010](#K-010)、[K-011](#K-011)、[K-012](#K-012)

<a id="E-002"></a>

#### E-002｜稅務 SFTP／archive/tax／daily-tax.csv

新增 sftp.put：明確 SFTP 邏輯目標、port 22、tax-importer 帳號，將 daily-tax.csv 寫入 /archive/tax；引用 TAX_SFTP_KEY 與 TAX_SFTP_HOSTKEY。（已辨識目標）
連線／用途：publish_tax.sftp／tax-importer（寫入用途）。
**上版準備**：核對指定的 SFTP 目標主機、22 埠、tax-importer 金鑰部署來源、TAX_SFTP_HOSTKEY 的實際解析與受信任指紋、/archive/tax 目錄及寫入／同名檔案覆寫權限。由核對人填日期與驗證證據；認證內容不寫入報告。

**核對項**：[K-013](#K-013)、[K-014](#K-014)、[K-015](#K-015)、[K-016](#K-016)、[K-017](#K-017)

核對狀態限「待確認／已確認／未通過／不適用」。已確認需核對人、日期及開通／套用後驗證證據；
僅有申請工單仍為待確認，不適用需具體理由。必要項目待確認或未通過時，上版準備未完成。
本段核對紀錄可人工更新；固定正文與產生時判斷保留原始快照。

<!-- release-checklist:start -->
### 人工核對紀錄

#### [C-001｜TAX_SFTP_KEY](#C-001)

| 編號 | 核對事項 | 狀態 | 核對人 | 日期 | 工單／驗證證據 | 不適用理由 |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="K-001"></a>K-001 | 確認必要設定已提供，設定檔路徑、載入與部署注入正確，預設值適用目標環境。 | 待確認 | | | | |

#### [C-002｜TAX_SFTP_HOSTKEY](#C-002)

| 編號 | 核對事項 | 狀態 | 核對人 | 日期 | 工單／驗證證據 | 不適用理由 |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="K-002"></a>K-002 | 確認必要設定已提供，設定檔路徑、載入與部署注入正確，預設值適用目標環境。 | 待確認 | | | | |

#### [D-001｜BillingDb.dbo.InvoiceTax](#D-001)

| 編號 | 核對事項 | 狀態 | 核對人 | 日期 | 工單／驗證證據 | 不適用理由 |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="K-003"></a>K-003 | 確認目標主機、連接埠、DNS／路由與防火牆已開通；本機依賴可記錄不適用理由。 | 待確認 | | | | |
| <a id="K-004"></a>K-004 | 確認 TLS／憑證／加密方式與目標環境相符；不適用時說明原因。 | 待確認 | | | | |
| <a id="K-005"></a>K-005 | 確認目標 DB／schema 與所需資料表、欄位、View 或預存程序已建立或套用。 | 待確認 | | | | |
| <a id="K-006"></a>K-006 | 確認 migration／DDL 執行順序、鎖定與資料回填，以及新舊程式版本相容性。 | 待確認 | | | | |
| <a id="K-007"></a>K-007 | 確認實際服務帳號具備本次操作所需 DB／物件權限；其他服務可用不代表本服務已授權。 | 待確認 | | | | |

#### [E-001｜稅務 FTP／incoming/tax／daily-tax.csv](#E-001)

| 編號 | 核對事項 | 狀態 | 核對人 | 日期 | 工單／驗證證據 | 不適用理由 |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="K-008"></a>K-008 | 確認目標主機、連接埠、DNS／路由與防火牆已開通；本機依賴可記錄不適用理由。 | 待確認 | | | | |
| <a id="K-009"></a>K-009 | 確認 TLS／憑證／加密方式與目標環境相符；不適用時說明原因。 | 待確認 | | | | |
| <a id="K-010"></a>K-010 | 確認 FTP 主動／被動模式及資料通道連接埠的防火牆規則。 | 待確認 | | | | |
| <a id="K-011"></a>K-011 | 確認 FTP 帳號與憑證來源已配置且有效。 | 待確認 | | | | |
| <a id="K-012"></a>K-012 | 確認目錄存在及上傳、下載、搬移、刪除所需權限。 | 待確認 | | | | |

#### [E-002｜稅務 SFTP／archive/tax／daily-tax.csv](#E-002)

| 編號 | 核對事項 | 狀態 | 核對人 | 日期 | 工單／驗證證據 | 不適用理由 |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="K-013"></a>K-013 | 確認目標主機、連接埠、DNS／路由與防火牆已開通；本機依賴可記錄不適用理由。 | 待確認 | | | | |
| <a id="K-014"></a>K-014 | 確認 TLS／憑證／加密方式與目標環境相符；不適用時說明原因。 | 待確認 | | | | |
| <a id="K-015"></a>K-015 | 確認 SFTP 帳號／金鑰及憑證部署來源有效。 | 待確認 | | | | |
| <a id="K-016"></a>K-016 | 確認已核對 SFTP 主機金鑰及輪替安排。 | 待確認 | | | | |
| <a id="K-017"></a>K-017 | 確認遠端目錄及檔案操作權限符合服務帳號需求。 | 待確認 | | | | |

<!-- release-checklist:end -->

### 下一步

- 部署負責人核對 InvoiceTax migration 套用順序、BillingDb 存取、tax_rate 欄位定義及服務帳號 INSERT 權限。
- 由 FTP／SFTP 維運與開發核對兩個指定目標主機、網路、帳號、目錄寫入與檔案覆寫行為，並確認 SFTP 金鑰和主機金鑰來源。
- 在本報告可編輯 Checklist 填寫核對人、日期及實際驗證證據；無適用條件時填明確理由。

## 技術審查紀錄（工程師參考）

### 比較資訊

- Repo：`orders-service`
- 本機路徑：`<USER_LOCAL_APPDATA>\Temp\rr-quality-v0.10.0-smoke-followup-src\gpt-6.1-sol-candidate-resources-01\fixture\workspace\orders-service`

| 角色 | 輸入分支 | 實際 ref | Commit（前 8 碼） |
| --- | --- | --- | --- |
| 基礎分支 | `main` | `refs/heads/main` | `c5872abe` |
| 比較分支 | `feature/review` | `refs/heads/feature/review` | `bf16845e` |

- 合併方向：`feature/review` → `main`
- 主要差異範圍（共同祖先 → 比較分支）：`c5872abe` → `bf16845e`
- 最終受審版本：`merge-preview`；`a8964e19c7f504bedd8d67b841c1717b75578d6e`
- 快照擷取：2026-10-29T14:07:41.639190+00:00 → 2026-10-29T14:07:43.133249+00:00（UTC）。
- 適用版本：本報告僅適用上述固定 SHA 及本次擷取的相關 Repo；後續分支更新不改變本次結論。

<details>
<summary>固定版本、搜尋、功能鏈與覆蓋紀錄</summary>

- Repo：<USER_LOCAL_APPDATA>\Temp\rr-quality-v0.10.0-smoke-followup-src\gpt-6.1-sol-candidate-resources-01\fixture\workspace\orders-service
- 報告目錄：<USER_LOCAL_APPDATA>\Temp\rr-quality-v0.10.0-smoke-followup-src\gpt-6.1-sol-candidate-resources-01\fixture\workspace\orders-service\review-reports
- 基礎分支：main
- 比較分支：feature/review
- 基礎 SHA：c5872abeca67c849a9b2ac4b4e95d215a274fa64
- 比較 SHA：bf16845e0e50c8f1c93b892396d59282b25d3662
- 共同起點：c5872abeca67c849a9b2ac4b4e95d215a274fa64
- 最終來源：merge-preview
- 最終 SHA：a8964e19c7f504bedd8d67b841c1717b75578d6e
- 擷取開始（UTC）：2026-10-29T14:07:41.639190+00:00
- 快照完成（UTC）：2026-10-29T14:07:43.133249+00:00
- 快照政策：frozen-start/v1
- 本機檔案與暫存區維持原狀：True

版本比對只包含已提交內容。靜態範圍完整不代表目標環境已驗證。

- Diff 起點 DIFF-001：README.md；c5872abeca67c849a9b2ac4b4e95d215a274fa64 → bf16845e0e50c8f1c93b892396d59282b25d3662
- Diff 起點 DIFF-002：app.py；c5872abeca67c849a9b2ac4b4e95d215a274fa64 → bf16845e0e50c8f1c93b892396d59282b25d3662
- Diff 起點 DIFF-003：deploy/migrations/20261029_invoice_tax.sql；c5872abeca67c849a9b2ac4b4e95d215a274fa64 → bf16845e0e50c8f1c93b892396d59282b25d3662
- Diff 起點 DIFF-004：README.md；c5872abeca67c849a9b2ac4b4e95d215a274fa64 → a8964e19c7f504bedd8d67b841c1717b75578d6e
- Diff 起點 DIFF-005：app.py；c5872abeca67c849a9b2ac4b4e95d215a274fa64 → a8964e19c7f504bedd8d67b841c1717b75578d6e
- Diff 起點 DIFF-006：deploy/migrations/20261029_invoice_tax.sql；c5872abeca67c849a9b2ac4b4e95d215a274fa64 → a8964e19c7f504bedd8d67b841c1717b75578d6e
### C-001 新增判定與證據

新增類型：first-use；入口 base 全 Repo 無此識別、SFTP 或其他對應 client；已讀 base 的全部五個檔案，未見等價來源或別名。最終版本只在新 SFTP 寫入呼叫引用，未找到版本內定義。

Diff 起點：DIFF-005；新增 publish_tax 的 SFTP 寫入呼叫，首次引用金鑰來源識別。

`orders-service/app.py:14-16`（來源：`merge-preview`；固定版本：`a8964e19c7f504bedd8d67b841c1717b75578d6e`）

內容 SHA-256：`bd6bfb36337995b11293466a444cf7e85cb2dc6cd2593cc0fe98fbceac69807b`

~~~text
14: [含認證或連線設定的行已省略]
15:              key_name="TAX_SFTP_KEY", known_hosts="TAX_SFTP_HOSTKEY",
16:              remote_dir="/archive/tax", filename="daily-tax.csv")
~~~

- 基礎全 Repo 搜尋：命中 0；紀錄 5cbc92cbe0e5a7373034ab44e083f1d57118bf6bfb058d313b874342990103b9
- 基礎全 Repo 搜尋：命中 0；紀錄 ff6b1130048367fef83f3c76564359b18285ed0581ec91e2269b014ddd01c64a

### C-002 新增判定與證據

新增類型：first-use；入口 base 全 Repo 無此識別或 SFTP 使用；已讀全部 base 檔案，未見別名或載入程式。最終版本的唯一引用是新增的 SFTP 呼叫，未找到版本內定義。

Diff 起點：DIFF-005；新增 SFTP 寫入呼叫首次需要主機金鑰來源。

`orders-service/app.py:14-16`（來源：`merge-preview`；固定版本：`a8964e19c7f504bedd8d67b841c1717b75578d6e`）

內容 SHA-256：`bd6bfb36337995b11293466a444cf7e85cb2dc6cd2593cc0fe98fbceac69807b`

~~~text
14: [含認證或連線設定的行已省略]
15:              key_name="TAX_SFTP_KEY", known_hosts="TAX_SFTP_HOSTKEY",
16:              remote_dir="/archive/tax", filename="daily-tax.csv")
~~~

- 基礎全 Repo 搜尋：命中 0；紀錄 e4aefeabcee8e697d2a49ebd09423d79768c44a8f74d2d3b8236d35d0bceb73c
- 基礎全 Repo 搜尋：命中 0；紀錄 ff6b1130048367fef83f3c76564359b18285ed0581ec91e2269b014ddd01c64a

### D-001 新增判定與證據

新增類型：first-use；入口 base 全 Repo 對 BillingDb、InvoiceTax 及 tax_rate 均無命中；全部 base 內容僅有 CoreDb.dbo.Orders 定義與 orders 查詢。settings.json 的 billing_db_connection 僅為定義，沒有 loader 或與 db 參數的 mapping，不足以證明既有 BillingDb 正式使用。

Diff 起點：DIFF-005、DIFF-006；新增 publish_tax 的 INSERT 及 InvoiceTax 建表 migration，首次需要此完整 DB 物件與 tax_rate 欄位。

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

### E-001 新增判定與證據

新增類型：first-use；入口 base 全 Repo 搜尋 ftp 無命中；完整閱讀 base 五檔亦無傳檔 wrapper、別名或相同目錄／帳號的正式使用。最終版本新增明確 FTP 上傳操作。

Diff 起點：DIFF-005；新增 publish_tax 中的 FTP 上傳，首次引入稅務匯出目錄、服務帳號及寫入權限。

`orders-service/app.py:12-13`（來源：`merge-preview`；固定版本：`a8964e19c7f504bedd8d67b841c1717b75578d6e`）

內容 SHA-256：`bd6bfb36337995b11293466a444cf7e85cb2dc6cd2593cc0fe98fbceac69807b`

~~~text
12: [含認證或連線設定的行已省略]
13:                remote_dir="/incoming/tax", filename="daily-tax.csv")
~~~

- 基礎全 Repo 搜尋：命中 0；紀錄 d909a229e72611007d52868c8950332015a78f61b0e52ed8fc2b99e56760319f

### E-002 新增判定與證據

新增類型：first-use；入口 base 全 Repo 搜尋 sftp、TAX_SFTP_KEY、TAX_SFTP_HOSTKEY 均無命中；已讀全部 base 檔案，未見其他正式使用、等價 alias 或同一存取需求。

Diff 起點：DIFF-005；新增 publish_tax 的 SFTP 寫入，首次需要歸檔目錄、金鑰認證與主機金鑰核對。

`orders-service/app.py:14-16`（來源：`merge-preview`；固定版本：`a8964e19c7f504bedd8d67b841c1717b75578d6e`）

內容 SHA-256：`bd6bfb36337995b11293466a444cf7e85cb2dc6cd2593cc0fe98fbceac69807b`

~~~text
14: [含認證或連線設定的行已省略]
15:              key_name="TAX_SFTP_KEY", known_hosts="TAX_SFTP_HOSTKEY",
16:              remote_dir="/archive/tax", filename="daily-tax.csv")
~~~

- 基礎全 Repo 搜尋：命中 0；紀錄 ff6b1130048367fef83f3c76564359b18285ed0581ec91e2269b014ddd01c64a
- 基礎全 Repo 搜尋：命中 0；紀錄 5cbc92cbe0e5a7373034ab44e083f1d57118bf6bfb058d313b874342990103b9
- 基礎全 Repo 搜尋：命中 0；紀錄 e4aefeabcee8e697d2a49ebd09423d79768c44a8f74d2d3b8236d35d0bceb73c

### 關聯 Repo 與功能鏈

| Repo | 固定本機分支 | SHA | 狀態 |
| --- | --- | --- | --- |
| entry / orders-service | 入口指定分支 | 見入口版本／未取得 | captured |

### 靜態覆蓋

| 路徑 | 狀態 | 未完成原因 |
| --- | --- | --- |
| README.md | reviewed |  |
| app.py | reviewed |  |
| deploy/migrations/20261029_invoice_tax.sql | reviewed |  |

**依賴類型覆蓋**：

- config：reviewed；已讀 base／最終版本全部設定及程式，確認 TAX_SFTP_KEY 與 TAX_SFTP_HOSTKEY 在新增 SFTP 呼叫首次引用，列 C-001／C-002；既有 billing_db_connection 僅定義，沒有本次 loader 或部署 binding，不列新增使用。
- db：reviewed；已讀既有 schema／startup、最終 INSERT 及新 migration，並對入口 base 全 Repo 搜尋完整 DB、表及欄位名稱，確認首次使用 BillingDb.dbo.InvoiceTax.tax_rate，列 D-001。
- ftp：reviewed；已讀新增 ftp.upload 及全部 base 檔案，核對帳號、21 埠、被動模式、目錄與上傳操作；base 無既有使用，列 E-001。
- sftp：reviewed；已讀新增 sftp.put 及全部 base 檔案，核對帳號、22 埠、金鑰／主機金鑰識別、目錄與寫入操作；base 無既有使用，列 E-002。
- redis：reviewed；已讀全部 base／最終版本檔案、兩份差異及 import／呼叫內容，本次只有 SQL、FTP 與 SFTP 新增操作，無 Redis client、key prefix、channel、命令或部署資源變更。
- kafka：reviewed；已讀全部 base／最終版本檔案及差異；既有 publish／consume 僅在程序內傳遞 amount 字典，無 broker 或事件發送。本次沒有 Kafka provider、Topic、Consumer Group 或部署定義。
- other：reviewed；已讀全部 base／最終版本與 diff，新增外部互動均可辨識為 DB、FTP 與 SFTP，沒有其他服務 client、事件 schema、部署 binding 或其他新資源定義。

</details>

<!-- release-review-report:eyJiYXNlX2lucHV0IjoibWFpbiIsImJhc2VfcmVzb2x2ZWRfcmVmIjoicmVmcy9oZWFkcy9tYWluIiwiYmFzZV9zaGEiOiJjNTg3MmFiZWNhNjdjODQ5YTliMmFjNGI0ZTk1ZDIxNWEyNzRmYTY0IiwiYmxvY2tlcl9jb3VudCI6MCwiY2FwdHVyZV9jb21wbGV0ZWRfYXQiOiIyMDI2LTEwLTI5VDE0OjA3OjQzLjEzMzI0OSswMDowMCIsImNvbnRleHRfc2hhMjU2IjoiZTIyNzdiZjFkYmJiZjM2OTUxZDFhOWZlYzVkNTBhMDYzY2UzYjZiZTQxZWM5NDE2YjNiYzI2NzVlZTY4ODA1OCIsImNvdmVyYWdlX3BvbGljeSI6ImFuY2VzdG9yLW5vb3AvdjEiLCJkaWZmX2Jhc2UiOiJjNTg3MmFiZWNhNjdjODQ5YTliMmFjNGI0ZTk1ZDIxNWEyNzRmYTY0IiwiZGlmZl9yb290cyI6W3siYWZ0ZXIiOnsicmVmIjoiYmYxNjg0NWUwZTUwYzhmMWM5M2I4OTIzOTZkNTkyODJiMjVkMzY2MiIsInNvdXJjZSI6ImhlYWQifSwiYmVmb3JlIjp7InJlZiI6ImM1ODcyYWJlY2E2N2M4NDlhOWIyYWM0YjRlOTVkMjE1YTI3NGZhNjQiLCJzb3VyY2UiOiJtZXJnZS1iYXNlIn0sImlkIjoiRElGRi0wMDEiLCJvbGRfcGF0aCI6bnVsbCwicGF0aCI6IlJFQURNRS5tZCIsInN0YXR1cyI6Ik0ifSx7ImFmdGVyIjp7InJlZiI6ImJmMTY4NDVlMGU1MGM4ZjFjOTNiODkyMzk2ZDU5MjgyYjI1ZDM2NjIiLCJzb3VyY2UiOiJoZWFkIn0sImJlZm9yZSI6eyJyZWYiOiJjNTg3MmFiZWNhNjdjODQ5YTliMmFjNGI0ZTk1ZDIxNWEyNzRmYTY0Iiwic291cmNlIjoibWVyZ2UtYmFzZSJ9LCJpZCI6IkRJRkYtMDAyIiwib2xkX3BhdGgiOm51bGwsInBhdGgiOiJhcHAucHkiLCJzdGF0dXMiOiJNIn0seyJhZnRlciI6eyJyZWYiOiJiZjE2ODQ1ZTBlNTBjOGYxYzkzYjg5MjM5NmQ1OTI4MmIyNWQzNjYyIiwic291cmNlIjoiaGVhZCJ9LCJiZWZvcmUiOnsicmVmIjoiYzU4NzJhYmVjYTY3Yzg0OWE5YjJhYzRiNGU5NWQyMTVhMjc0ZmE2NCIsInNvdXJjZSI6Im1lcmdlLWJhc2UifSwiaWQiOiJESUZGLTAwMyIsIm9sZF9wYXRoIjpudWxsLCJwYXRoIjoiZGVwbG95L21pZ3JhdGlvbnMvMjAyNjEwMjlfaW52b2ljZV90YXguc3FsIiwic3RhdHVzIjoiQSJ9LHsiYWZ0ZXIiOnsicmVmIjoiYTg5NjRlMTljN2Y1MDRiZWRkOGQ2N2I4NDFjMTcxN2I3NTU3OGQ2ZSIsInNvdXJjZSI6Im1lcmdlLXByZXZpZXcifSwiYmVmb3JlIjp7InJlZiI6ImM1ODcyYWJlY2E2N2M4NDlhOWIyYWM0YjRlOTVkMjE1YTI3NGZhNjQiLCJzb3VyY2UiOiJiYXNlIn0sImlkIjoiRElGRi0wMDQiLCJvbGRfcGF0aCI6bnVsbCwicGF0aCI6IlJFQURNRS5tZCIsInN0YXR1cyI6Ik0ifSx7ImFmdGVyIjp7InJlZiI6ImE4OTY0ZTE5YzdmNTA0YmVkZDhkNjdiODQxYzE3MTdiNzU1NzhkNmUiLCJzb3VyY2UiOiJtZXJnZS1wcmV2aWV3In0sImJlZm9yZSI6eyJyZWYiOiJjNTg3MmFiZWNhNjdjODQ5YTliMmFjNGI0ZTk1ZDIxNWEyNzRmYTY0Iiwic291cmNlIjoiYmFzZSJ9LCJpZCI6IkRJRkYtMDA1Iiwib2xkX3BhdGgiOm51bGwsInBhdGgiOiJhcHAucHkiLCJzdGF0dXMiOiJNIn0seyJhZnRlciI6eyJyZWYiOiJhODk2NGUxOWM3ZjUwNGJlZGQ4ZDY3Yjg0MWMxNzE3Yjc1NTc4ZDZlIiwic291cmNlIjoibWVyZ2UtcHJldmlldyJ9LCJiZWZvcmUiOnsicmVmIjoiYzU4NzJhYmVjYTY3Yzg0OWE5YjJhYzRiNGU5NWQyMTVhMjc0ZmE2NCIsInNvdXJjZSI6ImJhc2UifSwiaWQiOiJESUZGLTAwNiIsIm9sZF9wYXRoIjpudWxsLCJwYXRoIjoiZGVwbG95L21pZ3JhdGlvbnMvMjAyNjEwMjlfaW52b2ljZV90YXguc3FsIiwic3RhdHVzIjoiQSJ9XSwiZW50cnlfcmVwbyI6ImVudHJ5IiwiZmluYWxfcmVmIjoiYTg5NjRlMTljN2Y1MDRiZWRkOGQ2N2I4NDFjMTcxN2I3NTU3OGQ2ZSIsImZpbmFsX3NvdXJjZSI6Im1lcmdlLXByZXZpZXciLCJnYXBfY291bnQiOjAsImdlbmVyYXRlZF9hdCI6IjIwMjYtMTAtMjlUMTQ6MDc6NDEuNjM5MTkwKzAwOjAwIiwiaGVhZF9pbnB1dCI6ImZlYXR1cmUvcmV2aWV3IiwiaGVhZF9yZXNvbHZlZF9yZWYiOiJyZWZzL2hlYWRzL2ZlYXR1cmUvcmV2aWV3IiwiaGVhZF9zaGEiOiJiZjE2ODQ1ZTBlNTBjOGYxYzkzYjg5MjM5NmQ1OTI4MmIyNWQzNjYyIiwibWVyZ2VfYmFzZSI6ImM1ODcyYWJlY2E2N2M4NDlhOWIyYWM0YjRlOTVkMjE1YTI3NGZhNjQiLCJtb2RlIjoibWVyZ2UiLCJwZW5kaW5nX2NvdW50IjoxNywicHJvamVjdCI6Im9yZGVycy1zZXJ2aWNlIiwicmVsZWFzZV9hc3Nlc3NtZW50IjoicGVuZGluZy1jb25maXJtYXRpb24iLCJyZXBvIjoiPFVTRVJfTE9DQUxfQVBQREFUQT5cXFRlbXBcXHJyLXF1YWxpdHktdjAuMTAuMC1zbW9rZS1mb2xsb3d1cC1zcmNcXGdwdC02LjEtc29sLWNhbmRpZGF0ZS1yZXNvdXJjZXMtMDFcXGZpeHR1cmVcXHdvcmtzcGFjZVxcb3JkZXJzLXNlcnZpY2UiLCJyZXBvcnRfcm9vdCI6IjxVU0VSX0xPQ0FMX0FQUERBVEE+XFxUZW1wXFxyci1xdWFsaXR5LXYwLjEwLjAtc21va2UtZm9sbG93dXAtc3JjXFxncHQtNi4xLXNvbC1jYW5kaWRhdGUtcmVzb3VyY2VzLTAxXFxmaXh0dXJlXFx3b3Jrc3BhY2VcXG9yZGVycy1zZXJ2aWNlXFxyZXZpZXctcmVwb3J0cyIsInJlcG9zaXRvcmllcyI6W3siaWQiOiJlbnRyeSIsImluc3RydWN0aW9ucyI6W10sIm5hbWUiOiJvcmRlcnMtc2VydmljZSIsInBhdGgiOiI8VVNFUl9MT0NBTF9BUFBEQVRBPlxcVGVtcFxccnItcXVhbGl0eS12MC4xMC4wLXNtb2tlLWZvbGxvd3VwLXNyY1xcZ3B0LTYuMS1zb2wtY2FuZGlkYXRlLXJlc291cmNlcy0wMVxcZml4dHVyZVxcd29ya3NwYWNlXFxvcmRlcnMtc2VydmljZSIsInJvbGUiOiJlbnRyeSIsInNuYXBzaG90Ijp7ImNhcHR1cmVkX2F0IjoiMjAyNi0xMC0yOVQxNDowNzo0Mi43OTgwNzQrMDA6MDAiLCJnaXRfZGlyIjoicmVwb3MvZW50cnkvc25hcHNob3QuZ2l0Iiwib2JqZWN0X2Zvcm1hdCI6InNoYTEiLCJwYWNrIjp7ImNoZWNrc3VtIjoiNzY1MWMxNzQ2OTFiNjM1ZmY5MzlmZGRkOTcwODVmOGY5ZDhkYWRjYyIsImZpbGUiOiJvYmplY3RzL3BhY2svc25hcHNob3QucGFjayIsImluZGV4Ijoib2JqZWN0cy9wYWNrL3NuYXBzaG90LmlkeCIsImluZGV4X3NoYTI1NiI6IjU0NTM2ODc2ZWIxODlhY2M0YjVmN2EwZmI3NWYwMDI2OGUyMGEyZWQzZDIxZTkyZWVhOTI0NTRmY2ZkODQyYzgiLCJzaGEyNTYiOiIxNmMwZDIxMzBkYjc2YjkyOGY1NmE4NGEzZWE0ZGU1YzQ3MjljYmFjNTZhOGY2YWI3NDQ0MmQ3OTJiMGFmY2U5In0sInBvbGljeSI6ImZyb3plbi1zdGFydC92MSJ9LCJzdGF0dXMiOiJjYXB0dXJlZCJ9XSwicmV2aWV3X2NvbXBsZXRlIjp0cnVlLCJzY2hlbWEiOiJSZWxlYXNlUmV2aWV3UmVwb3J0L3YzIiwic2NvcGUiOiJncm91cCIsInNuYXBzaG90X3BvbGljeSI6ImZyb3plbi1zdGFydC92MSIsInN0YXRpY19ib2R5X3NoYTI1NiI6ImY3MGUwZWFjNTYwZjU2ZTJkODljMjBiZmM3ZDFmMDE5OTE4ZTIwMzNkNjc5YzkyOWFkMmVkMzljOTc0MDg3ODQiLCJ3b3JraW5nX3RyZWVfdW5jaGFuZ2VkIjp0cnVlLCJ3b3Jrc3BhY2VfaW5zdHJ1Y3Rpb25zIjpbXX0= -->
