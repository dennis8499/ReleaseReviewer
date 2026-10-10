# 示範案例

[上板檢查報告](report.md) 與 [結構化結果](report.json) 由固定 Git 版本、人工判斷的草稿及正式報告 helper 產生。base、head 子目錄保存實際受審來源；暫存 Git Repo 與審查 context 於產生後清理。

此案例展示：

- 兩項程式風險：移除零數量保護、讀取未定義的必要設定。只對自建 fixture 做最小重現，沒有執行實際專案。
- 三項設定提醒：未找到定義、需確認外部注入且有預設值、已有版本內定義。
- 三項 DB 提醒：首次使用 BillingDb、InvoiceTax 與 tax_rate。base 只有 schema 定義，仍需提醒首次程式使用。
- 不重複提醒 CoreDb.dbo.Orders，因為 base 另一個檔案已使用該物件。

重新建立示範：

~~~powershell
python -X utf8 -B scripts/build_demo.py
~~~

這是 Skill 行為與輸出示範，語意判斷由目前建立 Skill 的 agent 完成，沒有獨立模型盲測。自動測試主要驗證固定版本、引用、搜尋紀錄、覆蓋、清理及獨立封裝；不以這些測試宣稱語意審查必然沒有漏報。正式驗收仍需用實際 Repo 的分支組合試用。
