# 上板檢查報告

**檢查狀態：靜態檢查完成**

發現兩項會讓流程失敗的程式風險；另有三項新增設定及三項 DB 使用資訊需核對上板準備。

- 程式風險：2 項
- 新增設定提醒：3 項
- 新增 DB 使用提醒：3 項

本報告依固定版本做靜態檢查；設定與 DB 項目記錄新增使用及版本內證據，環境套用狀態未查證。

## 程式風險

### R-001｜P1 上板前優先修正｜數量為零時平均金額計算會失敗

**觸發條件**：以數量 0 計算平均金額。

**影響**：原本回傳零的流程改成除以零例外。

**建議處理**：開發恢復零數量的處理。

**情境來源**：已實際重現，執行紀錄見後文

**技術證據**：

- src/service.py:7-8；來源 merge-preview；版本 ee0d69799c4b2bd9a741365c2b7f69c2d4325f8f
- src/service.py:1-4；來源 base；版本 e46b0ea1fea8e3a50f15244a293e9b5b9421abae

### R-002｜P1 上板前優先修正｜新增發票流程讀取未定義設定而失敗

**觸發條件**：發票準備流程讀取檔案內的必要逾時設定。

**影響**：設定檔沒有該鍵，讀取時發生例外，發票準備流程中斷。

**建議處理**：開發補齊必要設定，或依產品需求提供可用的預設行為。

**情境來源**：已實際重現，執行紀錄見後文

**技術證據**：

- src/service.py:5-11；來源 merge-preview；版本 ee0d69799c4b2bd9a741365c2b7f69c2d4325f8f
- src/settings.json:1-1；來源 merge-preview；版本 ee0d69799c4b2bd9a741365c2b7f69c2d4325f8f
- src/application.py:10-12；來源 merge-preview；版本 ee0d69799c4b2bd9a741365c2b7f69c2d4325f8f

## 新增設定提醒

| 編號 | 設定項目 | 狀態 | 使用說明 |
| --- | --- | --- | --- |
| C-001 | billing_timeout_seconds | 未找到定義 | 新增發票流程必要的逾時設定，讀取時沒有預設值。 |
| C-002 | INVOICE_API_URL | 需確認外部注入 | 新增讀取；未提供時有預設值。 |
| C-003 | discount_rate | 已有定義 | 新增折扣設定；比較版本的設定檔已提供定義。 |

## 新增 DB 使用提醒

| 編號 | 類型 | DB／物件 | 連線識別 | 使用說明 |
| --- | --- | --- | --- | --- |
| D-001 | 資料庫 | BillingDb | 傳入的 DB 連線 | 新增發票流程開始查詢 BillingDb 內的稅率資料。 |
| D-002 | 資料表 | BillingDb.dbo.InvoiceTax | 傳入的 DB 連線 | 查詢發票稅率。 |
| D-003 | 欄位 | BillingDb.dbo.InvoiceTax.tax_rate | 傳入的 DB 連線 | 新增讀取稅率欄位。 |

## 設定與 DB 提醒明細

### C-001｜billing_timeout_seconds

**新增判定**：搜尋基礎版本全部檔案未找到此變數的執行程式使用。

**上板前提醒**：開發補齊版本內設定；對應的實際失敗另見 R-002。

**程式使用證據**：

- src/service.py:10-11；來源 merge-preview；版本 ee0d69799c4b2bd9a741365c2b7f69c2d4325f8f

**基礎版本全 Repo 搜尋**：

- 搜尋 billing_timeout_seconds；符合 0 處；紀錄 876dafa282e5f76adcc11c547ba18c9a4ef8eeed32ab0fda670d83e5bd1e4993

### C-002｜INVOICE_API_URL

**新增判定**：搜尋基礎版本全部檔案未找到此變數的執行程式使用。

**上板前提醒**：維運確認是否需要覆寫預設值；環境值未查證。

**程式使用證據**：

- src/service.py:14-14；來源 merge-preview；版本 ee0d69799c4b2bd9a741365c2b7f69c2d4325f8f

**基礎版本全 Repo 搜尋**：

- 搜尋 INVOICE_API_URL；符合 0 處；紀錄 869f2468e9c1cd958b833efe9b990aea0bdb03a1f7f8944c329d7f1e8e040300

### C-003｜discount_rate

**新增判定**：基礎版本整個 Repo 未找到此設定的程式使用或定義。

**上板前提醒**：維運依本次版本核對折扣設定；已有版本內定義。

**程式使用證據**：

- src/service.py:16-17；來源 merge-preview；版本 ee0d69799c4b2bd9a741365c2b7f69c2d4325f8f

**設定定義證據**：

- src/settings.json:1-1；來源 merge-preview；版本 ee0d69799c4b2bd9a741365c2b7f69c2d4325f8f

**基礎版本全 Repo 搜尋**：

- 搜尋 discount_rate；符合 0 處；紀錄 4f5f03bf8d8b7b96c7a5d63fb3f177e8b5b82ff2a6fdb782e39a829c36c368cd

### D-001｜BillingDb

**新增判定**：基礎版本命中 schema 定義，沒有執行程式使用；這次首次查詢。

**上板前提醒**：確認此連線可使用該資料表；本次未查證環境建置狀態。

**程式使用證據**：

- src/service.py:20-20；來源 merge-preview；版本 ee0d69799c4b2bd9a741365c2b7f69c2d4325f8f

**基礎版本全 Repo 搜尋**：

- 搜尋 BillingDb；符合 1 處；紀錄 b6452234908caee434bbef9a27da7aa6ef41aa467cdb74779bf39e214c0b84dd

### D-002｜BillingDb.dbo.InvoiceTax

**新增判定**：基礎版本命中 schema 定義，沒有執行程式使用；這次首次查詢。

**上板前提醒**：確認此連線可使用該資料表；本次未查證環境建置狀態。

**程式使用證據**：

- src/service.py:20-20；來源 merge-preview；版本 ee0d69799c4b2bd9a741365c2b7f69c2d4325f8f

**基礎版本全 Repo 搜尋**：

- 搜尋 InvoiceTax；符合 1 處；紀錄 c2c07d4c3cfa8058d33a908cd55af4e8bbe7c0fbb512799d25c153afc59d352f

### D-003｜BillingDb.dbo.InvoiceTax.tax_rate

**新增判定**：基礎版本命中 schema 定義，沒有執行程式使用；這次首次查詢。

**上板前提醒**：確認此連線可使用該資料表；本次未查證環境建置狀態。

**程式使用證據**：

- src/service.py:20-20；來源 merge-preview；版本 ee0d69799c4b2bd9a741365c2b7f69c2d4325f8f

**基礎版本全 Repo 搜尋**：

- 搜尋 tax_rate；符合 1 處；紀錄 801b0b94800722ccb1a2b3493bf29b0abcd12fd6cbeafe125a430fb550d277de

## 下一步

- 開發先修正零數量計算及未定義設定造成的失敗。
- 維運確認發票服務位址是否需覆寫預設值，以及 BillingDb 稅率物件的上板準備。
- QA 在修正後驗證零數量與發票準備流程。

## 覆蓋、驗證與限制

| 路徑 | 覆蓋狀態 | 未完成原因 |
| --- | --- | --- |
| src/application.py | reviewed |  |
| src/service.py | reviewed |  |
| src/settings.json | reviewed |  |

**已執行檢查**：

- 僅對本示範 fixture 重現：base 的 average(10, 0) 回傳 0；最終版本發生 ZeroDivisionError。
- 僅對本示範 fixture 重現：billing_timeout() 發生 KeyError('billing_timeout_seconds')。

**未執行檢查**：

- 未查詢 DB、未連線外部服務、未執行部署或實際環境檢查。

**證據缺口**：

- 無紀錄。

**限制**：

- 本報告是人工判斷的測試案例示範；不代表已審查使用者的實際 Repo。
- DB 物件名稱可由固定 SQL 辨識；實際連線由呼叫端傳入，環境權限與物件建置狀態未查證。
- CoreDb.dbo.Orders 已在 base 的 src/existing.py 使用；本次新增呼叫不重複列為新 DB 使用。
- BillingDb 的 base 命中只有 schema.sql 定義；本次才新增執行程式查詢，因此保留首次使用提醒。

## 固定版本紀錄

- 專案：repo
- Repo：C:\Users\denni\AppData\Local\Temp\release-reviewer-demo-19pnm9wr\repo
- 基礎輸入：main
- 比較輸入：feature
- 基礎完整 SHA：e46b0ea1fea8e3a50f15244a293e9b5b9421abae
- 比較完整 SHA：8aaec8342743ec1faf19564662b6967639a0f516
- 共同起點：e46b0ea1fea8e3a50f15244a293e9b5b9421abae
- 變更起點：e46b0ea1fea8e3a50f15244a293e9b5b9421abae
- 比較方式：merge
- 最終內容來源：merge-preview
- 最終內容完整 SHA：ee0d69799c4b2bd9a741365c2b7f69c2d4325f8f
- 擷取時間（UTC）：2026-10-10T04:38:24.080934+00:00
- 本機檔案與暫存區維持原狀：True

版本比對只包含已提交內容；本機未提交修改不在本次範圍內。

<!-- release-review-report:eyJiYXNlX2lucHV0IjoibWFpbiIsImJhc2Vfc2hhIjoiZTQ2YjBlYTFmZWE4ZTNhNTBmMTUyNDRhMjkzZTliNWI5NDIxYWJhZSIsImJvZHlfc2hhMjU2IjoiYjBjODZhMDY0N2VlODBhY2FkYjkzNDlkODA3ZjcxOTc2Nzc1YTk2Y2QwMzVhOGY1ODVhNjI4ZjVlOWYwMTcyMCIsImNvbnRleHRfc2hhMjU2IjoiYTViM2NhNDc4Mzc0ZTVhNDcwN2NjOGRhYzM0MDlhMjIxMjc4M2YzMGQ2NWRhZjg5ZmVmZjczZjE0OTBkOGFjNCIsImRpZmZfYmFzZSI6ImU0NmIwZWExZmVhOGUzYTUwZjE1MjQ0YTI5M2U5YjViOTQyMWFiYWUiLCJmaW5hbF9yZWYiOiJlZTBkNjk3OTljNGIyYmQ5YTc0MTM2NWMyYjdmNjljMmQ0MzI1ZjhmIiwiZmluYWxfc291cmNlIjoibWVyZ2UtcHJldmlldyIsImdlbmVyYXRlZF9hdCI6IjIwMjYtMTAtMTBUMDQ6Mzg6MjQuMDgwOTM0KzAwOjAwIiwiaGVhZF9pbnB1dCI6ImZlYXR1cmUiLCJoZWFkX3NoYSI6IjhhYWVjODM0Mjc0M2VjMWZhZjE5NTY0NjYyYjY5Njc2MzlhMGY1MTYiLCJtZXJnZV9iYXNlIjoiZTQ2YjBlYTFmZWE4ZTNhNTBmMTUyNDRhMjkzZTliNWI5NDIxYWJhZSIsIm1vZGUiOiJtZXJnZSIsInByb2plY3QiOiJyZXBvIiwicmVwbyI6IkM6XFxVc2Vyc1xcZGVubmlcXEFwcERhdGFcXExvY2FsXFxUZW1wXFxyZWxlYXNlLXJldmlld2VyLWRlbW8tMTlwbm05d3JcXHJlcG8iLCJyZXZpZXdfY29tcGxldGUiOnRydWUsInNjaGVtYSI6IlJlbGVhc2VSZXZpZXdSZXBvcnQvdjEiLCJ3b3JraW5nX3RyZWVfdW5jaGFuZ2VkIjp0cnVlfQ== -->
