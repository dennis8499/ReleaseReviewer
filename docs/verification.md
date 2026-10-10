# 驗證紀錄

日期：2026-10-10（Asia/Taipei）

版本：release-reviewer 0.1.0。驗證環境為 Windows、Python 3.14.7、Git 2.55.0.windows.5。

## 已執行

- skill-creator quick_validate.py：通過。
- python -X utf8 -B -m unittest discover -s tests -v：35 項通過。
- python -X utf8 -B scripts/build_demo.py：通過，產生兩項程式風險、三項設定提醒、三項 DB 使用提醒的[示範報告](demo/report.md)。
- python -X utf8 -B scripts/package_release.py：成功產生獨立 ZIP 與 SHA-256 檔。
- 獨立封裝測試：只解壓 Skill，在清除 PYTHONPATH 的子程序中擷取版本並產生報告；不依賴 MergeReviewer 安裝位置。
- 已安裝到 C:/Users/denni/.codex/skills/release-reviewer，9 個檔案與原始碼逐檔 SHA-256 一致。
- 已安裝版對本機 MergeReviewer 的 main／main 做只讀檢查：沒有差異、覆蓋完整、本機檔案與暫存區維持原狀；未在該 Repo 寫報告。

## 驗證重點

- 固定 base/head 與合併預覽，實際上板差異使用 base 到最終 tree；新增判斷搜尋完整 base Repo。
- 本機 staged、unstaged、untracked 檔案保留，內容未混入固定版本。
- 遠端指定版本需取得成功；失敗時拒絕過期 remote-tracking ref，不改用同名本機分支。
- 有衝突、未覆蓋路徑、二進位內容限制或無法辨識項目時，保留未完成狀態。
- SHA、行號、設定定義證據、全 Repo 基礎搜尋紀錄、逐檔覆蓋及提醒去重均有驗證。
- 報告成功、驗證失敗或寫入失敗均清理 owned context；一般未管理的目錄不刪除。
- Markdown 與 JSON 一致，正文摘要可驗證，常見秘密值格式在兩種輸出中遮蔽。

## 行為示範與限制

示範保留 base/head 原始檔，確認另一檔案已用過的 CoreDb.dbo.Orders 不重複列出；BillingDb 只有 schema 定義，首次程式使用仍列提醒。自建 fixture 的除以零與缺少設定例外已最小重現；未查詢 DB 或外部服務。

語意判斷由建立 Skill 的 agent 完成，未做獨立模型盲測。35 項測試驗證 helpers 與輸出契約，不證明語意審查沒有漏報。尚未使用使用者的實際 Repo 驗收。本頁記錄本機驗證；Linux、Windows 及 Python 3.10、3.14 的持續驗證結果，請見 [GitHub Actions](https://github.com/dennis8499/ReleaseReviewer/actions/workflows/ci.yml)。

GitlabWorkSpace 與既有 MergeReviewer 不在此階段修改範圍；整合留待獨立試用確認後另行處理。
