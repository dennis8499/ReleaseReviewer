# 上版檢查: orders-service

## 審查結論

**待核對上版條件**｜靜態檢查完成。靜態檢查已完成，未發現本次變更直接造成的 P0／P1 阻擋；既有 TAX_SFTP_TARGET 改指新 SFTP 主機與檔案路徑，部署條件仍待人工核對。

問題數量：P0 0／P1 0（僅列直接阻擋上版的程式風險）。

阻擋 0 項｜合併衝突 0 項｜待確認 5 項｜檢查缺口 0 項。
判斷固定於產生時；人工填寫 Checklist 不會自動核准上版。

- 入口 Repo：`orders-service`
- 合併方向：`feature/review`（`b439a101`） → `main`（`76924756`）

## 問題總攬

| 項目 | 等級 | 說明 |
| --- | --- | --- |
| — | — | 已檢查範圍未發現已確認的直接上版阻擋。 |

## 問題詳情（工程師參考）

已檢查範圍未發現有證據的直接阻擋；待確認條件與缺口見後文。

## 範圍與限制

本次只審查指定 Repo 的已提交差異；其他 Repo 的擷取時 HEAD 是功能鏈佐證。
結論只適用本次固定版本；擷取後分支移動不改寫本次結果。

- 審查限已提交的 main → feature/review 及乾淨合併預覽；固定 catalog 僅含入口 Repo，無 supporting Repo、適用 AGENTS.md 或必要 merge parent。
- upload_tax 的設定讀取、目標與 put 呼叫／回傳已由固定程式辨識；Repo 未提供注入 sftp 物件的具體 adapter、認證、重試或傳輸實作。本次未變更此介面，不據此推定執行必然失敗或現場權限不足。
- 未驗證實際環境的連線、認證、主機金鑰、目錄、接收端約定與存取權限；這些條件須由 E-001 Checklist 人工確認。靜態檢查完成不代表環境就緒或核准上版。

**已執行檢查**

- 無紀錄。

**未執行檢查**

- 依使用者要求，未執行任何專案測試、部署、實際 SFTP／DB 或其他環境連線。
- 未存取 Git Server、GitLab 或 oracle；本機分支固定快照的 fetch 次數為 0。

### 新增部署依賴與人工核對

<a id="E-001"></a>

#### E-001｜稅務上傳新主機 tax-new.internal 的 /archive/invoices.csv

既有設定鍵由舊主機的 /drop/current.csv 改指 tax-new.internal 的 /archive/invoices.csv。app.py 載入 settings.json 後，upload_tax 將此設定與 data 傳給注入的 sftp.put 並回傳其結果；本次改變實際傳輸目標，並未新增上傳程式。（已辨識目標）
連線／用途：TAX_SFTP_TARGET（稅務上傳目標）。
**上版準備**：部署前由負責人確認新主機的 DNS／網路與防火牆、SFTP 認證來源及主機金鑰、/archive 目錄與服務帳號的寫入／覆寫權限、接收端對新檔名及目錄的約定。記錄核對人、日期與實際傳輸驗證證據；目前不推定環境已建立或已就緒。

**核對項**：[K-001](#K-001)、[K-002](#K-002)、[K-003](#K-003)、[K-004](#K-004)、[K-005](#K-005)

核對狀態限「待確認／已確認／未通過／不適用」。已確認需核對人、日期及開通／套用後驗證證據；
僅有申請工單仍為待確認，不適用需具體理由。必要項目待確認或未通過時，上版準備未完成。
本段核對紀錄可人工更新；固定正文與產生時判斷保留原始快照。

<!-- release-checklist:start -->
### 人工核對紀錄

#### [E-001｜稅務上傳新主機 tax-new.internal 的 /archive/invoices.csv](#E-001)

| 編號 | 核對事項 | 狀態 | 核對人 | 日期 | 工單／驗證證據 | 不適用理由 |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="K-001"></a>K-001 | 確認目標主機、連接埠、DNS／路由與防火牆已開通；本機依賴可記錄不適用理由。 | 待確認 | | | | |
| <a id="K-002"></a>K-002 | 確認 TLS／憑證／加密方式與目標環境相符；不適用時說明原因。 | 待確認 | | | | |
| <a id="K-003"></a>K-003 | 確認 SFTP 帳號／金鑰及憑證部署來源有效。 | 待確認 | | | | |
| <a id="K-004"></a>K-004 | 確認已核對 SFTP 主機金鑰及輪替安排。 | 待確認 | | | | |
| <a id="K-005"></a>K-005 | 確認遠端目錄及檔案操作權限符合服務帳號需求。 | 待確認 | | | | |

<!-- release-checklist:end -->

### 下一步

- 由部署負責人完成 E-001 的可編輯 Checklist，填寫核對人、日期及開通／套用後的驗證證據；僅有申請單或排程仍維持待確認。

## 技術審查紀錄（工程師參考）

### 比較資訊

- Repo：`orders-service`
- 本機路徑：`<USER_LOCAL_APPDATA>\Temp\rr-quality-v0.10.0-smoke-src\gpt-6.1-sol-candidate-same-target-01\fixture\workspace\orders-service`

| 角色 | 輸入分支 | 實際 ref | Commit（前 8 碼） |
| --- | --- | --- | --- |
| 基礎分支 | `main` | `refs/heads/main` | `76924756` |
| 比較分支 | `feature/review` | `refs/heads/feature/review` | `b439a101` |

- 合併方向：`feature/review` → `main`
- 主要差異範圍（共同祖先 → 比較分支）：`76924756` → `b439a101`
- 最終受審版本：`merge-preview`；`ab749eb68db6bebce628b2b4c92eeae54a6a72fd`
- 快照擷取：2026-10-29T13:50:27.299456+00:00 → 2026-10-29T13:50:29.105634+00:00（UTC）。
- 適用版本：本報告僅適用上述固定 SHA 及本次擷取的相關 Repo；後續分支更新不改變本次結論。

<details>
<summary>固定版本、搜尋、功能鏈與覆蓋紀錄</summary>

- Repo：<USER_LOCAL_APPDATA>\Temp\rr-quality-v0.10.0-smoke-src\gpt-6.1-sol-candidate-same-target-01\fixture\workspace\orders-service
- 報告目錄：<USER_LOCAL_APPDATA>\Temp\rr-quality-v0.10.0-smoke-src\gpt-6.1-sol-candidate-same-target-01\fixture\workspace\orders-service\review-reports
- 基礎分支：main
- 比較分支：feature/review
- 基礎 SHA：76924756c7b4b8ed7a0b35a670adfc3e41266906
- 比較 SHA：b439a101aaddcf42e2b107f23a965416a5f2f75f
- 共同起點：76924756c7b4b8ed7a0b35a670adfc3e41266906
- 最終來源：merge-preview
- 最終 SHA：ab749eb68db6bebce628b2b4c92eeae54a6a72fd
- 擷取開始（UTC）：2026-10-29T13:50:27.299456+00:00
- 快照完成（UTC）：2026-10-29T13:50:29.105634+00:00
- 快照政策：frozen-start/v1
- 本機檔案與暫存區維持原狀：True

版本比對只包含已提交內容。靜態範圍完整不代表目標環境已驗證。

- Diff 起點 DIFF-001：settings.json；76924756c7b4b8ed7a0b35a670adfc3e41266906 → b439a101aaddcf42e2b107f23a965416a5f2f75f
- Diff 起點 DIFF-002：settings.json；76924756c7b4b8ed7a0b35a670adfc3e41266906 → ab749eb68db6bebce628b2b4c92eeae54a6a72fd
### E-001 新增判定與證據

新增類型：target-change；入口 base 全 Repo 的 TAX_SFTP_TARGET、sftp、SETTINGS 搜尋與四個檔案全文核對，確認既有使用只指向舊主機及 /drop/current.csv；新主機與新路徑的獨立全 Repo 搜尋皆無命中。已沿 JSON 載入與 SETTINGS 索引排除別名／等價搬移，故同鍵仍須列新目標部署提醒。

Diff 起點：DIFF-001、DIFF-002；本次唯一差異變更已被既有載入與 sftp.put 讀取的 TAX_SFTP_TARGET 值，導致稅務上傳改指新主機及檔案路徑。

`orders-service/settings.json:1-1`（來源：`merge-preview`；固定版本：`ab749eb68db6bebce628b2b4c92eeae54a6a72fd`）

內容 SHA-256：`1db3386ecd974dd81995c30de48bf5d22f4cf57b59a3799c6d6b96604561a095`

設定鍵：TAX_SFTP_TARGET

設定值已省略；僅保留可解析的鍵名及原始內容 SHA-256。

`orders-service/app.py:4-7`（來源：`merge-preview`；固定版本：`ab749eb68db6bebce628b2b4c92eeae54a6a72fd`）

內容 SHA-256：`7c9874e2966cb5b468c6a9476d5c299c251232d574a95c6852f83d24095a8460`

~~~text
4: SETTINGS = json.loads(Path(__file__).with_name("settings.json").read_text())
5:
6: def upload_tax(sftp, data):
7:     return sftp.put(SETTINGS["TAX_SFTP_TARGET"], data)
~~~

- 基礎全 Repo 搜尋：命中 2；紀錄 8552ac2dbda094891597c741edc8850dc6c1e40357e100df122883c5d47998cd
- 基礎全 Repo 搜尋：命中 3；紀錄 60a84cdaebd3d5894b64d892d6ed45908a815bd557cad129f52091db88e5565f
- 基礎全 Repo 搜尋：命中 2；紀錄 3593294e68cf22de6b414010f1e845d0c02c4d9332f6dd3bce3de51ac298287e
- 基礎全 Repo 搜尋：命中 0；紀錄 4f3bbba3fdc716d64614a8ba3b1166ea7812c2a5744bc23f3cc1652258dda8db
- 基礎全 Repo 搜尋：命中 0；紀錄 6c71d3461c7ae1fc230dcb36fa9aebf83f8e98cd7c0a0e6ceaf2005a0a5359d2

### 關聯 Repo 與功能鏈

| Repo | 固定本機分支 | SHA | 狀態 |
| --- | --- | --- | --- |
| entry / orders-service | 入口指定分支 | 見入口版本／未取得 | captured |

### 靜態覆蓋

| 路徑 | 狀態 | 未完成原因 |
| --- | --- | --- |
| settings.json | reviewed |  |

**依賴類型覆蓋**：

- config：reviewed；已檢查兩版本 settings.json 及 app.py 的 JSON 載入與 SETTINGS 索引；沒有新增設定鍵或載入方式。既有 TAX_SFTP_TARGET 的有效目標變更由 E-001 核對，不因鍵名相同而略過。
- db：reviewed；已閱讀入口 base 與最終版本全部四個檔案；bootstrap.startup 仍執行既有 orders 計數查詢，app.py 無新增 DB 操作；唯一設定差異未改變 DB 連線、物件、欄位或權限。
- ftp：reviewed；完整固定來源中只有既有 SFTP 上傳呼叫；本次唯一設定變更仍使用 SFTP，沒有 FTP client、部署定義、目錄或權限新增。
- sftp：reviewed；已沿 settings.json → app.py 載入 → upload_tax → 注入的 sftp.put 追查，並比對 base 全 Repo；新主機與新檔案路徑列 E-001 target-change，現場條件留 Checklist。
- redis：reviewed；已核對全部固定檔案與唯一設定差異，沒有 Redis client、連線、key prefix、命令或部署資源變更。
- kafka：reviewed；已核對全部固定檔案與唯一設定差異，沒有 Kafka provider、事件發布／消費、Topic、Group 或部署資源變更。
- other：reviewed；已核對 README、app.py、bootstrap.py、settings.json 的兩版本全文及唯一 release diff；沒有其他新增服務、套件或部署資源。新目標依明確 SFTP 設定與上傳用途分類至 sftp。

</details>

<!-- release-review-report:eyJiYXNlX2lucHV0IjoibWFpbiIsImJhc2VfcmVzb2x2ZWRfcmVmIjoicmVmcy9oZWFkcy9tYWluIiwiYmFzZV9zaGEiOiI3NjkyNDc1NmM3YjRiOGVkN2EwYjM1YTY3MGFkZmMzZTQxMjY2OTA2IiwiYmxvY2tlcl9jb3VudCI6MCwiY2FwdHVyZV9jb21wbGV0ZWRfYXQiOiIyMDI2LTEwLTI5VDEzOjUwOjI5LjEwNTYzNCswMDowMCIsImNvbnRleHRfc2hhMjU2IjoiOTBjY2I1NjIyMDQxMzUyYTEzZDU2YWJiZDUyMzhkYTVmZTQ3NzM1NjQ0MGNkMWNhMjg3ZDBiM2NiMDY3OWMxYyIsImNvdmVyYWdlX3BvbGljeSI6ImFuY2VzdG9yLW5vb3AvdjEiLCJkaWZmX2Jhc2UiOiI3NjkyNDc1NmM3YjRiOGVkN2EwYjM1YTY3MGFkZmMzZTQxMjY2OTA2IiwiZGlmZl9yb290cyI6W3siYWZ0ZXIiOnsicmVmIjoiYjQzOWExMDFhYWRkY2Y0MmUyYjEwN2YyM2E5NjU0MTZhNWYyZjc1ZiIsInNvdXJjZSI6ImhlYWQifSwiYmVmb3JlIjp7InJlZiI6Ijc2OTI0NzU2YzdiNGI4ZWQ3YTBiMzVhNjcwYWRmYzNlNDEyNjY5MDYiLCJzb3VyY2UiOiJtZXJnZS1iYXNlIn0sImlkIjoiRElGRi0wMDEiLCJvbGRfcGF0aCI6bnVsbCwicGF0aCI6InNldHRpbmdzLmpzb24iLCJzdGF0dXMiOiJNIn0seyJhZnRlciI6eyJyZWYiOiJhYjc0OWViNjhkYjZiZWJjZTYyOGIyYjRjOTJlZWFlNTRhNmE3MmZkIiwic291cmNlIjoibWVyZ2UtcHJldmlldyJ9LCJiZWZvcmUiOnsicmVmIjoiNzY5MjQ3NTZjN2I0YjhlZDdhMGIzNWE2NzBhZGZjM2U0MTI2NjkwNiIsInNvdXJjZSI6ImJhc2UifSwiaWQiOiJESUZGLTAwMiIsIm9sZF9wYXRoIjpudWxsLCJwYXRoIjoic2V0dGluZ3MuanNvbiIsInN0YXR1cyI6Ik0ifV0sImVudHJ5X3JlcG8iOiJlbnRyeSIsImZpbmFsX3JlZiI6ImFiNzQ5ZWI2OGRiNmJlYmNlNjI4YjJiNGM5MmVlYWU1NGE2YTcyZmQiLCJmaW5hbF9zb3VyY2UiOiJtZXJnZS1wcmV2aWV3IiwiZ2FwX2NvdW50IjowLCJnZW5lcmF0ZWRfYXQiOiIyMDI2LTEwLTI5VDEzOjUwOjI3LjI5OTQ1NiswMDowMCIsImhlYWRfaW5wdXQiOiJmZWF0dXJlL3JldmlldyIsImhlYWRfcmVzb2x2ZWRfcmVmIjoicmVmcy9oZWFkcy9mZWF0dXJlL3JldmlldyIsImhlYWRfc2hhIjoiYjQzOWExMDFhYWRkY2Y0MmUyYjEwN2YyM2E5NjU0MTZhNWYyZjc1ZiIsIm1lcmdlX2Jhc2UiOiI3NjkyNDc1NmM3YjRiOGVkN2EwYjM1YTY3MGFkZmMzZTQxMjY2OTA2IiwibW9kZSI6Im1lcmdlIiwicGVuZGluZ19jb3VudCI6NSwicHJvamVjdCI6Im9yZGVycy1zZXJ2aWNlIiwicmVsZWFzZV9hc3Nlc3NtZW50IjoicGVuZGluZy1jb25maXJtYXRpb24iLCJyZXBvIjoiPFVTRVJfTE9DQUxfQVBQREFUQT5cXFRlbXBcXHJyLXF1YWxpdHktdjAuMTAuMC1zbW9rZS1zcmNcXGdwdC02LjEtc29sLWNhbmRpZGF0ZS1zYW1lLXRhcmdldC0wMVxcZml4dHVyZVxcd29ya3NwYWNlXFxvcmRlcnMtc2VydmljZSIsInJlcG9ydF9yb290IjoiPFVTRVJfTE9DQUxfQVBQREFUQT5cXFRlbXBcXHJyLXF1YWxpdHktdjAuMTAuMC1zbW9rZS1zcmNcXGdwdC02LjEtc29sLWNhbmRpZGF0ZS1zYW1lLXRhcmdldC0wMVxcZml4dHVyZVxcd29ya3NwYWNlXFxvcmRlcnMtc2VydmljZVxccmV2aWV3LXJlcG9ydHMiLCJyZXBvc2l0b3JpZXMiOlt7ImlkIjoiZW50cnkiLCJpbnN0cnVjdGlvbnMiOltdLCJuYW1lIjoib3JkZXJzLXNlcnZpY2UiLCJwYXRoIjoiPFVTRVJfTE9DQUxfQVBQREFUQT5cXFRlbXBcXHJyLXF1YWxpdHktdjAuMTAuMC1zbW9rZS1zcmNcXGdwdC02LjEtc29sLWNhbmRpZGF0ZS1zYW1lLXRhcmdldC0wMVxcZml4dHVyZVxcd29ya3NwYWNlXFxvcmRlcnMtc2VydmljZSIsInJvbGUiOiJlbnRyeSIsInNuYXBzaG90Ijp7ImNhcHR1cmVkX2F0IjoiMjAyNi0xMC0yOVQxMzo1MDoyOC42NzY0MjIrMDA6MDAiLCJnaXRfZGlyIjoicmVwb3MvZW50cnkvc25hcHNob3QuZ2l0Iiwib2JqZWN0X2Zvcm1hdCI6InNoYTEiLCJwYWNrIjp7ImNoZWNrc3VtIjoiMDE1MmQ5YjU5NjRhMDI1NjNjM2JlZjMxZTQ2YzFiYTcwZDg3NDNjYSIsImZpbGUiOiJvYmplY3RzL3BhY2svc25hcHNob3QucGFjayIsImluZGV4Ijoib2JqZWN0cy9wYWNrL3NuYXBzaG90LmlkeCIsImluZGV4X3NoYTI1NiI6Ijg1N2IwYWQyZWUyY2Q0NDY5MWI4MjRmMDZlMmI0OWQzNDRjNjk5YTEwOWMzOTBiNmI3YmQ3YzlmMzU3YzZkZGYiLCJzaGEyNTYiOiIwYjJlNzQ1ZTBjMzI4MjI3NTMzNWNlZGYyMjMyYTMxY2Y2ZmVlZDdiZGM0N2E0NDQ0MGY1NjBiMjZhN2EyOTdjIn0sInBvbGljeSI6ImZyb3plbi1zdGFydC92MSJ9LCJzdGF0dXMiOiJjYXB0dXJlZCJ9XSwicmV2aWV3X2NvbXBsZXRlIjp0cnVlLCJzY2hlbWEiOiJSZWxlYXNlUmV2aWV3UmVwb3J0L3YzIiwic2NvcGUiOiJncm91cCIsInNuYXBzaG90X3BvbGljeSI6ImZyb3plbi1zdGFydC92MSIsInN0YXRpY19ib2R5X3NoYTI1NiI6IjM5NThlYjUyMjViMGYyNzk1ZTdiODJjNWEzNWZmODc2NGQ2Yzk4Yzc5MzRjYzUyMmUxOGQ4ZDcxODI5NWYxMDkiLCJ3b3JraW5nX3RyZWVfdW5jaGFuZ2VkIjp0cnVlLCJ3b3Jrc3BhY2VfaW5zdHJ1Y3Rpb25zIjpbXX0= -->
