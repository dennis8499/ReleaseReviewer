# 上版檢查: orders-service

## 審查結論

**存在已確認阻擋**｜靜態檢查完成。發現 1 項 P0 上版阻擋：正常啟動會先無條件刪除 orders 全部資料；應先移除破壞性 SQL。另列既有 orders 新增 DELETE 操作的部署核對，實際環境未驗證。

問題數量：P0 1／P1 0（僅列直接阻擋上版的程式風險）。

阻擋 1 項｜合併衝突 0 項｜待確認 5 項｜檢查缺口 0 項。
判斷固定於產生時；人工填寫 Checklist 不會自動核准上版。

- 入口 Repo：`orders-service`
- 合併方向：`feature/review`（`78a5cf10`） → `main`（`391609f9`）

## 問題總攬

| 項目 | 等級 | 說明 |
| --- | --- | --- |
| [R-001](#R-001) | P0 | 正常啟動無條件清空訂單資料 |

## 問題詳情（工程師參考）

<a id="R-001"></a>

### R-001｜[P0] 正常啟動無條件清空訂單資料

**問題描述**

該 db 連線解析到的 orders 表全部訂單都可能被刪除，沒有訂單、租戶或時間條件限制；重啟服務會再次觸發。

影響範圍：影響傳入 db 所解析到的整張 orders 表；實際資料庫、交易提交模式、權限及現場資料未查證。這些影響持久化與現場後果，不改變程式新增全表刪除的事實。

上版阻擋依據：README 指定 bootstrap.startup 為訂單啟動入口；本次新增的無條件 DELETE 在每次啟動先於原有計數查詢執行。正式連線允許刪除並提交時會清除整張 orders 表，屬重大資料毀損，必須阻止本次上版。

**重現原因**

部署新版並啟動訂單服務，啟動函式取得含正式訂單的 db 連線後，先將整張 orders 表刪空；刪除提交後歷史資料遺失。

觸發條件：以連到已有訂單資料的資料庫物件呼叫 bootstrap.startup(db)；DELETE 可成功執行，且連線自動提交或呼叫端後續提交交易。

依程式推導，未實際重現。

**預期結果**

服務啟動只讀取既有訂單數量，保留所有歷史訂單資料。

**實際結果**

startup 先執行 DELETE FROM orders，再執行 SELECT COUNT(*) FROM orders；成功刪除後，同一交易中計數為零，提交後刪除持久化。固定程式未提供清除開關、條件或復原流程。

**證據**

bootstrap.py 第 2 行是本次新增 SQL，沒有 WHERE、環境判斷或明確選用條件；第 3 行沿用原有計數。README 第 1 行將 startup 指定為啟動入口，最終合併預覽仍保留此刪除。

`orders-service/bootstrap.py:1-3`（來源：`merge-preview`；固定版本：`c5e96fb4fedba050393b074d8d009d05a248225f`）

內容 SHA-256：`6a612ca3c8538f8b45e499c33b78ae969fb7f337678c8da606294dee1d6ca4bc`

~~~text
1: def startup(db):
2:     db.execute("DELETE FROM orders")
3:     return db.execute("SELECT COUNT(*) FROM orders")
~~~

`orders-service/bootstrap.py:1-3`（來源：`head`；固定版本：`78a5cf10f28db4ae2dd7105b6ee247c726b914f9`）

內容 SHA-256：`6a612ca3c8538f8b45e499c33b78ae969fb7f337678c8da606294dee1d6ca4bc`

~~~text
1: def startup(db):
2:     db.execute("DELETE FROM orders")
3:     return db.execute("SELECT COUNT(*) FROM orders")
~~~

`orders-service/bootstrap.py:1-2`（來源：`base`；固定版本：`391609f9048e30ec6a161da3b3c550f5a67d3ca4`）

內容 SHA-256：`da79078e83745dfd6dfdec123f320c51967e5e2c44250739fdcb126d59bff9a8`

~~~text
1: def startup(db):
2:     return db.execute("SELECT COUNT(*) FROM orders")
~~~

`orders-service/README.md:1-1`（來源：`merge-preview`；固定版本：`c5e96fb4fedba050393b074d8d009d05a248225f`）

內容 SHA-256：`1a4c4ff93c30ad17132eda704f9c814eae62421e4f315b979117742ecb878246`

~~~text
1: Order startup uses bootstrap.startup; requests use app.checkout.
~~~

Diff 起點：DIFF-001、DIFF-002；兩個 Diff 均在既有唯讀 startup 中新增無條件 DELETE；原 base 只有 SELECT COUNT(*)，全表刪除因本次新增行而發生。

**建議改善方式**

上版前移除啟動路徑中的清表操作，讓啟動維持唯讀。若業務另需清除資料，使用獨立且明確授權的維護流程。

刪除 startup 中的 DELETE，保留原有 SELECT。修正後由開發者在隔離資料庫預置多筆訂單，核對首次啟動及重複啟動都保留資料與正確計數；本次未執行該驗證。

## 範圍與限制

本次只審查指定 Repo 的已提交差異；其他 Repo 的擷取時 HEAD 是功能鏈佐證。
結論只適用本次固定版本；擷取後分支移動不改寫本次結果。

- 本次僅審查 main 與 feature/review 的本機已提交固定快照及乾淨合併預覽，沒有執行應用程式。
- 已辨識 startup 與 orders 邏輯目標；實際資料庫主機、schema 解析、服務帳號權限、自動提交或呼叫端提交模式、現場資料與備份復原能力均未連線查證。SQL 執行及資料持久化後果依程式推導，未實際重現。
- 固定 Repo 僅三個文字檔，無其他部署 manifest、DB adapter 或跨 Repo 契約；README 已明示 startup 是啟動入口。未推定特定 DB provider，未聲稱資料表或權限尚未建立。

**已執行檢查**

- 無紀錄。

**未執行檢查**

- 依使用者要求，未執行測試、部署、DB 查詢、Git Server 存取或 GitLab 呼叫；未檢視任何 oracle。

### 新增部署依賴與人工核對

<a id="D-001"></a>

#### D-001｜orders（新增 DELETE 操作）

既有邏輯 orders 表由 COUNT 查詢新增為啟動時全表 DELETE；不是新表。已辨識 SQL 與傳入連線介面，實際 DB／schema 與交易模式需現場核對。此提醒不表示應開放不安全的刪除權限。（已辨識目標）
連線／用途：bootstrap.startup 的 db 參數。
**上版準備**：先依 R-001 移除啟動清表 SQL，修正後重新審查本項是否仍適用，勿為通過啟動而授予全表刪除權限。核對 db 的正式目標、服務帳號最小權限、交易模式與備份復原；若操作已移除，在 Checklist 記錄不適用理由與修正版本。

**核對項**：[K-001](#K-001)、[K-002](#K-002)、[K-003](#K-003)、[K-004](#K-004)、[K-005](#K-005)

核對狀態限「待確認／已確認／未通過／不適用」。已確認需核對人、日期及開通／套用後驗證證據；
僅有申請工單仍為待確認，不適用需具體理由。必要項目待確認或未通過時，上版準備未完成。
本段核對紀錄可人工更新；固定正文與產生時判斷保留原始快照。

<!-- release-checklist:start -->
### 人工核對紀錄

#### [D-001｜orders（新增 DELETE 操作）](#D-001)

| 編號 | 核對事項 | 狀態 | 核對人 | 日期 | 工單／驗證證據 | 不適用理由 |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="K-001"></a>K-001 | 確認目標主機、連接埠、DNS／路由與防火牆已開通；本機依賴可記錄不適用理由。 | 待確認 | | | | |
| <a id="K-002"></a>K-002 | 確認 TLS／憑證／加密方式與目標環境相符；不適用時說明原因。 | 待確認 | | | | |
| <a id="K-003"></a>K-003 | 確認目標 DB／schema 與所需資料表、欄位、View 或預存程序已建立或套用。 | 待確認 | | | | |
| <a id="K-004"></a>K-004 | 確認 migration／DDL 執行順序、鎖定與資料回填，以及新舊程式版本相容性。 | 待確認 | | | | |
| <a id="K-005"></a>K-005 | 確認實際服務帳號具備本次操作所需 DB／物件權限；其他服務可用不代表本服務已授權。 | 待確認 | | | | |

<!-- release-checklist:end -->

### 下一步

- 上版前修正 R-001，移除啟動時無條件清表，並對修正提交重新審查。
- 修正後由開發者在隔離資料庫驗證啟動與重啟均保留既有訂單；依本次要求未執行測試。
- 維運依同一報告 Checklist 記錄目標 DB、權限及資料保護核對；若 DELETE 已移除，附修正版本與不適用理由。

## 技術審查紀錄（工程師參考）

### 比較資訊

- Repo：`orders-service`
- 本機路徑：`<USER_LOCAL_APPDATA>\Temp\rr-quality-matrix-smoke-src-858025dc801f4076825b4b874c0f6d22\gpt-6.1-sol-candidate-p0-01\fixture\workspace\orders-service`

| 角色 | 輸入分支 | 實際 ref | Commit（前 8 碼） |
| --- | --- | --- | --- |
| 基礎分支 | `main` | `refs/heads/main` | `391609f9` |
| 比較分支 | `feature/review` | `refs/heads/feature/review` | `78a5cf10` |

- 合併方向：`feature/review` → `main`
- 主要差異範圍（共同祖先 → 比較分支）：`391609f9` → `78a5cf10`
- 最終受審版本：`merge-preview`；`c5e96fb4fedba050393b074d8d009d05a248225f`
- 快照擷取：2026-10-29T12:57:33.609429+00:00 → 2026-10-29T12:57:36.671021+00:00（UTC）。
- 適用版本：本報告僅適用上述固定 SHA 及本次擷取的相關 Repo；後續分支更新不改變本次結論。

<details>
<summary>固定版本、搜尋、功能鏈與覆蓋紀錄</summary>

- Repo：<USER_LOCAL_APPDATA>\Temp\rr-quality-matrix-smoke-src-858025dc801f4076825b4b874c0f6d22\gpt-6.1-sol-candidate-p0-01\fixture\workspace\orders-service
- 報告目錄：<USER_LOCAL_APPDATA>\Temp\rr-quality-matrix-smoke-src-858025dc801f4076825b4b874c0f6d22\gpt-6.1-sol-candidate-p0-01\fixture\workspace\orders-service\review-reports
- 基礎分支：main
- 比較分支：feature/review
- 基礎 SHA：391609f9048e30ec6a161da3b3c550f5a67d3ca4
- 比較 SHA：78a5cf10f28db4ae2dd7105b6ee247c726b914f9
- 共同起點：391609f9048e30ec6a161da3b3c550f5a67d3ca4
- 最終來源：merge-preview
- 最終 SHA：c5e96fb4fedba050393b074d8d009d05a248225f
- 擷取開始（UTC）：2026-10-29T12:57:33.609429+00:00
- 快照完成（UTC）：2026-10-29T12:57:36.671021+00:00
- 快照政策：frozen-start/v1
- 本機檔案與暫存區維持原狀：True

版本比對只包含已提交內容。靜態範圍完整不代表目標環境已驗證。

- Diff 起點 DIFF-001：bootstrap.py；391609f9048e30ec6a161da3b3c550f5a67d3ca4 → 78a5cf10f28db4ae2dd7105b6ee247c726b914f9
- Diff 起點 DIFF-002：bootstrap.py；391609f9048e30ec6a161da3b3c550f5a67d3ca4 → c5e96fb4fedba050393b074d8d009d05a248225f
### D-001 新增判定與證據

新增類型：access-change；已搜尋 base 整個 Repo 的 orders 與 startup；orders 僅命中 bootstrap.py 原有 COUNT 查詢，startup 命中該函式及 README。完整固定樹只有 README.md、app.py、bootstrap.py；app.py 是純 publish/consume/checkout，不含別名、ORM 或其他 DB 使用，因此本次為既有表新增刪除操作，不重複列新表。

Diff 起點：DIFF-002；本次新增 DELETE FROM orders，擴大既有表的存取操作，需核對正式帳號權限及資料保護。

`orders-service/bootstrap.py:1-3`（來源：`merge-preview`；固定版本：`c5e96fb4fedba050393b074d8d009d05a248225f`）

內容 SHA-256：`6a612ca3c8538f8b45e499c33b78ae969fb7f337678c8da606294dee1d6ca4bc`

~~~text
1: def startup(db):
2:     db.execute("DELETE FROM orders")
3:     return db.execute("SELECT COUNT(*) FROM orders")
~~~

- 基礎全 Repo 搜尋：命中 1；紀錄 de07eccfa109dd002c803b7b767ae8f9f694d58e11647d321f3a8b36aa218ac8
- 基礎全 Repo 搜尋：命中 2；紀錄 de5988af9172d65bcf1a65e86d342ef7820e01312cfe977c8d9c7b8eb6d454ce

### 關聯 Repo 與功能鏈

| Repo | 固定本機分支 | SHA | 狀態 |
| --- | --- | --- | --- |
| entry / orders-service | 入口指定分支 | 見入口版本／未取得 | captured |

**F-001｜entry → entry｜database**
reviewed：README 指定訂單啟動入口 bootstrap.startup；startup 接收 db，新增無條件 DELETE orders 後回傳 COUNT 查詢。db 是既有注入介面，實際資料庫及提交模式留部署核對；app.checkout 的既有純函式鏈不受此 Diff 修改。
Diff 起點：DIFF-002；追查新增清表 SQL 的啟動入口與後續計數，以確認正常啟動即可觸發破壞性操作。

`orders-service/README.md:1-1`（來源：`merge-preview`；固定版本：`c5e96fb4fedba050393b074d8d009d05a248225f`）

內容 SHA-256：`1a4c4ff93c30ad17132eda704f9c814eae62421e4f315b979117742ecb878246`

~~~text
1: Order startup uses bootstrap.startup; requests use app.checkout.
~~~

`orders-service/bootstrap.py:1-3`（來源：`merge-preview`；固定版本：`c5e96fb4fedba050393b074d8d009d05a248225f`）

內容 SHA-256：`6a612ca3c8538f8b45e499c33b78ae969fb7f337678c8da606294dee1d6ca4bc`

~~~text
1: def startup(db):
2:     db.execute("DELETE FROM orders")
3:     return db.execute("SELECT COUNT(*) FROM orders")
~~~

`orders-service/app.py:1-8`（來源：`merge-preview`；固定版本：`c5e96fb4fedba050393b074d8d009d05a248225f`）

內容 SHA-256：`347691be148560ef0927b9494fffb5d5d3d156756a4d63ddd1b43d9f4d7cdb8e`

~~~text
1: def publish(amount):
2:     return {"amount": amount}
3:
4: def consume(payload):
5:     return payload["amount"]
6:
7: def checkout(amount):
8:     return consume(publish(amount))
~~~

### 靜態覆蓋

| 路徑 | 狀態 | 未完成原因 |
| --- | --- | --- |
| bootstrap.py | reviewed |  |

**依賴類型覆蓋**：

- config：reviewed；已讀取固定全樹的 README、bootstrap.py 與 app.py；本次僅新增 DB DELETE，沒有此類設定讀取、部署定義、client、目標或契約變更。
- db：reviewed；已核對固定全樹與 base 全 Repo 搜尋；既有 orders 新增 DELETE 操作，列 D-001。
- ftp：reviewed；已讀取固定全樹的 README、bootstrap.py 與 app.py；本次僅新增 DB DELETE，沒有此類設定讀取、部署定義、client、目標或契約變更。
- sftp：reviewed；已讀取固定全樹的 README、bootstrap.py 與 app.py；本次僅新增 DB DELETE，沒有此類設定讀取、部署定義、client、目標或契約變更。
- redis：reviewed；已讀取固定全樹的 README、bootstrap.py 與 app.py；本次僅新增 DB DELETE，沒有此類設定讀取、部署定義、client、目標或契約變更。
- kafka：reviewed；已讀取固定全樹的 README、bootstrap.py 與 app.py；本次僅新增 DB DELETE，沒有此類設定讀取、部署定義、client、目標或契約變更。
- other：reviewed；已讀取固定全樹的 README、bootstrap.py 與 app.py；本次僅新增 DB DELETE，沒有此類設定讀取、部署定義、client、目標或契約變更。

</details>

<!-- release-review-report:eyJiYXNlX2lucHV0IjoibWFpbiIsImJhc2VfcmVzb2x2ZWRfcmVmIjoicmVmcy9oZWFkcy9tYWluIiwiYmFzZV9zaGEiOiIzOTE2MDlmOTA0OGUzMGVjNmExNjFkYTNiM2M1NTBmNWE2N2QzY2E0IiwiYmxvY2tlcl9jb3VudCI6MSwiY2FwdHVyZV9jb21wbGV0ZWRfYXQiOiIyMDI2LTEwLTI5VDEyOjU3OjM2LjY3MTAyMSswMDowMCIsImNvbnRleHRfc2hhMjU2IjoiMjZjNjNmNjM4MzQ0NWI4ZDg0ZDViODdhODFhMzQ1ODAyYmY5ZjQ4OWIxZjUxYTE5MjJiMTNjMGZhMDEzZjQ5MCIsImNvdmVyYWdlX3BvbGljeSI6ImFuY2VzdG9yLW5vb3AvdjEiLCJkaWZmX2Jhc2UiOiIzOTE2MDlmOTA0OGUzMGVjNmExNjFkYTNiM2M1NTBmNWE2N2QzY2E0IiwiZGlmZl9yb290cyI6W3siYWZ0ZXIiOnsicmVmIjoiNzhhNWNmMTBmMjhkYjRhZTJkZDcxMDViNmVlMjQ3YzcyNmI5MTRmOSIsInNvdXJjZSI6ImhlYWQifSwiYmVmb3JlIjp7InJlZiI6IjM5MTYwOWY5MDQ4ZTMwZWM2YTE2MWRhM2IzYzU1MGY1YTY3ZDNjYTQiLCJzb3VyY2UiOiJtZXJnZS1iYXNlIn0sImlkIjoiRElGRi0wMDEiLCJvbGRfcGF0aCI6bnVsbCwicGF0aCI6ImJvb3RzdHJhcC5weSIsInN0YXR1cyI6Ik0ifSx7ImFmdGVyIjp7InJlZiI6ImM1ZTk2ZmI0ZmVkYmEwNTAzOTNiMDc0ZDhkMDA5ZDA1YTI0ODIyNWYiLCJzb3VyY2UiOiJtZXJnZS1wcmV2aWV3In0sImJlZm9yZSI6eyJyZWYiOiIzOTE2MDlmOTA0OGUzMGVjNmExNjFkYTNiM2M1NTBmNWE2N2QzY2E0Iiwic291cmNlIjoiYmFzZSJ9LCJpZCI6IkRJRkYtMDAyIiwib2xkX3BhdGgiOm51bGwsInBhdGgiOiJib290c3RyYXAucHkiLCJzdGF0dXMiOiJNIn1dLCJlbnRyeV9yZXBvIjoiZW50cnkiLCJmaW5hbF9yZWYiOiJjNWU5NmZiNGZlZGJhMDUwMzkzYjA3NGQ4ZDAwOWQwNWEyNDgyMjVmIiwiZmluYWxfc291cmNlIjoibWVyZ2UtcHJldmlldyIsImdhcF9jb3VudCI6MCwiZ2VuZXJhdGVkX2F0IjoiMjAyNi0xMC0yOVQxMjo1NzozMy42MDk0MjkrMDA6MDAiLCJoZWFkX2lucHV0IjoiZmVhdHVyZS9yZXZpZXciLCJoZWFkX3Jlc29sdmVkX3JlZiI6InJlZnMvaGVhZHMvZmVhdHVyZS9yZXZpZXciLCJoZWFkX3NoYSI6Ijc4YTVjZjEwZjI4ZGI0YWUyZGQ3MTA1YjZlZTI0N2M3MjZiOTE0ZjkiLCJtZXJnZV9iYXNlIjoiMzkxNjA5ZjkwNDhlMzBlYzZhMTYxZGEzYjNjNTUwZjVhNjdkM2NhNCIsIm1vZGUiOiJtZXJnZSIsInBlbmRpbmdfY291bnQiOjUsInByb2plY3QiOiJvcmRlcnMtc2VydmljZSIsInJlbGVhc2VfYXNzZXNzbWVudCI6ImJsb2NrZWQiLCJyZXBvIjoiPFVTRVJfTE9DQUxfQVBQREFUQT5cXFRlbXBcXHJyLXF1YWxpdHktbWF0cml4LXNtb2tlLXNyYy04NTgwMjVkYzgwMWY0MDc2ODI1YjRiODc0YzBmNmQyMlxcZ3B0LTYuMS1zb2wtY2FuZGlkYXRlLXAwLTAxXFxmaXh0dXJlXFx3b3Jrc3BhY2VcXG9yZGVycy1zZXJ2aWNlIiwicmVwb3J0X3Jvb3QiOiI8VVNFUl9MT0NBTF9BUFBEQVRBPlxcVGVtcFxccnItcXVhbGl0eS1tYXRyaXgtc21va2Utc3JjLTg1ODAyNWRjODAxZjQwNzY4MjViNGI4NzRjMGY2ZDIyXFxncHQtNi4xLXNvbC1jYW5kaWRhdGUtcDAtMDFcXGZpeHR1cmVcXHdvcmtzcGFjZVxcb3JkZXJzLXNlcnZpY2VcXHJldmlldy1yZXBvcnRzIiwicmVwb3NpdG9yaWVzIjpbeyJpZCI6ImVudHJ5IiwiaW5zdHJ1Y3Rpb25zIjpbXSwibmFtZSI6Im9yZGVycy1zZXJ2aWNlIiwicGF0aCI6IjxVU0VSX0xPQ0FMX0FQUERBVEE+XFxUZW1wXFxyci1xdWFsaXR5LW1hdHJpeC1zbW9rZS1zcmMtODU4MDI1ZGM4MDFmNDA3NjgyNWI0Yjg3NGMwZjZkMjJcXGdwdC02LjEtc29sLWNhbmRpZGF0ZS1wMC0wMVxcZml4dHVyZVxcd29ya3NwYWNlXFxvcmRlcnMtc2VydmljZSIsInJvbGUiOiJlbnRyeSIsInNuYXBzaG90Ijp7ImNhcHR1cmVkX2F0IjoiMjAyNi0xMC0yOVQxMjo1NzozNS45ODY4MTkrMDA6MDAiLCJnaXRfZGlyIjoicmVwb3MvZW50cnkvc25hcHNob3QuZ2l0Iiwib2JqZWN0X2Zvcm1hdCI6InNoYTEiLCJwYWNrIjp7ImNoZWNrc3VtIjoiYWE3NTI2MWY3MmM0ZjJhY2JhMmMwMGNjY2Q2MmNmYWIzYmM5ZDI2ZCIsImZpbGUiOiJvYmplY3RzL3BhY2svc25hcHNob3QucGFjayIsImluZGV4Ijoib2JqZWN0cy9wYWNrL3NuYXBzaG90LmlkeCIsImluZGV4X3NoYTI1NiI6ImYzYWJiN2M4ZjQ5YWMwZDZjODAxNWI0YzI3ZTAwZDBlYmUzY2YxMjkyZmI1NjM1MzdlNzk4OGNhMmU2OWQ1YWIiLCJzaGEyNTYiOiJhYzBmYzhmNDIwMDcxZjQyNzgxOGRlYjc3YjdhMTY3MzY4OTU0YzBhZDlhNGE0NGJjMzlkYzkxODVkMTcwYWQwIn0sInBvbGljeSI6ImZyb3plbi1zdGFydC92MSJ9LCJzdGF0dXMiOiJjYXB0dXJlZCJ9XSwicmV2aWV3X2NvbXBsZXRlIjp0cnVlLCJzY2hlbWEiOiJSZWxlYXNlUmV2aWV3UmVwb3J0L3YzIiwic2NvcGUiOiJncm91cCIsInNuYXBzaG90X3BvbGljeSI6ImZyb3plbi1zdGFydC92MSIsInN0YXRpY19ib2R5X3NoYTI1NiI6IjFlMWJjZTNkMGU2Mzk0OThlYjBkMTRkMDNkM2NmOTJhZDM2MWU4OWRhNTUzNjQ3YTg5NjI1ZGM0OGZkNjFkZDciLCJ3b3JraW5nX3RyZWVfdW5jaGFuZ2VkIjp0cnVlLCJ3b3Jrc3BhY2VfaW5zdHJ1Y3Rpb25zIjpbXX0= -->
