# 上版審查整合情境

- **既有定義、首次使用：** `$release-reviewer Repo=BillingApi 基礎分支=main 比較分支=feature/invoice`。
  base 已定義 `BillingDb.dbo.Invoice`，但全 Repo 搜尋與 mapping 查證均無正式程式使用；
  最終預覽首次寫入該表。記錄 first-use、base 搜尋 ID、定義／使用證據並產生待確認 Checklist；
  只憑首次使用不能認定環境缺表或列 P1。靜態完整且只有待核對項時為 pending-confirmation。
- **同鍵改目標：** `BillingDb` 鍵名未變，但最終設定改指另一邏輯 DB。沿載入路徑核對目標，
  列 target-change 並確認新目標與帳號權限；不能以鍵名相同略過。僅填「已申請」仍是待確認。
- **阻擋與缺口同時存在：** 最終預覽可證明必要流程的 P1 失敗，另有必要 consumer 版本缺失。
  保留 P1 及其 blocking_reason／固定證據，也保留鏈路缺口；helper 判 blocked，不能因已有阻擋
  而省略缺口，也不能以未找到更多問題宣稱完整。人工填寫限 Checklist 邊界，固定判斷保持快照。
