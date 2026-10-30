# 上版檢查: orders-service

## 審查結論

**存在已確認阻擋**｜靜態檢查完成。本次僅修改事件 payload 欄位，amount 改為 total，與既有 orders.created 契約及必要 billing-consumer 不相容，確認 1 項 P1 上版阻擋。靜態審查已完成；事件 schema 與部署相容性仍待人工核對。

問題數量：P0 0／P1 1（僅列直接阻擋上版的程式風險）。

阻擋 1 項｜合併衝突 0 項｜待確認 3 項｜檢查缺口 0 項。
判斷固定於產生時；人工填寫 Checklist 不會自動核准上版。

- 入口 Repo：`orders-service`
- 合併方向：`feature/review`（`b03bd6a9`） → `main`（`db4c7ecc`）

## 問題總攬

| 項目 | 等級 | 說明 |
| --- | --- | --- |
| [R-001](#R-001) | P1 | 移除 amount 欄位使必要帳務消費端無法處理訂單事件 |

## 問題詳情（工程師參考）

<a id="R-001"></a>

### R-001｜[P1] 移除 amount 欄位使必要帳務消費端無法處理訂單事件

**問題描述**

新版本產生的訂單事件無法提供帳務所需金額，正常帳務處理無法依既有契約完成；影響本次變更後的事件，不涵蓋仍含 amount 的舊事件。

影響範圍：新 producer 產生的 payload 與擷取時 billing-consumer HEAD 之間的欄位契約。未驗證 broker 路由、重送或 persist 的內部行為；不宣稱實際已發生資料遺失。

上版阻擋依據：本次部署對所有新產生的訂單事件移除必要金額欄位，固定版本帳務消費端仍要求 amount，會中斷訂單事件的正常帳務處理；須先恢復相容性。

**重現原因**

訂單服務產生金額為 100 的事件時回傳 {"total": 100}，既有帳務服務依 orders.created 契約索取 amount，無法取得應入帳金額。

觸發條件：以新版本 publish_event(amount) 產生 orders.created payload，交付仍依固定契約讀取 amount 的 billing-consumer。

依程式推導，未實際重現。

**預期結果**

orders.created 持續提供既有必要欄位 amount，讓已部署的 billing-consumer 能讀取金額並交給 persist。

**實際結果**

最終版本只回傳 total，未提供 amount；契約仍要求 amount，消費端仍使用 payload["amount"]，缺少相容欄位或轉換處理。

**證據**

app.py 的唯一差異把回傳 dict 的鍵由 amount 改為 total。未變更的 contract-registry.json 明確連結 orders.created、必要 billing-consumer 與 amount；固定 consumer.py 直接以 payload["amount"] 取值，沒有 total fallback。若執行環境已提供既有 persist callable，求取該引數時會因缺鍵拋出 KeyError，無法進入 persist。

`orders-service/app.py:1-2`（來源：`base`；固定版本：`db4c7ecc865d3b209bd17d60c2a74f2f473c6ba5`）

內容 SHA-256：`41f72aaf668c9eac136143cb7ee46e63515ff2fd7977484dfa4c8c95434c7f91`

~~~text
1: def publish_event(amount):
2:     return {"amount": amount}
~~~

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

Diff 起點：DIFF-001、DIFF-002；本次 app.py 的實際變更直接移除原有 amount 鍵並改為 total；最終預覽仍有此差異，必要消費端與契約均未配合。

**建議改善方式**

上版前恢復 amount；如需導入 total，先保留 amount 並安排消費端相容遷移，核對所有必要消費端後才移除舊欄位。

維持輸出 amount 的向後相容性；遷移時可暫時同時輸出 amount、total，先更新 consumer 接受新舊欄位，再依明確版本與部署順序移除舊契約。

## 範圍與限制

本次只審查指定 Repo 的已提交差異；其他 Repo 的擷取時 HEAD 是功能鏈佐證。
結論只適用本次固定版本；擷取後分支移動不改寫本次結果。

- 本次僅檢查 main 與 feature/review 的已提交固定內容及擷取時 billing-consumer HEAD；不包含工作檔或其他未提供版本。
- 固定契約已明確指定必要消費端與 amount，兩端程式足以確認欄位不相容；傳輸 provider、實際路由、broker ACK／重送、現場網路與權限未驗證，不推定為 Kafka 或已完成環境準備。
- consumer.py 引用的既有 persist 實作／綁定未在固定 Repo 提供；未驗證持久化結果，KeyError 的具體推導以 runtime 已提供該 callable 為條件。必要欄位缺失與契約違反不依賴其內部實作。
- 靜態審查完整與 Checklist 填寫均不代表核准上版；本次已確認 P1 應先修正。

**已執行檢查**

- 無紀錄。

**未執行檢查**

- 依使用者要求，未執行專案測試、重現程式、部署、實際環境或 DB 查詢。
- 未存取 Git Server、呼叫 GitLab 或查閱 oracle；本機分支 capture 的 fetch 次數為 0。

### 新增部署依賴與人工核對

<a id="E-001"></a>

#### E-001｜orders.created payload schema：amount → total

既有 orders.created 事件首次改用 total 欄位並移除 amount。Topic／事件識別原已存在，提醒針對新增 payload 欄位與舊消費契約的相容性，不表示新增 broker 或 Topic。（已辨識目標）
連線／用途：orders-service → billing-consumer；事件 provider 與實際傳輸端點未在固定來源提供。
**上版準備**：先修正 R-001，再核對 orders.created producer／必要 consumer 新舊 payload 相容性、部署與回復順序及舊訊息處理。provider 與實際端點待環境核對，依實際傳輸機制確認必要網路與服務身分權限；不預設 Kafka。

**核對項**：[K-001](#K-001)、[K-002](#K-002)、[K-003](#K-003)

核對狀態限「待確認／已確認／未通過／不適用」。已確認需核對人、日期及開通／套用後驗證證據；
僅有申請工單仍為待確認，不適用需具體理由。必要項目待確認或未通過時，上版準備未完成。
本段核對紀錄可人工更新；固定正文與產生時判斷保留原始快照。

<!-- release-checklist:start -->
### 人工核對紀錄

#### [E-001｜orders.created payload schema：amount → total](#E-001)

| 編號 | 核對事項 | 狀態 | 核對人 | 日期 | 工單／驗證證據 | 不適用理由 |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="K-001"></a>K-001 | 確認目標「orders.created payload schema：amount → total」及連線／服務身分「orders-service → billing-consumer；事件 provider 與實際傳輸端點未在固定來源提供」的主機、連接埠、DNS／路由與防火牆已開通；未知或環境提供值須核對實際設定並記錄驗證證據。 | 待確認 | | | | |
| <a id="K-002"></a>K-002 | 確認目標「orders.created payload schema：amount → total」及連線／服務身分「orders-service → billing-consumer；事件 provider 與實際傳輸端點未在固定來源提供」的 TLS／憑證／加密設定符合目標環境；不適用時記錄理由。 | 待確認 | | | | |
| <a id="K-003"></a>K-003 | 確認本次所需的外部資源、帳號與操作權限已備妥；沿用既有資源時，核對變更後的服務契約與部署相容性。 | 待確認 | | | | |

<!-- release-checklist:end -->

### 下一步

- 開發先修正 R-001 的事件向後相容性，變更提交後重新審查固定版本。
- 部署負責人於同一報告 Checklist 填寫核對人、日期與實際驗證證據，核對事件 schema、必要消費端及部署／回復順序。

## 技術審查紀錄（工程師參考）

### 比較資訊

- Repo：`orders-service`
- 本機路徑：`<USER_LOCAL_APPDATA>\Temp\q-rr-group-contract-1030\gpt-6.1-sol-candidate-group-contract-01\fixture\workspace\orders-service`

| 角色 | 輸入分支 | 實際 ref | Commit（前 8 碼） |
| --- | --- | --- | --- |
| 基礎分支 | `main` | `refs/heads/main` | `db4c7ecc` |
| 比較分支 | `feature/review` | `refs/heads/feature/review` | `b03bd6a9` |

- 合併方向：`feature/review` → `main`
- 主要差異範圍（共同祖先 → 比較分支）：`db4c7ecc` → `b03bd6a9`
- 最終受審版本：`merge-preview`；`76e6819fd25265228fb6f6e0a16391750ee13e58`
- 快照擷取：2026-10-29T16:59:12.042178+00:00 → 2026-10-29T16:59:13.953215+00:00（UTC）。
- 適用版本：本報告僅適用上述固定 SHA 及本次擷取的相關 Repo；後續分支更新不改變本次結論。

<details>
<summary>固定版本、搜尋、功能鏈與覆蓋紀錄</summary>

- Repo：<USER_LOCAL_APPDATA>\Temp\q-rr-group-contract-1030\gpt-6.1-sol-candidate-group-contract-01\fixture\workspace\orders-service
- 報告目錄：<USER_LOCAL_APPDATA>\Temp\q-rr-group-contract-1030\gpt-6.1-sol-candidate-group-contract-01\fixture\workspace\orders-service\review-reports
- 基礎分支：main
- 比較分支：feature/review
- 基礎 SHA：db4c7ecc865d3b209bd17d60c2a74f2f473c6ba5
- 比較 SHA：b03bd6a9d023259af04a0cd07b3bfaebfa7f194a
- 共同起點：db4c7ecc865d3b209bd17d60c2a74f2f473c6ba5
- 最終來源：merge-preview
- 最終 SHA：76e6819fd25265228fb6f6e0a16391750ee13e58
- 擷取開始（UTC）：2026-10-29T16:59:12.042178+00:00
- 快照完成（UTC）：2026-10-29T16:59:13.953215+00:00
- 快照政策：frozen-start/v1
- 本機檔案與暫存區維持原狀：True

版本比對只包含已提交內容。靜態範圍完整不代表目標環境已驗證。

- Diff 起點 DIFF-001：app.py；db4c7ecc865d3b209bd17d60c2a74f2f473c6ba5 → b03bd6a9d023259af04a0cd07b3bfaebfa7f194a
- Diff 起點 DIFF-002：app.py；db4c7ecc865d3b209bd17d60c2a74f2f473c6ba5 → 76e6819fd25265228fb6f6e0a16391750ee13e58
### E-001 新增判定與證據

新增類型：first-use；入口 base 全 Repo 的 orders.created 搜尋命中 README 與契約定義，amount 搜尋同時命中正式 producer 與契約；total 搜尋零命中。結合已讀 base app.py、契約與完整路徑清單，可確認既有正式 producer 使用 amount，本次才改用 total，並非等價搬移或僅文件修改。

Diff 起點：DIFF-001、DIFF-002；唯一程式差異變更既有事件的金額 schema，因此須新增 payload 契約相容性的上版核對。

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

- 基礎全 Repo 搜尋：命中 2；紀錄 38fe1694b1bb388791ba78e326cf5eba1f4bba61c3339e2547c20fcc0b7dc0e3
- 基礎全 Repo 搜尋：命中 3；紀錄 fd53c51a3cd165e4dd835e40ee980013e9efc0ffa7f441f80af420e2b04b6ff5
- 基礎全 Repo 搜尋：命中 0；紀錄 74b4feda97f2f7fd688c4da33f24b81f41327cb80399291ca9b8414254bc9f5a

### 關聯 Repo 與功能鏈

| Repo | 固定本機分支 | SHA | 狀態 |
| --- | --- | --- | --- |
| entry / orders-service | 入口指定分支 | 見入口版本／未取得 | captured |
| repo-001 / billing-consumer | main | 8901da22999cb8bc2ff272ab09a79833170d8437 | captured |

**repo-001：reviewed**；已讀取完整 consumer.py，並全 Repo 搜尋 amount、total、persist，核對完整檔案清單只有此檔。必要消費端直接取 amount，沒有新欄位 fallback 或轉換；欄位契約不相容已可判定。persist 內部與傳輸平台不在固定來源，列限制，不推定其資料儲存目標或實際運行狀態。

`billing-consumer/consumer.py:1-2`（來源：`supporting`；固定版本：`8901da22999cb8bc2ff272ab09a79833170d8437`）

內容 SHA-256：`fed1aa5996dc2194a7acaac062259f757418c51b9a6054e2404db207e10707ee`

**F-001｜entry → repo-001｜event**
reviewed：orders-service 的 publish_event 產生 payload；固定 contract-registry.json 指定 orders.created 的必要消費端為 billing-consumer 且欄位為 amount。consumer.handle 直接取 amount 並交付 persist，沒有 fallback、轉換、授權分支或本地錯誤／重送處理。本次 total 變更使兩端契約不相容，結果為無法提供必要入帳金額（R-001）。
Diff 起點：DIFF-001、DIFF-002；入口本次刪除 amount 欄位，直接影響必要 consumer 對該欄位的取值與後續持久化呼叫。

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

- 關聯搜尋：repo-001；版本 8901da22999cb8bc2ff272ab09a79833170d8437；命中 1；紀錄 ce2efa68a559a8777509bd78745659309ceda103c257cc000603c5797102de64
- 關聯搜尋：repo-001；版本 8901da22999cb8bc2ff272ab09a79833170d8437；命中 0；紀錄 000ee3c3914885ed379cc993389e084bdecae8414dd9954016fa7279436891fe
- 關聯搜尋：repo-001；版本 8901da22999cb8bc2ff272ab09a79833170d8437；命中 1；紀錄 c37f2171a2ad2ca3b9abd9ed079edfdb097514f48996a5a2085fcb042be33cd6

### 靜態覆蓋

| 路徑 | 狀態 | 未完成原因 |
| --- | --- | --- |
| app.py | reviewed |  |

**依賴類型覆蓋**：

- config：reviewed；已讀入口完整清單、app.py、bootstrap.py 與契約定義。沒有設定載入、環境變數或部署 binding 的新增；既有契約檔未變更，事件 schema 提醒列 E-001。
- db：reviewed；base 與最終 bootstrap.py 同樣執行既有 orders 計數查詢；本次只改 payload 鍵，未新增 DB 目標、物件、欄位或操作。consumer 的既有 persist 實作未提供，不能據此推定新增 DB。
- ftp：reviewed；已核對入口 base／最終完整檔案清單與程式、契約及 supporting 完整 consumer；唯一變更為事件欄位，無此類 client、設定、資源定義、目標或新增操作證據。
- sftp：reviewed；已核對入口 base／最終完整檔案清單與程式、契約及 supporting 完整 consumer；唯一變更為事件欄位，無此類 client、設定、資源定義、目標或新增操作證據。
- redis：reviewed；已核對入口 base／最終完整檔案清單與程式、契約及 supporting 完整 consumer；唯一變更為事件欄位，無此類 client、設定、資源定義、目標或新增操作證據。
- kafka：reviewed；固定來源只可辨識 orders.created 事件與 payload 契約，沒有 Kafka client、adapter 或部署定義；不將抽象事件識別推定為 Kafka。此次 schema 變更以 other 列 E-001。
- other：reviewed；已對帳 base 全 Repo 搜尋、正式 producer、契約及必要 consumer：既有事件本次首次輸出 total 並移除 amount，列 E-001；無其他新增部署資源證據。

</details>

<!-- release-review-report:eyJiYXNlX2lucHV0IjoibWFpbiIsImJhc2VfcmVzb2x2ZWRfcmVmIjoicmVmcy9oZWFkcy9tYWluIiwiYmFzZV9zaGEiOiJkYjRjN2VjYzg2NWQzYjIwOWJkMTdkNjBjMmE3NGYyZjQ3M2M2YmE1IiwiYmxvY2tlcl9jb3VudCI6MSwiY2FwdHVyZV9jb21wbGV0ZWRfYXQiOiIyMDI2LTEwLTI5VDE2OjU5OjEzLjk1MzIxNSswMDowMCIsImNvbnRleHRfc2hhMjU2IjoiMjljZjI1MGU4MjkyZjI3ZWFmNWVkNDRjZWQzMTk4OGQ2ZGZlMmViOTllNTA2MTFkNjgzZTlmMjU3ZmFhZWMxOSIsImNvdmVyYWdlX3BvbGljeSI6ImFuY2VzdG9yLW5vb3AvdjEiLCJkaWZmX2Jhc2UiOiJkYjRjN2VjYzg2NWQzYjIwOWJkMTdkNjBjMmE3NGYyZjQ3M2M2YmE1IiwiZGlmZl9yb290cyI6W3siYWZ0ZXIiOnsicmVmIjoiYjAzYmQ2YTlkMDIzMjU5YWYwNGEwY2QwN2IzYmZhZWJmYTdmMTk0YSIsInNvdXJjZSI6ImhlYWQifSwiYmVmb3JlIjp7InJlZiI6ImRiNGM3ZWNjODY1ZDNiMjA5YmQxN2Q2MGMyYTc0ZjJmNDczYzZiYTUiLCJzb3VyY2UiOiJtZXJnZS1iYXNlIn0sImlkIjoiRElGRi0wMDEiLCJvbGRfcGF0aCI6bnVsbCwicGF0aCI6ImFwcC5weSIsInN0YXR1cyI6Ik0ifSx7ImFmdGVyIjp7InJlZiI6Ijc2ZTY4MTlmZDI1MjY1MjI4ZmI2ZjZlMGExNjM5MTc1MGVlMTNlNTgiLCJzb3VyY2UiOiJtZXJnZS1wcmV2aWV3In0sImJlZm9yZSI6eyJyZWYiOiJkYjRjN2VjYzg2NWQzYjIwOWJkMTdkNjBjMmE3NGYyZjQ3M2M2YmE1Iiwic291cmNlIjoiYmFzZSJ9LCJpZCI6IkRJRkYtMDAyIiwib2xkX3BhdGgiOm51bGwsInBhdGgiOiJhcHAucHkiLCJzdGF0dXMiOiJNIn1dLCJlbnRyeV9yZXBvIjoiZW50cnkiLCJmaW5hbF9yZWYiOiI3NmU2ODE5ZmQyNTI2NTIyOGZiNmY2ZTBhMTYzOTE3NTBlZTEzZTU4IiwiZmluYWxfc291cmNlIjoibWVyZ2UtcHJldmlldyIsImdhcF9jb3VudCI6MCwiZ2VuZXJhdGVkX2F0IjoiMjAyNi0xMC0yOVQxNjo1OToxMi4wNDIxNzgrMDA6MDAiLCJoZWFkX2lucHV0IjoiZmVhdHVyZS9yZXZpZXciLCJoZWFkX3Jlc29sdmVkX3JlZiI6InJlZnMvaGVhZHMvZmVhdHVyZS9yZXZpZXciLCJoZWFkX3NoYSI6ImIwM2JkNmE5ZDAyMzI1OWFmMDRhMGNkMDdiM2JmYWViZmE3ZjE5NGEiLCJtZXJnZV9iYXNlIjoiZGI0YzdlY2M4NjVkM2IyMDliZDE3ZDYwYzJhNzRmMmY0NzNjNmJhNSIsIm1vZGUiOiJtZXJnZSIsInBlbmRpbmdfY291bnQiOjMsInByb2plY3QiOiJvcmRlcnMtc2VydmljZSIsInJlbGVhc2VfYXNzZXNzbWVudCI6ImJsb2NrZWQiLCJyZXBvIjoiPFVTRVJfTE9DQUxfQVBQREFUQT5cXFRlbXBcXHEtcnItZ3JvdXAtY29udHJhY3QtMTAzMFxcZ3B0LTYuMS1zb2wtY2FuZGlkYXRlLWdyb3VwLWNvbnRyYWN0LTAxXFxmaXh0dXJlXFx3b3Jrc3BhY2VcXG9yZGVycy1zZXJ2aWNlIiwicmVwb3J0X3Jvb3QiOiI8VVNFUl9MT0NBTF9BUFBEQVRBPlxcVGVtcFxccS1yci1ncm91cC1jb250cmFjdC0xMDMwXFxncHQtNi4xLXNvbC1jYW5kaWRhdGUtZ3JvdXAtY29udHJhY3QtMDFcXGZpeHR1cmVcXHdvcmtzcGFjZVxcb3JkZXJzLXNlcnZpY2VcXHJldmlldy1yZXBvcnRzIiwicmVwb3NpdG9yaWVzIjpbeyJpZCI6ImVudHJ5IiwiaW5zdHJ1Y3Rpb25zIjpbXSwibmFtZSI6Im9yZGVycy1zZXJ2aWNlIiwicGF0aCI6IjxVU0VSX0xPQ0FMX0FQUERBVEE+XFxUZW1wXFxxLXJyLWdyb3VwLWNvbnRyYWN0LTEwMzBcXGdwdC02LjEtc29sLWNhbmRpZGF0ZS1ncm91cC1jb250cmFjdC0wMVxcZml4dHVyZVxcd29ya3NwYWNlXFxvcmRlcnMtc2VydmljZSIsInJvbGUiOiJlbnRyeSIsInNuYXBzaG90Ijp7ImNhcHR1cmVkX2F0IjoiMjAyNi0xMC0yOVQxNjo1OToxMy4yNzc1ODUrMDA6MDAiLCJnaXRfZGlyIjoicmVwb3MvZW50cnkvc25hcHNob3QuZ2l0Iiwib2JqZWN0X2Zvcm1hdCI6InNoYTEiLCJwYWNrIjp7ImNoZWNrc3VtIjoiNmZmNjAzMmU2MzJjNzA1ODQzODU5OGRkMzYxOTI1ZTg0NTJiZDdlYiIsImZpbGUiOiJvYmplY3RzL3BhY2svc25hcHNob3QucGFjayIsImluZGV4Ijoib2JqZWN0cy9wYWNrL3NuYXBzaG90LmlkeCIsImluZGV4X3NoYTI1NiI6IjllYzhmMWE1OGM0YWUyMzhhOGQ1ZWY5NjUxNWZkODQ0ZTM5MTQzZjUzNmJkYTFlZDEzMmMyYTVmZDk5YmViODAiLCJzaGEyNTYiOiIyM2U4YmQxYjFlMThjZDA5NmM5NmY3ODUxY2E3NzlmYmFmOGQxNTMwMTYwNGQ4YTBjNjY1NjY1NDUwOGIzYTM0In0sInBvbGljeSI6ImZyb3plbi1zdGFydC92MSJ9LCJzdGF0dXMiOiJjYXB0dXJlZCJ9LHsiYnJhbmNoIjoibWFpbiIsImJyYW5jaF9zb3VyY2UiOiJsb2NhbC1IRUFEIiwiaWQiOiJyZXBvLTAwMSIsImluc3RydWN0aW9ucyI6W10sIm5hbWUiOiJiaWxsaW5nLWNvbnN1bWVyIiwicGF0aCI6IjxVU0VSX0xPQ0FMX0FQUERBVEE+XFxUZW1wXFxxLXJyLWdyb3VwLWNvbnRyYWN0LTEwMzBcXGdwdC02LjEtc29sLWNhbmRpZGF0ZS1ncm91cC1jb250cmFjdC0wMVxcZml4dHVyZVxcd29ya3NwYWNlXFxiaWxsaW5nLWNvbnN1bWVyIiwicmVmIjoiODkwMWRhMjI5OTljYjhiYzJmZjI3MmFiMDlhNzk4MzMxNzBkODQzNyIsInJvbGUiOiJzdXBwb3J0aW5nIiwic25hcHNob3QiOnsiY2FwdHVyZWRfYXQiOiIyMDI2LTEwLTI5VDE2OjU5OjEzLjY3NzI3OCswMDowMCIsImdpdF9kaXIiOiJyZXBvcy9yZXBvLTAwMS9zbmFwc2hvdC5naXQiLCJvYmplY3RfZm9ybWF0Ijoic2hhMSIsInBhY2siOnsiY2hlY2tzdW0iOiJkNWFkYmEzMWIyMjg3Y2VkMzdhNTllNmUzZGZmYWJjY2NlMjE1ZDY5IiwiZmlsZSI6Im9iamVjdHMvcGFjay9zbmFwc2hvdC5wYWNrIiwiaW5kZXgiOiJvYmplY3RzL3BhY2svc25hcHNob3QuaWR4IiwiaW5kZXhfc2hhMjU2IjoiNTU1YWNkMjNmN2NlZDY0Mzc5NWNkNWM1Y2JhMDViMjE1MWYyOWQ2MjMwYTQyN2U5MzNlNWM1YmMwMzJhOTliOCIsInNoYTI1NiI6IjM3MjBlMTRlMmY5Mzg0NTkyN2UyNjUyNGVlNzIwZWI3ODhlN2FkNTk3OGNjYTA4MDMwYWI5MzI0OTFlMDg5NTAifSwicG9saWN5IjoiZnJvemVuLXN0YXJ0L3YxIn0sInN0YXR1cyI6ImNhcHR1cmVkIn1dLCJyZXZpZXdfY29tcGxldGUiOnRydWUsInNjaGVtYSI6IlJlbGVhc2VSZXZpZXdSZXBvcnQvdjMiLCJzY29wZSI6Imdyb3VwIiwic25hcHNob3RfcG9saWN5IjoiZnJvemVuLXN0YXJ0L3YxIiwic3RhdGljX2JvZHlfc2hhMjU2IjoiNmIxZjA5OWQ1MGU2OTQ2NzI1NDA1M2NkOTE4MmQ2MGQwNzlmZDE3OTM2MmQwZmM1NzhiYzIwZmEyMmY5MGQxYSIsIndvcmtpbmdfdHJlZV91bmNoYW5nZWQiOnRydWUsIndvcmtzcGFjZV9pbnN0cnVjdGlvbnMiOltdfQ== -->
