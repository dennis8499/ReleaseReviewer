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
- **完整靜態鏈與環境待核對：** 帳務 producer 將幣別欄位 `currency_code` 改為 `settlement_currency`，
  relay 原樣轉送，必要結算 consumer 仍讀取舊欄位。
  保存三端證據並列契約不相容的 P1；入口發布與 consumer 結果已足以判斷。broker 部署、ACL、ACK／retry
  和 provider 內部實作未查，放 limitations；新增或變更的 Topic／payload schema 保留外部資源提醒及 Checklist，
  不憑這些環境未知增加 chain_gaps。若必要 consumer 版本取得不到，則保留真缺口及未完成。
- **條件式設定與動態目標：** `BILLING_REGION` 是必要分區查詢條件；未注入時，程式拒絕查詢並中斷帳務流程。
  若固定程式足以證明此條件下的 P1，列出條件及設定提醒，不聲稱現場未注入。若 DB 目標由無法取得的 mapping 決定，則
  保留 `resolution=unknown` 及具體缺口；不能用環境待確認掩蓋靜態目標仍未知。
- **抽象事件介面：** 固定程式只呼叫 `bus.send("invoice.updated", payload)`，未提供可辨識的 adapter／provider。
  既有 Topic 的 payload 新增欄位仍需核對 consumer 相容性：用 `kind=other`、`resolution=identified`，
  name 寫已知 Topic／schema，limitations 寫 provider 未查。保留契約與部署順序提醒，不生成確定的 Kafka
  ACL／partition／Consumer Group 義務；找到固定 Kafka client／adapter 證據後才改用 Kafka 類型。
