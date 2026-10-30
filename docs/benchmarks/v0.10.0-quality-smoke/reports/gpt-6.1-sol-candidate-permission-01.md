# 上版檢查: orders-service

## 審查結論

**待核對上版條件**｜靜態檢查完成。取消訂單處理由查詢改為刪除；已檢查範圍未發現有證據的 P0／P1 直接阻擋，部署前須人工核對 CoreDb.dbo.Orders 的 DELETE 權限與資料清除準備。

問題數量：P0 0／P1 0（僅列直接阻擋上版的程式風險）。

阻擋 0 項｜合併衝突 0 項｜待確認 5 項｜檢查缺口 0 項。
判斷固定於產生時；人工填寫 Checklist 不會自動核准上版。

- 入口 Repo：`orders-service`
- 合併方向：`feature/review`（`433f8191`） → `main`（`ebac3a8c`）

## 問題總攬

| 項目 | 等級 | 說明 |
| --- | --- | --- |
| — | — | 已檢查範圍未發現已確認的直接上版阻擋。 |

## 問題詳情（工程師參考）

已檢查範圍未發現有證據的直接阻擋；待確認條件與缺口見後文。

## 範圍與限制

本次只審查指定 Repo 的已提交差異；其他 Repo 的擷取時 HEAD 是功能鏈佐證。
結論只適用本次固定版本；擷取後分支移動不改寫本次結果。

- 僅審查本機已提交 main → feature/review 的固定快照與乾淨合併結果；唯一變更為 app.py 第 2 行，無 merge commit 或必要 merge-parent，catalog 僅含入口 Repo，沒有 supporting Repo。
- 以 purge_cancelled(db) 為明確功能起點：傳入既有 db 後執行刪除取消訂單的 SQL，直接回傳 execute 結果。函式內沒有交易提交、錯誤處理、授權或重試；這些控制在 base 已同樣不存在，本次未變更。未在固定範圍找到呼叫端，故未推定正式入口會呼叫此函式或既有 consumer 依賴 SELECT 回傳列。
- README 指稱啟動使用 bootstrap.startup、請求使用 app.checkout；最終 app.py 未定義 checkout，base 亦同，此既有不一致未列為本次阻擋。文件未證明 purge_cancelled 為啟動或請求必要鏈路，不因任意外部 caller 未取得而建立靜態缺口。
- 已辨識 SQL 目標 CoreDb.dbo.Orders 與新增 DELETE 操作；實際注入 db 的 provider、服務帳號、交易提交方式、現場資料、關聯約束、ACL、備份與清除政策未連線驗證，留待部署人工核對。無證據聲稱環境缺少權限或資料表；靜態完成不等於環境就緒或核准上版。
- session 首次搜尋遇到 Windows 路徑長度限制（搜尋紀錄路徑 261 字元）；於同一 request 的 helper 程序檔案開啟使用 Windows 長路徑前綴後續跑，沿用原始 SHA、快照與操作 ID，未修改原始碼或重新 capture。

**已執行檢查**

- 無紀錄。

**未執行檢查**

- 依使用者要求，未執行專案測試、部署、DB 查詢或實際環境連線；未存取 Git Server、呼叫 GitLab 或檢視 oracle。

### 新增部署依賴與人工核對

<a id="D-001"></a>

#### D-001｜CoreDb.dbo.Orders

既有資料表新增 DELETE 操作，刪除 state 為 cancelled 的訂單；相較 base 的 SELECT，服務身分需具備刪除權限，並需核對資料保留、關聯約束與清除影響。（已辨識目標）
連線／用途：purge_cancelled(db) 注入的 DB 連線；SQL 明確指定 CoreDb。
**上版準備**：由部署負責人核對實際服務帳號的 DELETE 權限、目標資料庫、取消訂單保留政策、關聯約束、刪除量及鎖影響，確認備份／復原與交易提交方式；於本報告 Checklist 填寫核對人、日期及套用後驗證證據，不適用項須填理由。

**核對項**：[K-001](#K-001)、[K-002](#K-002)、[K-003](#K-003)、[K-004](#K-004)、[K-005](#K-005)

核對狀態限「待確認／已確認／未通過／不適用」。已確認需核對人、日期及開通／套用後驗證證據；
僅有申請工單仍為待確認，不適用需具體理由。必要項目待確認或未通過時，上版準備未完成。
本段核對紀錄可人工更新；固定正文與產生時判斷保留原始快照。

<!-- release-checklist:start -->
### 人工核對紀錄

#### [D-001｜CoreDb.dbo.Orders](#D-001)

| 編號 | 核對事項 | 狀態 | 核對人 | 日期 | 工單／驗證證據 | 不適用理由 |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="K-001"></a>K-001 | 確認目標主機、連接埠、DNS／路由與防火牆已開通；本機依賴可記錄不適用理由。 | 待確認 | | | | |
| <a id="K-002"></a>K-002 | 確認 TLS／憑證／加密方式與目標環境相符；不適用時說明原因。 | 待確認 | | | | |
| <a id="K-003"></a>K-003 | 確認目標 DB／schema 與所需資料表、欄位、View 或預存程序已建立或套用。 | 待確認 | | | | |
| <a id="K-004"></a>K-004 | 確認 migration／DDL 執行順序、鎖定與資料回填，以及新舊程式版本相容性。 | 待確認 | | | | |
| <a id="K-005"></a>K-005 | 確認實際服務帳號具備本次操作所需 DB／物件權限；其他服務可用不代表本服務已授權。 | 待確認 | | | | |

<!-- release-checklist:end -->

### 下一步

- 部署負責人完成 D-001 對應 Checklist，記錄核對人、日期與實際驗證證據，特別確認 DELETE 權限及取消訂單清除政策。

## 技術審查紀錄（工程師參考）

### 比較資訊

- Repo：`orders-service`
- 本機路徑：`<USER_LOCAL_APPDATA>\Temp\rr-quality-v0.10.0-smoke-src\gpt-6.1-sol-candidate-permission-01\fixture\workspace\orders-service`

| 角色 | 輸入分支 | 實際 ref | Commit（前 8 碼） |
| --- | --- | --- | --- |
| 基礎分支 | `main` | `refs/heads/main` | `ebac3a8c` |
| 比較分支 | `feature/review` | `refs/heads/feature/review` | `433f8191` |

- 合併方向：`feature/review` → `main`
- 主要差異範圍（共同祖先 → 比較分支）：`ebac3a8c` → `433f8191`
- 最終受審版本：`merge-preview`；`fc156506c09b64b15215173aaee7e9630b55f9e4`
- 快照擷取：2026-10-29T13:44:26.356907+00:00 → 2026-10-29T13:44:28.842912+00:00（UTC）。
- 適用版本：本報告僅適用上述固定 SHA 及本次擷取的相關 Repo；後續分支更新不改變本次結論。

<details>
<summary>固定版本、搜尋、功能鏈與覆蓋紀錄</summary>

- Repo：<USER_LOCAL_APPDATA>\Temp\rr-quality-v0.10.0-smoke-src\gpt-6.1-sol-candidate-permission-01\fixture\workspace\orders-service
- 報告目錄：<USER_LOCAL_APPDATA>\Temp\rr-quality-v0.10.0-smoke-src\gpt-6.1-sol-candidate-permission-01\fixture\workspace\orders-service\review-reports
- 基礎分支：main
- 比較分支：feature/review
- 基礎 SHA：ebac3a8cd91e462f9fdd4802d3ffff53d76e7aa5
- 比較 SHA：433f819137a8a70188c8984301f8b9e7953485bd
- 共同起點：ebac3a8cd91e462f9fdd4802d3ffff53d76e7aa5
- 最終來源：merge-preview
- 最終 SHA：fc156506c09b64b15215173aaee7e9630b55f9e4
- 擷取開始（UTC）：2026-10-29T13:44:26.356907+00:00
- 快照完成（UTC）：2026-10-29T13:44:28.842912+00:00
- 快照政策：frozen-start/v1
- 本機檔案與暫存區維持原狀：True

版本比對只包含已提交內容。靜態範圍完整不代表目標環境已驗證。

- Diff 起點 DIFF-001：app.py；ebac3a8cd91e462f9fdd4802d3ffff53d76e7aa5 → 433f819137a8a70188c8984301f8b9e7953485bd
- Diff 起點 DIFF-002：app.py；ebac3a8cd91e462f9fdd4802d3ffff53d76e7aa5 → fc156506c09b64b15215173aaee7e9630b55f9e4
### D-001 新增判定與證據

新增類型：access-change；入口 base 全 Repo 的 Orders 搜尋命中 app.py 的完整表名查詢及 bootstrap.py 的未限定表名計數；CoreDb 搜尋僅命中 app.py，DELETE 搜尋零命中。已逐行閱讀三個固定檔案，無其他寫入 client、別名或 mapping。CoreDb.dbo.Orders 與 id／state 已有讀取使用，故只列同一表新增刪除操作，不重列新表、資料庫或新欄位。

Diff 起點：DIFF-002；本次 app.py 唯一差異將既有 SELECT 改為 DELETE，直接新增既有資料表的刪除權限與資料清除部署準備。

`orders-service/app.py:1-2`（來源：`merge-preview`；固定版本：`fc156506c09b64b15215173aaee7e9630b55f9e4`）

內容 SHA-256：`3c6ba0421c989382064bb7865a27f74b4364d1f8cb2f21a7729712fa271e72e4`

~~~text
1: def purge_cancelled(db):
2:     return db.execute("DELETE FROM CoreDb.dbo.Orders WHERE state = 'cancelled'")
~~~

`orders-service/app.py:1-2`（來源：`base`；固定版本：`ebac3a8cd91e462f9fdd4802d3ffff53d76e7aa5`）

內容 SHA-256：`7795212f16b88d7ad1734cbe4a66ffbb8c23bd91d50cbe8db8a517cee911e324`

- 基礎全 Repo 搜尋：命中 2；紀錄 c5807ab7c679a8906aad7969e45bac360ffb8ef7f42beea7288c4b99843354b1
- 基礎全 Repo 搜尋：命中 0；紀錄 2a9c8a08702b9e664a8846bb12650ee046b9f217849a4aea8d3222a3f500d64d
- 基礎全 Repo 搜尋：命中 1；紀錄 6be8777b27a5d53d381b0dbd44d4181b0cad9435879be2a2830423957819fd91

### 關聯 Repo 與功能鏈

| Repo | 固定本機分支 | SHA | 狀態 |
| --- | --- | --- | --- |
| entry / orders-service | 入口指定分支 | 見入口版本／未取得 | captured |

### 靜態覆蓋

| 路徑 | 狀態 | 未完成原因 |
| --- | --- | --- |
| app.py | reviewed |  |

**依賴類型覆蓋**：

- config：reviewed；已讀取 base 與最終完整三檔及差異；db 為既有函式參數，本次未新增設定讀取、環境變數、連線鍵、部署定義或改指目標。
- db：reviewed；已核對完整 SQL、既有啟動查詢及 base 全 Repo 的表名、CoreDb 與 DELETE 搜尋；相同表與條件由 SELECT 改為 DELETE，新增操作已列 D-001，無新物件或新欄位。
- ftp：reviewed；已閱讀完整固定 Repo 與唯一 SQL 差異，無 FTP client、傳檔、目錄操作或部署定義。
- sftp：reviewed；已閱讀完整固定 Repo 與唯一 SQL 差異，無 SFTP client、金鑰、傳檔或部署定義。
- redis：reviewed；已閱讀完整固定 Repo 與唯一 SQL 差異，無 Redis client、key、快取操作或部署定義。
- kafka：reviewed；已閱讀完整固定 Repo 與唯一 SQL 差異，無 Kafka provider、事件發送／消費或部署定義。
- other：reviewed；已閱讀完整固定 Repo 與唯一 SQL 差異，無其他新增外部服務、API、事件契約或部署資源；既有資料庫新增操作已列於 DB。

</details>

<!-- release-review-report:eyJiYXNlX2lucHV0IjoibWFpbiIsImJhc2VfcmVzb2x2ZWRfcmVmIjoicmVmcy9oZWFkcy9tYWluIiwiYmFzZV9zaGEiOiJlYmFjM2E4Y2Q5MWU0NjJmOWZkZDQ4MDJkM2ZmZmY1M2Q3NmU3YWE1IiwiYmxvY2tlcl9jb3VudCI6MCwiY2FwdHVyZV9jb21wbGV0ZWRfYXQiOiIyMDI2LTEwLTI5VDEzOjQ0OjI4Ljg0MjkxMiswMDowMCIsImNvbnRleHRfc2hhMjU2IjoiMzJhNmM3NTM2M2Q0MmE2NzczYTBhY2JlOTZiMTczYTBiMzNjZGY1MWIwODM4NWUwMjI3YzM3NzY3OTk1ZDA2OSIsImNvdmVyYWdlX3BvbGljeSI6ImFuY2VzdG9yLW5vb3AvdjEiLCJkaWZmX2Jhc2UiOiJlYmFjM2E4Y2Q5MWU0NjJmOWZkZDQ4MDJkM2ZmZmY1M2Q3NmU3YWE1IiwiZGlmZl9yb290cyI6W3siYWZ0ZXIiOnsicmVmIjoiNDMzZjgxOTEzN2E4YTcwMTg4Yzg5ODQzMDFmOGI5ZTc5NTM0ODViZCIsInNvdXJjZSI6ImhlYWQifSwiYmVmb3JlIjp7InJlZiI6ImViYWMzYThjZDkxZTQ2MmY5ZmRkNDgwMmQzZmZmZjUzZDc2ZTdhYTUiLCJzb3VyY2UiOiJtZXJnZS1iYXNlIn0sImlkIjoiRElGRi0wMDEiLCJvbGRfcGF0aCI6bnVsbCwicGF0aCI6ImFwcC5weSIsInN0YXR1cyI6Ik0ifSx7ImFmdGVyIjp7InJlZiI6ImZjMTU2NTA2YzA5YjY0YjE1MjE1MTczYWFlZTdlOTYzMGI1NWY5ZTQiLCJzb3VyY2UiOiJtZXJnZS1wcmV2aWV3In0sImJlZm9yZSI6eyJyZWYiOiJlYmFjM2E4Y2Q5MWU0NjJmOWZkZDQ4MDJkM2ZmZmY1M2Q3NmU3YWE1Iiwic291cmNlIjoiYmFzZSJ9LCJpZCI6IkRJRkYtMDAyIiwib2xkX3BhdGgiOm51bGwsInBhdGgiOiJhcHAucHkiLCJzdGF0dXMiOiJNIn1dLCJlbnRyeV9yZXBvIjoiZW50cnkiLCJmaW5hbF9yZWYiOiJmYzE1NjUwNmMwOWI2NGIxNTIxNTE3M2FhZWU3ZTk2MzBiNTVmOWU0IiwiZmluYWxfc291cmNlIjoibWVyZ2UtcHJldmlldyIsImdhcF9jb3VudCI6MCwiZ2VuZXJhdGVkX2F0IjoiMjAyNi0xMC0yOVQxMzo0NDoyNi4zNTY5MDcrMDA6MDAiLCJoZWFkX2lucHV0IjoiZmVhdHVyZS9yZXZpZXciLCJoZWFkX3Jlc29sdmVkX3JlZiI6InJlZnMvaGVhZHMvZmVhdHVyZS9yZXZpZXciLCJoZWFkX3NoYSI6IjQzM2Y4MTkxMzdhOGE3MDE4OGM4OTg0MzAxZjhiOWU3OTUzNDg1YmQiLCJtZXJnZV9iYXNlIjoiZWJhYzNhOGNkOTFlNDYyZjlmZGQ0ODAyZDNmZmZmNTNkNzZlN2FhNSIsIm1vZGUiOiJtZXJnZSIsInBlbmRpbmdfY291bnQiOjUsInByb2plY3QiOiJvcmRlcnMtc2VydmljZSIsInJlbGVhc2VfYXNzZXNzbWVudCI6InBlbmRpbmctY29uZmlybWF0aW9uIiwicmVwbyI6IjxVU0VSX0xPQ0FMX0FQUERBVEE+XFxUZW1wXFxyci1xdWFsaXR5LXYwLjEwLjAtc21va2Utc3JjXFxncHQtNi4xLXNvbC1jYW5kaWRhdGUtcGVybWlzc2lvbi0wMVxcZml4dHVyZVxcd29ya3NwYWNlXFxvcmRlcnMtc2VydmljZSIsInJlcG9ydF9yb290IjoiPFVTRVJfTE9DQUxfQVBQREFUQT5cXFRlbXBcXHJyLXF1YWxpdHktdjAuMTAuMC1zbW9rZS1zcmNcXGdwdC02LjEtc29sLWNhbmRpZGF0ZS1wZXJtaXNzaW9uLTAxXFxmaXh0dXJlXFx3b3Jrc3BhY2VcXG9yZGVycy1zZXJ2aWNlXFxyZXZpZXctcmVwb3J0cyIsInJlcG9zaXRvcmllcyI6W3siaWQiOiJlbnRyeSIsImluc3RydWN0aW9ucyI6W10sIm5hbWUiOiJvcmRlcnMtc2VydmljZSIsInBhdGgiOiI8VVNFUl9MT0NBTF9BUFBEQVRBPlxcVGVtcFxccnItcXVhbGl0eS12MC4xMC4wLXNtb2tlLXNyY1xcZ3B0LTYuMS1zb2wtY2FuZGlkYXRlLXBlcm1pc3Npb24tMDFcXGZpeHR1cmVcXHdvcmtzcGFjZVxcb3JkZXJzLXNlcnZpY2UiLCJyb2xlIjoiZW50cnkiLCJzbmFwc2hvdCI6eyJjYXB0dXJlZF9hdCI6IjIwMjYtMTAtMjlUMTM6NDQ6MjguMTc0MjYzKzAwOjAwIiwiZ2l0X2RpciI6InJlcG9zL2VudHJ5L3NuYXBzaG90LmdpdCIsIm9iamVjdF9mb3JtYXQiOiJzaGExIiwicGFjayI6eyJjaGVja3N1bSI6ImFkOTIxNDMxMWFjNGEyYWYyNGMwYzEzOGE1YTVkODhlNzUxOWU2YzciLCJmaWxlIjoib2JqZWN0cy9wYWNrL3NuYXBzaG90LnBhY2siLCJpbmRleCI6Im9iamVjdHMvcGFjay9zbmFwc2hvdC5pZHgiLCJpbmRleF9zaGEyNTYiOiI1Y2U3ZjU3Y2NmYjg4NDIzNTI4NGYwYzhkYzhmMmFmNGIxN2FkZTI4ZTNhYjJhZmRkNzlkODA1MGNjMjdjMjYyIiwic2hhMjU2IjoiYjBiZmFkMTZkMDg1MWU4ZTEyZDdhZTkxODFhOGU0ZGU2NGQzZjI1YTQ0ZWVhY2NhMmY2MDYyODIyZDVkOWVlNCJ9LCJwb2xpY3kiOiJmcm96ZW4tc3RhcnQvdjEifSwic3RhdHVzIjoiY2FwdHVyZWQifV0sInJldmlld19jb21wbGV0ZSI6dHJ1ZSwic2NoZW1hIjoiUmVsZWFzZVJldmlld1JlcG9ydC92MyIsInNjb3BlIjoiZ3JvdXAiLCJzbmFwc2hvdF9wb2xpY3kiOiJmcm96ZW4tc3RhcnQvdjEiLCJzdGF0aWNfYm9keV9zaGEyNTYiOiJlZjQ2ZGEyOWVkMjFmNjc5NDE2MzY4OWI1NTMwYjk2YTI4NjI4MjUxZTc5OGMwYWI1MWM5YzIyZjBlZTUwMzcwIiwid29ya2luZ190cmVlX3VuY2hhbmdlZCI6dHJ1ZSwid29ya3NwYWNlX2luc3RydWN0aW9ucyI6W119 -->
