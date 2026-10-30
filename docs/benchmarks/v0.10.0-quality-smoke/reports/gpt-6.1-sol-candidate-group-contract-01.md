# 上版檢查: orders-service

## 審查結論

**存在已確認阻擋**｜靜態檢查完成。發現 1 項 P1 直接阻擋：orders.created 金額欄位改名使既有 billing-consumer 在持久化前失敗；另須人工核對事件 schema 相容性與部署順序。

問題數量：P0 0／P1 1（僅列直接阻擋上版的程式風險）。

阻擋 1 項｜合併衝突 0 項｜待確認 3 項｜檢查缺口 0 項。
判斷固定於產生時；人工填寫 Checklist 不會自動核准上版。

- 入口 Repo：`orders-service`
- 合併方向：`feature/review`（`b03bd6a9`） → `main`（`db4c7ecc`）

## 問題總攬

| 項目 | 等級 | 說明 |
| --- | --- | --- |
| [R-001](#R-001) | P1 | 事件欄位改名中斷既有帳務消費流程 |

## 問題詳情（工程師參考）

<a id="R-001"></a>

### R-001｜[P1] 事件欄位改名中斷既有帳務消費流程

**問題描述**

必要帳務消費端無法處理新版訂單事件，該事件金額不會進入 persist 呼叫；是否重送或累積佇列取決於未檢查的傳輸層。

影響範圍：新版 publish_event 產生並送交目前固定 billing-consumer 的 orders.created 事件；舊版含 amount 的事件可通過此欄位讀取，persist 與傳輸層內部行為未納入結論。

上版阻擋依據：契約登錄指定 billing-consumer 為 orders.created 的必要消費端；新產生的每個 payload 都缺少該端直接索引的 amount，送達後即在 persist 呼叫前失敗，必要帳務流程無法處理新事件，上版前須修正相容性。

**重現原因**

例如訂單金額為 100，新 producer 產生 {"total": 100}；既有帳務處理器接收後尋找 amount，尚未儲存金額便發生例外。

觸發條件：將新 publish_event(amount) 回傳的僅含 total 的 payload 交給固定版本 billing-consumer.handle。

依程式推導，未實際重現。

**預期結果**

orders.created 的 payload 應保留既有必要消費端及契約登錄要求的 amount，讓 handle 能讀取金額並呼叫 persist。

**實際結果**

最終 producer 僅回傳 total；consumer 仍執行 payload["amount"]，Python 在計算 persist 引數時會拋出 KeyError，無法進入 persist。

**證據**

app.py 第 2 行由回傳 amount 改為 total，沒有雙欄位或版本轉換；contract-registry.json 仍列 amount 與必要 billing-consumer，consumer.py 第 2 行直接索引 amount，且完整消費端檔案未提供 fallback 或例外處理。

`orders-service/app.py:1-2`（來源：`head`；固定版本：`b03bd6a9d023259af04a0cd07b3bfaebfa7f194a`）

內容 SHA-256：`4775d15349a0f1ff7f5de0ce2b5895fe5eaa0a7f9f80b0873a61926548ff9e48`

~~~text
1: def publish_event(amount):
2:     return {"total": amount}
~~~

`orders-service/app.py:1-2`（來源：`merge-preview`；固定版本：`76e6819fd25265228fb6f6e0a16391750ee13e58`）

內容 SHA-256：`4775d15349a0f1ff7f5de0ce2b5895fe5eaa0a7f9f80b0873a61926548ff9e48`

~~~text
1: def publish_event(amount):
2:     return {"total": amount}
~~~

`orders-service/app.py:1-2`（來源：`base`；固定版本：`db4c7ecc865d3b209bd17d60c2a74f2f473c6ba5`）

內容 SHA-256：`41f72aaf668c9eac136143cb7ee46e63515ff2fd7977484dfa4c8c95434c7f91`

~~~text
1: def publish_event(amount):
2:     return {"amount": amount}
~~~

`orders-service/contract-registry.json:1-1`（來源：`merge-preview`；固定版本：`76e6819fd25265228fb6f6e0a16391750ee13e58`）

內容 SHA-256：`f92d0d2a61e99d6d0ee4efcd45f5dcc7baabc0ce93fa13eee7759c0d203fdbf0`

設定鍵：field、orders.created、required_consumer

設定值已省略；僅保留可解析的鍵名及原始內容 SHA-256。

`billing-consumer/consumer.py:1-2`（來源：`supporting`；固定版本：`8901da22999cb8bc2ff272ab09a79833170d8437`）

內容 SHA-256：`fed1aa5996dc2194a7acaac062259f757418c51b9a6054e2404db207e10707ee`

~~~text
1: def handle(payload):
2:     return persist(payload["amount"])
~~~

Diff 起點：DIFF-001、DIFF-002；本次唯一程式差異將 publish_event 回傳欄位 amount 改為 total；最終合併內容仍未保留 amount，直接破壞固定必要消費端的欄位讀取契約。

**建議改善方式**

上版前恢復 amount 相容欄位；若需要 total，先安排可同時處理新舊欄位的消費端與契約過渡，再移除舊欄位。

保留原 amount 欄位並同步維護契約；若採改名遷移，先讓消費端兼容 amount 與 total，確認滾動部署、舊事件重送及回滾時的相容順序，再停用舊欄位。

## 範圍與限制

本次只審查指定 Repo 的已提交差異；其他 Repo 的擷取時 HEAD 是功能鏈佐證。
結論只適用本次固定版本；擷取後分支移動不改寫本次結果。

- 僅審查指定 main 與 feature/review 的固定已提交差異及乾淨合併預覽；supporting Repo 只採擷取時已提交 HEAD，未納入工作檔。
- KeyError 與 persist 未被呼叫為 Python 引數求值的靜態推導，未實際重現。
- 事件 provider、傳輸 wiring、ACK／retry、外部授權及 persist 內部實作未提供或未連線查驗；既有契約登錄與兩端 payload 足以辨識本次欄位不相容，不推定 broker 類型、資料永久遺失或現場資源不存在。
- 實際環境就緒、帳號權限與部署順序尚待人工核對；靜態覆蓋完整不等於核准上版。
- helper 證據檔遇 Windows 長路徑錯誤，以本次 runtime 啟動器加上檔案操作長路徑前綴續跑；沿用同一 request、固定快照與搜尋紀錄，未修改原始碼、技能或重新擷取來源。

**已執行檢查**

- 無紀錄。

**未執行檢查**

- 依使用者要求未執行專案測試、程式重現、部署、DB 查詢或現場連線；亦未存取 Git Server、GitLab 或 oracle。

### 新增部署依賴與人工核對

<a id="E-001"></a>

#### E-001｜orders.created payload schema：amount 改為 total

既有 orders.created 事件的金額欄位改名，需核對 producer、必要 consumer 與契約的共同部署相容性；沒有固定證據可辨識為 Kafka。（已辨識目標）
連線／用途：orders-service 至 billing-consumer 的事件介面。
**上版準備**：先修正 R-001；再由負責人核對事件契約、各版本部署與回滾順序、舊事件重送相容性及現場服務連線／權限。提供核對人、日期與實際驗證證據，不適用的網路或資源建立項須填理由。

**核對項**：[K-001](#K-001)、[K-002](#K-002)、[K-003](#K-003)

核對狀態限「待確認／已確認／未通過／不適用」。已確認需核對人、日期及開通／套用後驗證證據；
僅有申請工單仍為待確認，不適用需具體理由。必要項目待確認或未通過時，上版準備未完成。
本段核對紀錄可人工更新；固定正文與產生時判斷保留原始快照。

<!-- release-checklist:start -->
### 人工核對紀錄

#### [E-001｜orders.created payload schema：amount 改為 total](#E-001)

| 編號 | 核對事項 | 狀態 | 核對人 | 日期 | 工單／驗證證據 | 不適用理由 |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="K-001"></a>K-001 | 確認目標主機、連接埠、DNS／路由與防火牆已開通；本機依賴可記錄不適用理由。 | 待確認 | | | | |
| <a id="K-002"></a>K-002 | 確認 TLS／憑證／加密方式與目標環境相符；不適用時說明原因。 | 待確認 | | | | |
| <a id="K-003"></a>K-003 | 確認本次所需的外部資源、帳號與操作權限已備妥；沿用既有資源時，核對變更後的服務契約與部署相容性。 | 待確認 | | | | |

<!-- release-checklist:end -->

### 下一步

- 開發先修正 R-001，保留必要 amount 契約，或先完成消費端兼容新舊欄位的遷移。
- 修正後驗證 producer payload 能經既有 handle 進入 persist，並覆核新舊事件與回滾相容性；本次依要求未執行測試。
- 部署負責人在本報告可編輯 Checklist 填寫核對人、日期與實際驗證證據，確認 E-001 schema 與部署順序。

## 技術審查紀錄（工程師參考）

### 比較資訊

- Repo：`orders-service`
- 本機路徑：`<USER_LOCAL_APPDATA>\Temp\rr-quality-v0.10.0-smoke-src\gpt-6.1-sol-candidate-group-contract-01\fixture\workspace\orders-service`

| 角色 | 輸入分支 | 實際 ref | Commit（前 8 碼） |
| --- | --- | --- | --- |
| 基礎分支 | `main` | `refs/heads/main` | `db4c7ecc` |
| 比較分支 | `feature/review` | `refs/heads/feature/review` | `b03bd6a9` |

- 合併方向：`feature/review` → `main`
- 主要差異範圍（共同祖先 → 比較分支）：`db4c7ecc` → `b03bd6a9`
- 最終受審版本：`merge-preview`；`76e6819fd25265228fb6f6e0a16391750ee13e58`
- 快照擷取：2026-10-29T14:02:28.744121+00:00 → 2026-10-29T14:02:30.869729+00:00（UTC）。
- 適用版本：本報告僅適用上述固定 SHA 及本次擷取的相關 Repo；後續分支更新不改變本次結論。

<details>
<summary>固定版本、搜尋、功能鏈與覆蓋紀錄</summary>

- Repo：<USER_LOCAL_APPDATA>\Temp\rr-quality-v0.10.0-smoke-src\gpt-6.1-sol-candidate-group-contract-01\fixture\workspace\orders-service
- 報告目錄：<USER_LOCAL_APPDATA>\Temp\rr-quality-v0.10.0-smoke-src\gpt-6.1-sol-candidate-group-contract-01\fixture\workspace\orders-service\review-reports
- 基礎分支：main
- 比較分支：feature/review
- 基礎 SHA：db4c7ecc865d3b209bd17d60c2a74f2f473c6ba5
- 比較 SHA：b03bd6a9d023259af04a0cd07b3bfaebfa7f194a
- 共同起點：db4c7ecc865d3b209bd17d60c2a74f2f473c6ba5
- 最終來源：merge-preview
- 最終 SHA：76e6819fd25265228fb6f6e0a16391750ee13e58
- 擷取開始（UTC）：2026-10-29T14:02:28.744121+00:00
- 快照完成（UTC）：2026-10-29T14:02:30.869729+00:00
- 快照政策：frozen-start/v1
- 本機檔案與暫存區維持原狀：True

版本比對只包含已提交內容。靜態範圍完整不代表目標環境已驗證。

- Diff 起點 DIFF-001：app.py；db4c7ecc865d3b209bd17d60c2a74f2f473c6ba5 → b03bd6a9d023259af04a0cd07b3bfaebfa7f194a
- Diff 起點 DIFF-002：app.py；db4c7ecc865d3b209bd17d60c2a74f2f473c6ba5 → 76e6819fd25265228fb6f6e0a16391750ee13e58
### E-001 新增判定與證據

新增類型：target-change；入口 base 全 Repo 的 amount 搜尋命中 producer 實際回傳與契約登錄；orders.created 搜尋確認既有事件及必要 consumer，publish_event 搜尋定位唯一 producer；total 搜尋零命中。結合全部 4 個固定路徑與程式內容，這是既有事件 schema 變更，並非新增 Topic 或其他服務已使用即可沿用的權限證明。

Diff 起點：DIFF-001、DIFF-002；本次 producer 欄位改名引入 total schema 並移除必要 amount；需要既有事件介面兩端的契約部署核對。

`orders-service/app.py:1-2`（來源：`merge-preview`；固定版本：`76e6819fd25265228fb6f6e0a16391750ee13e58`）

內容 SHA-256：`4775d15349a0f1ff7f5de0ce2b5895fe5eaa0a7f9f80b0873a61926548ff9e48`

~~~text
1: def publish_event(amount):
2:     return {"total": amount}
~~~

`orders-service/contract-registry.json:1-1`（來源：`merge-preview`；固定版本：`76e6819fd25265228fb6f6e0a16391750ee13e58`）

內容 SHA-256：`f92d0d2a61e99d6d0ee4efcd45f5dcc7baabc0ce93fa13eee7759c0d203fdbf0`

設定鍵：field、orders.created、required_consumer

設定值已省略；僅保留可解析的鍵名及原始內容 SHA-256。

`billing-consumer/consumer.py:1-2`（來源：`supporting`；固定版本：`8901da22999cb8bc2ff272ab09a79833170d8437`）

內容 SHA-256：`fed1aa5996dc2194a7acaac062259f757418c51b9a6054e2404db207e10707ee`

- 基礎全 Repo 搜尋：命中 3；紀錄 69f70488ac88b8758e964918a7f4632dceef599194a9ee5d69e75a8b87c45fd2
- 基礎全 Repo 搜尋：命中 0；紀錄 5030ba25b018d4d05a9b99fb6bc7d83fabcc4a5c8cc5dc1cce604f4ab4e3f2ce
- 基礎全 Repo 搜尋：命中 2；紀錄 d950724c0766c5866c1a1fba2451346efacbe36512c1cc46e95c7ac12dfa25f7
- 基礎全 Repo 搜尋：命中 2；紀錄 c63ae9b737dcb743b7198ea3e3fa9ff6574fd22bb88f755ccdab93c7c334cd0e

### 關聯 Repo 與功能鏈

| Repo | 固定本機分支 | SHA | 狀態 |
| --- | --- | --- | --- |
| entry / orders-service | 入口指定分支 | 見入口版本／未取得 | captured |
| repo-001 / billing-consumer | main | 8901da22999cb8bc2ff272ab09a79833170d8437 | captured |

**repo-001：reviewed**；已閱讀固定 HEAD 的唯一檔案 consumer.py，結合契約登錄辨識必要帳務消費端；追查 amount 讀取、total fallback 缺少與 persist 呼叫前例外，足以判斷本次 schema 不相容。persist 與傳輸層內部未提供，未對其儲存結果、授權、ACK 或重送機制做出推定。

`billing-consumer/consumer.py:1-2`（來源：`supporting`；固定版本：`8901da22999cb8bc2ff272ab09a79833170d8437`）

內容 SHA-256：`fed1aa5996dc2194a7acaac062259f757418c51b9a6054e2404db207e10707ee`

**F-001｜entry → repo-001｜event**
reviewed：orders.created 的 producer publish_event 建立金額 payload；契約登錄將 billing-consumer 指為必要 consumer 且欄位仍為 amount。固定 handle 讀取 amount 後才呼叫 persist；新 producer 僅提供 total，會在呼叫前失敗，舊 amount 事件通過此讀取。消費端無 fallback 或局部例外處理；provider、外部授權與重送實作未驗證，無法推定資料最終遺失或佇列行為。
Diff 起點：DIFF-001、DIFF-002；本次唯一程式差異將 publish_event 回傳欄位 amount 改為 total；最終合併內容仍未保留 amount，直接破壞固定必要消費端的欄位讀取契約。

`orders-service/app.py:1-2`（來源：`merge-preview`；固定版本：`76e6819fd25265228fb6f6e0a16391750ee13e58`）

內容 SHA-256：`4775d15349a0f1ff7f5de0ce2b5895fe5eaa0a7f9f80b0873a61926548ff9e48`

~~~text
1: def publish_event(amount):
2:     return {"total": amount}
~~~

`orders-service/contract-registry.json:1-1`（來源：`merge-preview`；固定版本：`76e6819fd25265228fb6f6e0a16391750ee13e58`）

內容 SHA-256：`f92d0d2a61e99d6d0ee4efcd45f5dcc7baabc0ce93fa13eee7759c0d203fdbf0`

設定鍵：field、orders.created、required_consumer

設定值已省略；僅保留可解析的鍵名及原始內容 SHA-256。

`billing-consumer/consumer.py:1-2`（來源：`supporting`；固定版本：`8901da22999cb8bc2ff272ab09a79833170d8437`）

內容 SHA-256：`fed1aa5996dc2194a7acaac062259f757418c51b9a6054e2404db207e10707ee`

- 關聯搜尋：repo-001；版本 8901da22999cb8bc2ff272ab09a79833170d8437；命中 1；紀錄 2d6f3643b774779863a9a5779dfc506f5d0d3a7c073962213b161788cc68ed66
- 關聯搜尋：repo-001；版本 8901da22999cb8bc2ff272ab09a79833170d8437；命中 0；紀錄 cd64a9e5407442816a60cbf079a3edf5779b76cc73cad540ca60d4aaf634d5b2
- 關聯搜尋：repo-001；版本 8901da22999cb8bc2ff272ab09a79833170d8437；命中 1；紀錄 920c3c75412bc2e363dd2d6194ec930d0723993291c56b45fa076b49895c2301

### 靜態覆蓋

| 路徑 | 狀態 | 未完成原因 |
| --- | --- | --- |
| app.py | reviewed |  |

**依賴類型覆蓋**：

- config：reviewed；已比對唯一 app.py 差異並查閱入口固定全部 4 個檔案；無新增設定讀取、設定定義或部署 binding，契約登錄維持不變。
- db：reviewed；bootstrap.py 的 SELECT COUNT(*) FROM orders 與 base 完全相同；本次只改事件欄位，未新增 DB 物件、欄位、連線目標或操作權限；消費端 persist 為既有呼叫，未推定其 DB 實作。
- ftp：reviewed；入口固定全部 4 個檔案與唯一 consumer 已閱讀；本次 schema 改名未引入 FTP client、目錄、設定或部署定義。
- sftp：reviewed；入口固定全部 4 個檔案與唯一 consumer 已閱讀；本次 schema 改名未引入 SFTP client、金鑰、目錄或部署定義。
- redis：reviewed；入口固定全部 4 個檔案與唯一 consumer 已閱讀；本次未引入 Redis client、key prefix、命令、TTL 或資源定義。
- kafka：reviewed；沒有固定 client、adapter 或部署證據可識別 Kafka；orders.created 事件名稱不足以指定 provider，本次 schema 變更以 other 列 E-001。
- other：reviewed；已沿 producer、契約登錄及必要 consumer 核對，新增 total 並移除 amount 的 schema 相容核對列 E-001；既有 Topic 與 consumer 身分未改，未宣稱新增 broker 資源。

</details>

<!-- release-review-report:eyJiYXNlX2lucHV0IjoibWFpbiIsImJhc2VfcmVzb2x2ZWRfcmVmIjoicmVmcy9oZWFkcy9tYWluIiwiYmFzZV9zaGEiOiJkYjRjN2VjYzg2NWQzYjIwOWJkMTdkNjBjMmE3NGYyZjQ3M2M2YmE1IiwiYmxvY2tlcl9jb3VudCI6MSwiY2FwdHVyZV9jb21wbGV0ZWRfYXQiOiIyMDI2LTEwLTI5VDE0OjAyOjMwLjg2OTcyOSswMDowMCIsImNvbnRleHRfc2hhMjU2IjoiZjM4NjMzOWFlMDUxNTA1YTE5ZWZjZWU5MTQ4YmE2ZGRkZjNiMDNlYzc0NDEzMTM2YzJlY2MyNjRiNWE4M2Q2MyIsImNvdmVyYWdlX3BvbGljeSI6ImFuY2VzdG9yLW5vb3AvdjEiLCJkaWZmX2Jhc2UiOiJkYjRjN2VjYzg2NWQzYjIwOWJkMTdkNjBjMmE3NGYyZjQ3M2M2YmE1IiwiZGlmZl9yb290cyI6W3siYWZ0ZXIiOnsicmVmIjoiYjAzYmQ2YTlkMDIzMjU5YWYwNGEwY2QwN2IzYmZhZWJmYTdmMTk0YSIsInNvdXJjZSI6ImhlYWQifSwiYmVmb3JlIjp7InJlZiI6ImRiNGM3ZWNjODY1ZDNiMjA5YmQxN2Q2MGMyYTc0ZjJmNDczYzZiYTUiLCJzb3VyY2UiOiJtZXJnZS1iYXNlIn0sImlkIjoiRElGRi0wMDEiLCJvbGRfcGF0aCI6bnVsbCwicGF0aCI6ImFwcC5weSIsInN0YXR1cyI6Ik0ifSx7ImFmdGVyIjp7InJlZiI6Ijc2ZTY4MTlmZDI1MjY1MjI4ZmI2ZjZlMGExNjM5MTc1MGVlMTNlNTgiLCJzb3VyY2UiOiJtZXJnZS1wcmV2aWV3In0sImJlZm9yZSI6eyJyZWYiOiJkYjRjN2VjYzg2NWQzYjIwOWJkMTdkNjBjMmE3NGYyZjQ3M2M2YmE1Iiwic291cmNlIjoiYmFzZSJ9LCJpZCI6IkRJRkYtMDAyIiwib2xkX3BhdGgiOm51bGwsInBhdGgiOiJhcHAucHkiLCJzdGF0dXMiOiJNIn1dLCJlbnRyeV9yZXBvIjoiZW50cnkiLCJmaW5hbF9yZWYiOiI3NmU2ODE5ZmQyNTI2NTIyOGZiNmY2ZTBhMTYzOTE3NTBlZTEzZTU4IiwiZmluYWxfc291cmNlIjoibWVyZ2UtcHJldmlldyIsImdhcF9jb3VudCI6MCwiZ2VuZXJhdGVkX2F0IjoiMjAyNi0xMC0yOVQxNDowMjoyOC43NDQxMjErMDA6MDAiLCJoZWFkX2lucHV0IjoiZmVhdHVyZS9yZXZpZXciLCJoZWFkX3Jlc29sdmVkX3JlZiI6InJlZnMvaGVhZHMvZmVhdHVyZS9yZXZpZXciLCJoZWFkX3NoYSI6ImIwM2JkNmE5ZDAyMzI1OWFmMDRhMGNkMDdiM2JmYWViZmE3ZjE5NGEiLCJtZXJnZV9iYXNlIjoiZGI0YzdlY2M4NjVkM2IyMDliZDE3ZDYwYzJhNzRmMmY0NzNjNmJhNSIsIm1vZGUiOiJtZXJnZSIsInBlbmRpbmdfY291bnQiOjMsInByb2plY3QiOiJvcmRlcnMtc2VydmljZSIsInJlbGVhc2VfYXNzZXNzbWVudCI6ImJsb2NrZWQiLCJyZXBvIjoiPFVTRVJfTE9DQUxfQVBQREFUQT5cXFRlbXBcXHJyLXF1YWxpdHktdjAuMTAuMC1zbW9rZS1zcmNcXGdwdC02LjEtc29sLWNhbmRpZGF0ZS1ncm91cC1jb250cmFjdC0wMVxcZml4dHVyZVxcd29ya3NwYWNlXFxvcmRlcnMtc2VydmljZSIsInJlcG9ydF9yb290IjoiPFVTRVJfTE9DQUxfQVBQREFUQT5cXFRlbXBcXHJyLXF1YWxpdHktdjAuMTAuMC1zbW9rZS1zcmNcXGdwdC02LjEtc29sLWNhbmRpZGF0ZS1ncm91cC1jb250cmFjdC0wMVxcZml4dHVyZVxcd29ya3NwYWNlXFxvcmRlcnMtc2VydmljZVxccmV2aWV3LXJlcG9ydHMiLCJyZXBvc2l0b3JpZXMiOlt7ImlkIjoiZW50cnkiLCJpbnN0cnVjdGlvbnMiOltdLCJuYW1lIjoib3JkZXJzLXNlcnZpY2UiLCJwYXRoIjoiPFVTRVJfTE9DQUxfQVBQREFUQT5cXFRlbXBcXHJyLXF1YWxpdHktdjAuMTAuMC1zbW9rZS1zcmNcXGdwdC02LjEtc29sLWNhbmRpZGF0ZS1ncm91cC1jb250cmFjdC0wMVxcZml4dHVyZVxcd29ya3NwYWNlXFxvcmRlcnMtc2VydmljZSIsInJvbGUiOiJlbnRyeSIsInNuYXBzaG90Ijp7ImNhcHR1cmVkX2F0IjoiMjAyNi0xMC0yOVQxNDowMjozMC4xMTk2ODMrMDA6MDAiLCJnaXRfZGlyIjoicmVwb3MvZW50cnkvc25hcHNob3QuZ2l0Iiwib2JqZWN0X2Zvcm1hdCI6InNoYTEiLCJwYWNrIjp7ImNoZWNrc3VtIjoiNmZmNjAzMmU2MzJjNzA1ODQzODU5OGRkMzYxOTI1ZTg0NTJiZDdlYiIsImZpbGUiOiJvYmplY3RzL3BhY2svc25hcHNob3QucGFjayIsImluZGV4Ijoib2JqZWN0cy9wYWNrL3NuYXBzaG90LmlkeCIsImluZGV4X3NoYTI1NiI6IjllYzhmMWE1OGM0YWUyMzhhOGQ1ZWY5NjUxNWZkODQ0ZTM5MTQzZjUzNmJkYTFlZDEzMmMyYTVmZDk5YmViODAiLCJzaGEyNTYiOiIyM2U4YmQxYjFlMThjZDA5NmM5NmY3ODUxY2E3NzlmYmFmOGQxNTMwMTYwNGQ4YTBjNjY1NjY1NDUwOGIzYTM0In0sInBvbGljeSI6ImZyb3plbi1zdGFydC92MSJ9LCJzdGF0dXMiOiJjYXB0dXJlZCJ9LHsiYnJhbmNoIjoibWFpbiIsImJyYW5jaF9zb3VyY2UiOiJsb2NhbC1IRUFEIiwiaWQiOiJyZXBvLTAwMSIsImluc3RydWN0aW9ucyI6W10sIm5hbWUiOiJiaWxsaW5nLWNvbnN1bWVyIiwicGF0aCI6IjxVU0VSX0xPQ0FMX0FQUERBVEE+XFxUZW1wXFxyci1xdWFsaXR5LXYwLjEwLjAtc21va2Utc3JjXFxncHQtNi4xLXNvbC1jYW5kaWRhdGUtZ3JvdXAtY29udHJhY3QtMDFcXGZpeHR1cmVcXHdvcmtzcGFjZVxcYmlsbGluZy1jb25zdW1lciIsInJlZiI6Ijg5MDFkYTIyOTk5Y2I4YmMyZmYyNzJhYjA5YTc5ODMzMTcwZDg0MzciLCJyb2xlIjoic3VwcG9ydGluZyIsInNuYXBzaG90Ijp7ImNhcHR1cmVkX2F0IjoiMjAyNi0xMC0yOVQxNDowMjozMC41ODMzMjUrMDA6MDAiLCJnaXRfZGlyIjoicmVwb3MvcmVwby0wMDEvc25hcHNob3QuZ2l0Iiwib2JqZWN0X2Zvcm1hdCI6InNoYTEiLCJwYWNrIjp7ImNoZWNrc3VtIjoiZDVhZGJhMzFiMjI4N2NlZDM3YTU5ZTZlM2RmZmFiY2NjZTIxNWQ2OSIsImZpbGUiOiJvYmplY3RzL3BhY2svc25hcHNob3QucGFjayIsImluZGV4Ijoib2JqZWN0cy9wYWNrL3NuYXBzaG90LmlkeCIsImluZGV4X3NoYTI1NiI6IjU1NWFjZDIzZjdjZWQ2NDM3OTVjZDVjNWNiYTA1YjIxNTFmMjlkNjIzMGE0MjdlOTMzZTVjNWJjMDMyYTk5YjgiLCJzaGEyNTYiOiIzNzIwZTE0ZTJmOTM4NDU5MjdlMjY1MjRlZTcyMGViNzg4ZTdhZDU5NzhjY2EwODAzMGFiOTMyNDkxZTA4OTUwIn0sInBvbGljeSI6ImZyb3plbi1zdGFydC92MSJ9LCJzdGF0dXMiOiJjYXB0dXJlZCJ9XSwicmV2aWV3X2NvbXBsZXRlIjp0cnVlLCJzY2hlbWEiOiJSZWxlYXNlUmV2aWV3UmVwb3J0L3YzIiwic2NvcGUiOiJncm91cCIsInNuYXBzaG90X3BvbGljeSI6ImZyb3plbi1zdGFydC92MSIsInN0YXRpY19ib2R5X3NoYTI1NiI6ImEzNDhlMzdjNDQ1NDI4ZGI4NThmYjZjMDYxMjdlNzNlNWY3MWRhNmRjNDExMmRjNTQzZGU2YzgwYjQ0OGYzZjgiLCJ3b3JraW5nX3RyZWVfdW5jaGFuZ2VkIjp0cnVlLCJ3b3Jrc3BhY2VfaW5zdHJ1Y3Rpb25zIjpbXX0= -->
