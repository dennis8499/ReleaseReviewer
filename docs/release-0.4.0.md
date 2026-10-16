# ReleaseReviewer 0.4.0

日期：2026-10-16（Asia/Taipei）。

本版新增批次固定證據操作、去重的 review-worklist.json、尚未審查的
review-result-draft.json，以及單次操作內的驗證快取。保留完整命中、固定來源、
全部 merge parents 與逐類依賴對帳；工作清單與草稿骨架不代表語意審查完成。

release_report.py 的 --retain-on-validation-error 在可修正草稿錯誤時以 exit 3
保留 context，修正後重新完整驗證。來源完整性、Git、逾時及輸出失敗仍清理；
未啟用此選項的既有呼叫沿用原失敗清理行為。Context／Draft／Report 契約維持 v3。

本機 Windows／Python 3.14 完整執行 100 項單元測試，全部通過，並重建獨立 ZIP
及 SHA-256。解壓套件驗證包含擷取與發布，無 MergeReviewer 執行期依賴。
正式提交另由 Linux／Windows × Python 3.10／3.14 CI 執行完整測試及封裝。

效能數字及其固定候選、樣本和限制保留在 [效能驗證](performance.md)，
不把 helper 測試或既有量測解讀為實際部署環境驗收。

發行附件為 release-reviewer-0.4.0.zip 與 release-reviewer-0.4.0.zip.sha256。
GitlabWorkSpace 0.24.4 使用該正式 ZIP 整合兩種安裝 profile。
