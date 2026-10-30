# 上版檢查: orders-service

## 審查結論

**待核對上版條件**｜靜態檢查完成。靜態檢查已完成，已檢查範圍未發現有證據的 P0／P1 直接阻擋；新增稅務資料表、FTP／SFTP 匯出與 SFTP 認證識別仍待人工核對部署條件。

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

- 只審查固定已提交 base／head 與乾淨合併預覽，未納入未提交工作檔；catalog 只有入口 Repo，沒有 supporting Repo。
- 新 callable 本身是本次功能鏈入口；固定 Repo 沒有其上層呼叫端或 db／ftp／sftp provider 實作，未臆測任意外部 caller。已辨識其公開呼叫參數、SQL 目標及檔案資源；provider 的 DB commit、認證解析、檔案來源、ACK／重試、授權與實際執行行為未驗證，這些實作與部署確認不等於本次已證實的 P0／P1。
- 程式內固定 FTP／SFTP 端點是否適用正式環境、DNS／網路可達性、資料庫與目錄是否存在、ACL、私鑰與主機信任材料均未連線確認；請依 Checklist 人工查證，不能推定已缺少或已就緒。
- TAX_SFTP_KEY／TAX_SFTP_HOSTKEY 僅能證明為 SFTP 呼叫參數中的邏輯識別；版本內未定義，未聲稱是 os.environ 讀取，也未推定部署缺失。
- 本次僅以靜態證據判斷直接阻擋；沒有 MR 驗證、環境驗證或上版核准。

**已執行檢查**

- 無紀錄。

**未執行檢查**

- 依使用者要求未執行任何測試、部署、DB 或外部環境連線；未存取 Git Server、GitLab 或 oracle。

### 新增部署依賴與人工核對

<a id="C-001"></a>

#### C-001｜TAX_SFTP_KEY

SFTP tax-importer 帳號使用的私鑰識別；本次首次作為 sftp.put 的參數傳入，固定來源未提供預設值或實際材料。此處是邏輯名稱，並非已證明的環境變數讀取。（未找到定義）
連線／用途：入口服務設定。
**上版準備**：確認 SFTP client 對此識別的解析方式、部署材料與服務身分可讀權限；主機金鑰應與目標端核對。記錄核對人、日期與實際驗證證據，勿將私鑰或認證值填入報告。

**核對項**：[K-001](#K-001)

<a id="C-002"></a>

#### C-002｜TAX_SFTP_HOSTKEY

SFTP 遠端主機金鑰／known_hosts 的識別；本次首次作為 sftp.put 的參數傳入，固定來源未提供預設值或實際材料。此處是邏輯名稱，並非已證明的環境變數讀取。（未找到定義）
連線／用途：入口服務設定。
**上版準備**：確認 SFTP client 對此識別的解析方式、部署材料與服務身分可讀權限；主機金鑰應與目標端核對。記錄核對人、日期與實際驗證證據，勿將私鑰或認證值填入報告。

**核對項**：[K-002](#K-002)

<a id="D-001"></a>

#### D-001｜BillingDb.dbo.InvoiceTax

本次新增 INSERT 與建表腳本，目標為 BillingDb.dbo.InvoiceTax，欄位 tax_rate 為 DECIMAL(5,2) NOT NULL。此新表包含該新欄位，不重複列欄位提醒。（已辨識目標）
連線／用途：publish_tax 注入的 db；SQL 明確指定 BillingDb.dbo。
**上版準備**：核對注入 db 的實際伺服器、BillingDb／dbo 目標及服務帳號 INSERT 權限；確認建表腳本先於啟用匯出套用、tax_rate 精度與輸入範圍相容、鎖與回復方案及新舊版本相容性。腳本只有 CREATE TABLE，沒有 CREATE DATABASE 或自動套用流程，須確認資料庫與 schema 已備妥；不推定現場尚未建立。

**核對項**：[K-003](#K-003)、[K-004](#K-004)、[K-005](#K-005)、[K-006](#K-006)、[K-007](#K-007)

<a id="E-001"></a>

#### E-001｜/incoming/tax/daily-tax.csv

publish_tax 在 DB execute 返回後呼叫 ftp.upload，向固定稅務 FTP 邏輯端點的 /incoming/tax 上傳 daily-tax.csv；端點為程式內固定值，並非本次新增設定鍵。（已辨識目標）
連線／用途：稅務 FTP；tax-exporter；port 21；passive 模式。
**上版準備**：核對程式固定目標與正式端點是否一致、DNS／連線與 TCP 21、passive 資料通道防火牆、tax-exporter 認證、/incoming/tax 目錄存在與上傳／覆寫權限；確認 daily-tax.csv 來源、接收契約、重試及重送處理。若覆寫、搬移或刪除不適用，填寫具體理由。

**核對項**：[K-008](#K-008)、[K-009](#K-009)、[K-010](#K-010)、[K-011](#K-011)、[K-012](#K-012)

<a id="E-002"></a>

#### E-002｜/archive/tax/daily-tax.csv

FTP 呼叫返回後執行 sftp.put，將 daily-tax.csv 寫入固定稅務 SFTP 邏輯端點的 /archive/tax，並傳入 TAX_SFTP_KEY 與 TAX_SFTP_HOSTKEY 識別。（已辨識目標）
連線／用途：稅務 SFTP；tax-importer；port 22。
**上版準備**：核對固定目標與正式端點、TCP 22 網路／防火牆、tax-importer 帳號、私鑰部署與讀取權限、主機金鑰信任、/archive/tax 目錄存在及寫入／覆寫權限；確認檔案來源、接收契約、重送及前序 FTP 已成功後的部分失敗處理。

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

#### [E-001｜/incoming/tax/daily-tax.csv](#E-001)

| 編號 | 核對事項 | 狀態 | 核對人 | 日期 | 工單／驗證證據 | 不適用理由 |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="K-008"></a>K-008 | 確認目標主機、連接埠、DNS／路由與防火牆已開通；本機依賴可記錄不適用理由。 | 待確認 | | | | |
| <a id="K-009"></a>K-009 | 確認 TLS／憑證／加密方式與目標環境相符；不適用時說明原因。 | 待確認 | | | | |
| <a id="K-010"></a>K-010 | 確認 FTP 主動／被動模式及資料通道連接埠的防火牆規則。 | 待確認 | | | | |
| <a id="K-011"></a>K-011 | 確認 FTP 帳號與憑證來源已配置且有效。 | 待確認 | | | | |
| <a id="K-012"></a>K-012 | 確認目錄存在及上傳、下載、搬移、刪除所需權限。 | 待確認 | | | | |

#### [E-002｜/archive/tax/daily-tax.csv](#E-002)

| 編號 | 核對事項 | 狀態 | 核對人 | 日期 | 工單／驗證證據 | 不適用理由 |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="K-013"></a>K-013 | 確認目標主機、連接埠、DNS／路由與防火牆已開通；本機依賴可記錄不適用理由。 | 待確認 | | | | |
| <a id="K-014"></a>K-014 | 確認 TLS／憑證／加密方式與目標環境相符；不適用時說明原因。 | 待確認 | | | | |
| <a id="K-015"></a>K-015 | 確認 SFTP 帳號／金鑰及憑證部署來源有效。 | 待確認 | | | | |
| <a id="K-016"></a>K-016 | 確認已核對 SFTP 主機金鑰及輪替安排。 | 待確認 | | | | |
| <a id="K-017"></a>K-017 | 確認遠端目錄及檔案操作權限符合服務帳號需求。 | 待確認 | | | | |

<!-- release-checklist:end -->

### 下一步

- 由維運／DBA 核對 BillingDb／dbo、建表套用順序與服務帳號 INSERT 權限，留下套用後驗證證據。
- 由維運確認 FTP／SFTP 固定目標、網路、帳號、目錄與檔案契約，以及 SFTP 金鑰／主機信任材料。
- 在本報告唯一可編輯 Checklist 填核對人、日期與實際驗證證據；未完成前維持待確認。

## 技術審查紀錄（工程師參考）

### 比較資訊

- Repo：`orders-service`
- 本機路徑：`<USER_LOCAL_APPDATA>\Temp\q-rr-resource-final-1030\gpt-6.1-sol-candidate-resources-01\fixture\workspace\orders-service`

| 角色 | 輸入分支 | 實際 ref | Commit（前 8 碼） |
| --- | --- | --- | --- |
| 基礎分支 | `main` | `refs/heads/main` | `c5872abe` |
| 比較分支 | `feature/review` | `refs/heads/feature/review` | `bf16845e` |

- 合併方向：`feature/review` → `main`
- 主要差異範圍（共同祖先 → 比較分支）：`c5872abe` → `bf16845e`
- 最終受審版本：`merge-preview`；`a8964e19c7f504bedd8d67b841c1717b75578d6e`
- 快照擷取：2026-10-29T16:24:55.761395+00:00 → 2026-10-29T16:24:57.188238+00:00（UTC）。
- 適用版本：本報告僅適用上述固定 SHA 及本次擷取的相關 Repo；後續分支更新不改變本次結論。

<details>
<summary>固定版本、搜尋、功能鏈與覆蓋紀錄</summary>

- Repo：<USER_LOCAL_APPDATA>\Temp\q-rr-resource-final-1030\gpt-6.1-sol-candidate-resources-01\fixture\workspace\orders-service
- 報告目錄：<USER_LOCAL_APPDATA>\Temp\q-rr-resource-final-1030\gpt-6.1-sol-candidate-resources-01\fixture\workspace\orders-service\review-reports
- 基礎分支：main
- 比較分支：feature/review
- 基礎 SHA：c5872abeca67c849a9b2ac4b4e95d215a274fa64
- 比較 SHA：bf16845e0e50c8f1c93b892396d59282b25d3662
- 共同起點：c5872abeca67c849a9b2ac4b4e95d215a274fa64
- 最終來源：merge-preview
- 最終 SHA：a8964e19c7f504bedd8d67b841c1717b75578d6e
- 擷取開始（UTC）：2026-10-29T16:24:55.761395+00:00
- 快照完成（UTC）：2026-10-29T16:24:57.188238+00:00
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

新增類型：first-use；base 全 Repo 搜尋此識別零命中，且已讀完 base 的 app.py、bootstrap.py、settings.json、schema.sql 與 README.md，沒有同用途載入、別名或 SFTP 呼叫。最終版本首次在 app.py:15 引用。

Diff 起點：DIFF-002、DIFF-005；本次新增 publish_tax 的 sftp.put 呼叫，首次實際傳入認證／主機信任識別，並非僅增加未使用設定。

`orders-service/app.py:14-16`（來源：`merge-preview`；固定版本：`a8964e19c7f504bedd8d67b841c1717b75578d6e`）

內容 SHA-256：`bd6bfb36337995b11293466a444cf7e85cb2dc6cd2593cc0fe98fbceac69807b`

~~~text
14: [含認證或連線設定的行已省略]
15:              key_name="TAX_SFTP_KEY", known_hosts="TAX_SFTP_HOSTKEY",
16:              remote_dir="/archive/tax", filename="daily-tax.csv")
~~~

- 基礎全 Repo 搜尋：命中 0；紀錄 5cbc92cbe0e5a7373034ab44e083f1d57118bf6bfb058d313b874342990103b9

### C-002 新增判定與證據

新增類型：first-use；base 全 Repo 搜尋此識別零命中，且已讀完 base 的 app.py、bootstrap.py、settings.json、schema.sql 與 README.md，沒有同用途載入、別名或 SFTP 呼叫。最終版本首次在 app.py:15 引用。

Diff 起點：DIFF-002、DIFF-005；本次新增 publish_tax 的 sftp.put 呼叫，首次實際傳入認證／主機信任識別，並非僅增加未使用設定。

`orders-service/app.py:14-16`（來源：`merge-preview`；固定版本：`a8964e19c7f504bedd8d67b841c1717b75578d6e`）

內容 SHA-256：`bd6bfb36337995b11293466a444cf7e85cb2dc6cd2593cc0fe98fbceac69807b`

~~~text
14: [含認證或連線設定的行已省略]
15:              key_name="TAX_SFTP_KEY", known_hosts="TAX_SFTP_HOSTKEY",
16:              remote_dir="/archive/tax", filename="daily-tax.csv")
~~~

- 基礎全 Repo 搜尋：命中 0；紀錄 e4aefeabcee8e697d2a49ebd09423d79768c44a8f74d2d3b8236d35d0bceb73c

### D-001 新增判定與證據

新增類型：first-use；base 全 Repo 對 BillingDb、InvoiceTax、tax_rate 搜尋均零命中；已核對既有 schema.sql 僅定義 CoreDb.dbo.Orders，bootstrap.py 只讀 orders，app.py 原有 checkout 只處理 amount。settings.json 的 billing_db_connection 是未讀取的既有定義，不能視為 BillingDb 或 InvoiceTax 已有正式使用。

Diff 起點：DIFF-002、DIFF-003、DIFF-005、DIFF-006；新增 publish_tax 首次寫入此完整限定資料表，並新增其 migration；部署須同時準備該表與寫入權限。

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

### E-001 新增判定與證據

新增類型：first-use；base 全 Repo 對 ftp 搜尋零命中；完整讀取 base 全部五個檔案，無 FTP client、部署定義、目錄別名或等價傳輸用途。本次 app.py:12-13 首次新增實際上傳。

Diff 起點：DIFF-002、DIFF-005；新增稅務匯出流程首次呼叫 ftp.upload，增加該服務身分的 FTP 上傳操作與目錄依賴。

`orders-service/app.py:12-13`（來源：`merge-preview`；固定版本：`a8964e19c7f504bedd8d67b841c1717b75578d6e`）

內容 SHA-256：`bd6bfb36337995b11293466a444cf7e85cb2dc6cd2593cc0fe98fbceac69807b`

~~~text
12: [含認證或連線設定的行已省略]
13:                remote_dir="/incoming/tax", filename="daily-tax.csv")
~~~

- 基礎全 Repo 搜尋：命中 0；紀錄 d909a229e72611007d52868c8950332015a78f61b0e52ed8fc2b99e56760319f

### E-002 新增判定與證據

新增類型：first-use；base 全 Repo 對 sftp 搜尋零命中；已讀完 base 全部檔案，沒有 SFTP 操作、金鑰綁定、目錄別名或部署定義。最終 app.py:14-16 為首次實際使用。

Diff 起點：DIFF-002、DIFF-005；新增 publish_tax 首次呼叫 sftp.put，增加該帳號的 SFTP 目錄存取與認證部署需求。

`orders-service/app.py:14-16`（來源：`merge-preview`；固定版本：`a8964e19c7f504bedd8d67b841c1717b75578d6e`）

內容 SHA-256：`bd6bfb36337995b11293466a444cf7e85cb2dc6cd2593cc0fe98fbceac69807b`

~~~text
14: [含認證或連線設定的行已省略]
15:              key_name="TAX_SFTP_KEY", known_hosts="TAX_SFTP_HOSTKEY",
16:              remote_dir="/archive/tax", filename="daily-tax.csv")
~~~

- 基礎全 Repo 搜尋：命中 0；紀錄 ff6b1130048367fef83f3c76564359b18285ed0581ec91e2269b014ddd01c64a

### 關聯 Repo 與功能鏈

| Repo | 固定本機分支 | SHA | 狀態 |
| --- | --- | --- | --- |
| entry / orders-service | 入口指定分支 | 見入口版本／未取得 | captured |

**F-001｜entry → entry｜other**
reviewed：以新增明確入口 app.publish_tax(db, ftp, sftp, tax_rate) 為起點，依序 INSERT BillingDb.dbo.InvoiceTax、FTP upload、SFTP put；同步呼叫無 try/except、補償、重試或顯式回傳，任一步拋出例外會中止後續步驟，全部呼叫返回則函式返回 None。固定契約是傳入的 tax_rate 與具名資源參數；migration 定義對應欄位。既有 checkout 的 amount producer／consumer 及 bootstrap 未變，沒有新增跨 Repo、事件或授權契約。provider 的提交、檔案來源與內部重試未驗證，不據此推定失敗或資料毀損。
Diff 起點：DIFF-002、DIFF-003、DIFF-005、DIFF-006；本次新增稅務匯出入口及其 DB／檔案傳輸順序，需核對新表欄位與三個操作的最終參數。

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

`orders-service/deploy/migrations/20261029_invoice_tax.sql:1-1`（來源：`merge-preview`；固定版本：`a8964e19c7f504bedd8d67b841c1717b75578d6e`）

內容 SHA-256：`a41b3aa9f000da8e923e95b4e168502f0f56cc66f426159632e7fd958aca1e05`

~~~text
1: CREATE TABLE BillingDb.dbo.InvoiceTax (tax_rate DECIMAL(5, 2) NOT NULL);
~~~

### 靜態覆蓋

| 路徑 | 狀態 | 未完成原因 |
| --- | --- | --- |
| README.md | reviewed |  |
| app.py | reviewed |  |
| deploy/migrations/20261029_invoice_tax.sql | reviewed |  |

**依賴類型覆蓋**：

- config：reviewed；已核對新入口、base／最終 settings.json 與全 Repo 識別搜尋；新增 SFTP 金鑰與主機信任識別已列 C-001／C-002。既有 billing_db_connection 沒有讀取或部署 binding，未列新增依賴，也不因找不到 loader 推定必要外部 consumer 缺失。
- db：reviewed；已核對 SQL 寫入、migration、既有 schema 與 bootstrap，並以入口 base 全 Repo 搜尋排除既有使用；新 BillingDb.dbo.InvoiceTax（含 tax_rate）列 D-001。
- ftp：reviewed；已核對新增 ftp.upload 的帳號用途、port、passive 模式、目錄及檔名；base 全 Repo 與完整檔案閱讀未見既有使用，列 E-001。
- sftp：reviewed；已核對新增 sftp.put 的帳號、port、金鑰識別、主機信任識別、目錄及檔名；與 base 完整內容比較，列 E-002。
- redis：reviewed；已閱讀全部固定程式、設定、schema 與新增 migration；沒有 Redis client、快取操作或部署 binding，base／最終 Redis 搜尋亦零命中，本次無新增。
- kafka：reviewed；既有 publish／consume 是同函式內 Python dict 處理，沒有 broker、Topic、Kafka adapter 或部署定義；完整程式閱讀及 base／最終 Kafka 搜尋未見新增 Kafka 依賴。
- other：reviewed；已閱讀全部變更與其餘固定檔案；除已列的 DB、FTP、SFTP 外沒有新的 HTTP、事件、中介服務、套件或部署資源。README 僅補充新 callable 入口。

</details>

<!-- release-review-report:eyJiYXNlX2lucHV0IjoibWFpbiIsImJhc2VfcmVzb2x2ZWRfcmVmIjoicmVmcy9oZWFkcy9tYWluIiwiYmFzZV9zaGEiOiJjNTg3MmFiZWNhNjdjODQ5YTliMmFjNGI0ZTk1ZDIxNWEyNzRmYTY0IiwiYmxvY2tlcl9jb3VudCI6MCwiY2FwdHVyZV9jb21wbGV0ZWRfYXQiOiIyMDI2LTEwLTI5VDE2OjI0OjU3LjE4ODIzOCswMDowMCIsImNvbnRleHRfc2hhMjU2IjoiYTMyMzU1MTdiYjBiMWJlZGQ4NzNkOWE4NmRlYWQ5ZTA1MWFhYzYzMTZkMGExZjY5OTRiZDBlYTg2MzBiMjg5MCIsImNvdmVyYWdlX3BvbGljeSI6ImFuY2VzdG9yLW5vb3AvdjEiLCJkaWZmX2Jhc2UiOiJjNTg3MmFiZWNhNjdjODQ5YTliMmFjNGI0ZTk1ZDIxNWEyNzRmYTY0IiwiZGlmZl9yb290cyI6W3siYWZ0ZXIiOnsicmVmIjoiYmYxNjg0NWUwZTUwYzhmMWM5M2I4OTIzOTZkNTkyODJiMjVkMzY2MiIsInNvdXJjZSI6ImhlYWQifSwiYmVmb3JlIjp7InJlZiI6ImM1ODcyYWJlY2E2N2M4NDlhOWIyYWM0YjRlOTVkMjE1YTI3NGZhNjQiLCJzb3VyY2UiOiJtZXJnZS1iYXNlIn0sImlkIjoiRElGRi0wMDEiLCJvbGRfcGF0aCI6bnVsbCwicGF0aCI6IlJFQURNRS5tZCIsInN0YXR1cyI6Ik0ifSx7ImFmdGVyIjp7InJlZiI6ImJmMTY4NDVlMGU1MGM4ZjFjOTNiODkyMzk2ZDU5MjgyYjI1ZDM2NjIiLCJzb3VyY2UiOiJoZWFkIn0sImJlZm9yZSI6eyJyZWYiOiJjNTg3MmFiZWNhNjdjODQ5YTliMmFjNGI0ZTk1ZDIxNWEyNzRmYTY0Iiwic291cmNlIjoibWVyZ2UtYmFzZSJ9LCJpZCI6IkRJRkYtMDAyIiwib2xkX3BhdGgiOm51bGwsInBhdGgiOiJhcHAucHkiLCJzdGF0dXMiOiJNIn0seyJhZnRlciI6eyJyZWYiOiJiZjE2ODQ1ZTBlNTBjOGYxYzkzYjg5MjM5NmQ1OTI4MmIyNWQzNjYyIiwic291cmNlIjoiaGVhZCJ9LCJiZWZvcmUiOnsicmVmIjoiYzU4NzJhYmVjYTY3Yzg0OWE5YjJhYzRiNGU5NWQyMTVhMjc0ZmE2NCIsInNvdXJjZSI6Im1lcmdlLWJhc2UifSwiaWQiOiJESUZGLTAwMyIsIm9sZF9wYXRoIjpudWxsLCJwYXRoIjoiZGVwbG95L21pZ3JhdGlvbnMvMjAyNjEwMjlfaW52b2ljZV90YXguc3FsIiwic3RhdHVzIjoiQSJ9LHsiYWZ0ZXIiOnsicmVmIjoiYTg5NjRlMTljN2Y1MDRiZWRkOGQ2N2I4NDFjMTcxN2I3NTU3OGQ2ZSIsInNvdXJjZSI6Im1lcmdlLXByZXZpZXcifSwiYmVmb3JlIjp7InJlZiI6ImM1ODcyYWJlY2E2N2M4NDlhOWIyYWM0YjRlOTVkMjE1YTI3NGZhNjQiLCJzb3VyY2UiOiJiYXNlIn0sImlkIjoiRElGRi0wMDQiLCJvbGRfcGF0aCI6bnVsbCwicGF0aCI6IlJFQURNRS5tZCIsInN0YXR1cyI6Ik0ifSx7ImFmdGVyIjp7InJlZiI6ImE4OTY0ZTE5YzdmNTA0YmVkZDhkNjdiODQxYzE3MTdiNzU1NzhkNmUiLCJzb3VyY2UiOiJtZXJnZS1wcmV2aWV3In0sImJlZm9yZSI6eyJyZWYiOiJjNTg3MmFiZWNhNjdjODQ5YTliMmFjNGI0ZTk1ZDIxNWEyNzRmYTY0Iiwic291cmNlIjoiYmFzZSJ9LCJpZCI6IkRJRkYtMDA1Iiwib2xkX3BhdGgiOm51bGwsInBhdGgiOiJhcHAucHkiLCJzdGF0dXMiOiJNIn0seyJhZnRlciI6eyJyZWYiOiJhODk2NGUxOWM3ZjUwNGJlZGQ4ZDY3Yjg0MWMxNzE3Yjc1NTc4ZDZlIiwic291cmNlIjoibWVyZ2UtcHJldmlldyJ9LCJiZWZvcmUiOnsicmVmIjoiYzU4NzJhYmVjYTY3Yzg0OWE5YjJhYzRiNGU5NWQyMTVhMjc0ZmE2NCIsInNvdXJjZSI6ImJhc2UifSwiaWQiOiJESUZGLTAwNiIsIm9sZF9wYXRoIjpudWxsLCJwYXRoIjoiZGVwbG95L21pZ3JhdGlvbnMvMjAyNjEwMjlfaW52b2ljZV90YXguc3FsIiwic3RhdHVzIjoiQSJ9XSwiZW50cnlfcmVwbyI6ImVudHJ5IiwiZmluYWxfcmVmIjoiYTg5NjRlMTljN2Y1MDRiZWRkOGQ2N2I4NDFjMTcxN2I3NTU3OGQ2ZSIsImZpbmFsX3NvdXJjZSI6Im1lcmdlLXByZXZpZXciLCJnYXBfY291bnQiOjAsImdlbmVyYXRlZF9hdCI6IjIwMjYtMTAtMjlUMTY6MjQ6NTUuNzYxMzk1KzAwOjAwIiwiaGVhZF9pbnB1dCI6ImZlYXR1cmUvcmV2aWV3IiwiaGVhZF9yZXNvbHZlZF9yZWYiOiJyZWZzL2hlYWRzL2ZlYXR1cmUvcmV2aWV3IiwiaGVhZF9zaGEiOiJiZjE2ODQ1ZTBlNTBjOGYxYzkzYjg5MjM5NmQ1OTI4MmIyNWQzNjYyIiwibWVyZ2VfYmFzZSI6ImM1ODcyYWJlY2E2N2M4NDlhOWIyYWM0YjRlOTVkMjE1YTI3NGZhNjQiLCJtb2RlIjoibWVyZ2UiLCJwZW5kaW5nX2NvdW50IjoxNywicHJvamVjdCI6Im9yZGVycy1zZXJ2aWNlIiwicmVsZWFzZV9hc3Nlc3NtZW50IjoicGVuZGluZy1jb25maXJtYXRpb24iLCJyZXBvIjoiPFVTRVJfTE9DQUxfQVBQREFUQT5cXFRlbXBcXHEtcnItcmVzb3VyY2UtZmluYWwtMTAzMFxcZ3B0LTYuMS1zb2wtY2FuZGlkYXRlLXJlc291cmNlcy0wMVxcZml4dHVyZVxcd29ya3NwYWNlXFxvcmRlcnMtc2VydmljZSIsInJlcG9ydF9yb290IjoiPFVTRVJfTE9DQUxfQVBQREFUQT5cXFRlbXBcXHEtcnItcmVzb3VyY2UtZmluYWwtMTAzMFxcZ3B0LTYuMS1zb2wtY2FuZGlkYXRlLXJlc291cmNlcy0wMVxcZml4dHVyZVxcd29ya3NwYWNlXFxvcmRlcnMtc2VydmljZVxccmV2aWV3LXJlcG9ydHMiLCJyZXBvc2l0b3JpZXMiOlt7ImlkIjoiZW50cnkiLCJpbnN0cnVjdGlvbnMiOltdLCJuYW1lIjoib3JkZXJzLXNlcnZpY2UiLCJwYXRoIjoiPFVTRVJfTE9DQUxfQVBQREFUQT5cXFRlbXBcXHEtcnItcmVzb3VyY2UtZmluYWwtMTAzMFxcZ3B0LTYuMS1zb2wtY2FuZGlkYXRlLXJlc291cmNlcy0wMVxcZml4dHVyZVxcd29ya3NwYWNlXFxvcmRlcnMtc2VydmljZSIsInJvbGUiOiJlbnRyeSIsInNuYXBzaG90Ijp7ImNhcHR1cmVkX2F0IjoiMjAyNi0xMC0yOVQxNjoyNDo1Ni44ODkxNTArMDA6MDAiLCJnaXRfZGlyIjoicmVwb3MvZW50cnkvc25hcHNob3QuZ2l0Iiwib2JqZWN0X2Zvcm1hdCI6InNoYTEiLCJwYWNrIjp7ImNoZWNrc3VtIjoiNzY1MWMxNzQ2OTFiNjM1ZmY5MzlmZGRkOTcwODVmOGY5ZDhkYWRjYyIsImZpbGUiOiJvYmplY3RzL3BhY2svc25hcHNob3QucGFjayIsImluZGV4Ijoib2JqZWN0cy9wYWNrL3NuYXBzaG90LmlkeCIsImluZGV4X3NoYTI1NiI6IjU0NTM2ODc2ZWIxODlhY2M0YjVmN2EwZmI3NWYwMDI2OGUyMGEyZWQzZDIxZTkyZWVhOTI0NTRmY2ZkODQyYzgiLCJzaGEyNTYiOiIxNmMwZDIxMzBkYjc2YjkyOGY1NmE4NGEzZWE0ZGU1YzQ3MjljYmFjNTZhOGY2YWI3NDQ0MmQ3OTJiMGFmY2U5In0sInBvbGljeSI6ImZyb3plbi1zdGFydC92MSJ9LCJzdGF0dXMiOiJjYXB0dXJlZCJ9XSwicmV2aWV3X2NvbXBsZXRlIjp0cnVlLCJzY2hlbWEiOiJSZWxlYXNlUmV2aWV3UmVwb3J0L3YzIiwic2NvcGUiOiJncm91cCIsInNuYXBzaG90X3BvbGljeSI6ImZyb3plbi1zdGFydC92MSIsInN0YXRpY19ib2R5X3NoYTI1NiI6IjM4NDU4NWZkNTg5MGU1MGEyNDVjMzA5ZTVmYWVhYzcyZDk3YTM2NTQwNDI4YzlhZjIzMzg1OGUzYTYyM2U5OWYiLCJ3b3JraW5nX3RyZWVfdW5jaGFuZ2VkIjp0cnVlLCJ3b3Jrc3BhY2VfaW5zdHJ1Y3Rpb25zIjpbXX0= -->
