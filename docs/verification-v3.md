# ReleaseReviewer 0.3.0 驗證

日期：2026-10-14（Asia/Taipei）。Windows 本機驗證；原生技能 0.3.0，工作台程式與本機 0.23.0 封裝同步。

## 驗證結果

| 檢查 | 結果 |
| --- | --- |
| 原生 unittest 回歸 | 60 項通過 |
| 最終證據片段與設定／認證省略檢查 | 最後調整後針對性重跑 2 項通過 |
| skill-creator quick_validate | 專案來源及已安裝技能通過 |
| 工作台編譯與 Webview 型別檢查 | 通過 |
| 工作台單元測試 | 313 項通過 |
| 工作台原生技能安裝整合 | 7 項通過 |
| VSIX 與組合包 | 打包完成；verify-vsix 通過，兩種封裝 payload 與 SHA256SUMS 一致 |
| 已安裝版冒煙 | 14 個檔案與 ZIP 一致，main/main 得到「沒有差異」，只產生一份報告，context 清理，Git index 不變 |

原生執行命令：`python -X utf8 -B -m unittest discover -s tests -v`。工作台驗證包含 compile、typecheck:webview、完整編譯後單元測試、release_reviewer_integration_test.py 及 package／verify-vsix。

## 已驗證行為

- 使用者只提供 Repo、基礎分支、比較分支；舊模式、非分支、JSON 與報告路徑覆寫拒絕。
- 直接 P0／P1 必須有 blocking_reason、觸發條件、影響、建議及最終版本證據；P2／P3 不收錄。
- 上版判斷優先序為阻擋 → 檢查未完成 → 待核對 → 未發現阻擋；合併衝突屬阻擋。未完成的功能鏈關係也列入缺口數與正文原因。
- Folder、Group、已儲存 multi-root 直接輸出根目錄的一份 Markdown；合法 UTF-8 BOM／JSONC 工作區可讀取。未儲存 multi-root 在工作台停止，不猜根目錄。
- 重跑保留歷史；同時間、同版本重名續號。人工紀錄不沿用至新報告。
- 新增設定、DB 及外部資源初始全部待確認；首次使用、部署定義、目標改變與新增權限各有核對，既有等價使用不重複提醒。
- Context／Draft／Report 使用 v3；publish_report 回傳單一路徑，CLI 只回傳 markdown_report。
- 人工填寫區塊不改變 static_body_sha256；修改固定正文或 Checklist 邊界會影響驗證。
- 非快轉合併的暫存最終 tree 清理後，引用行的非機密片段及內容 SHA-256 仍保留於報告。設定值、認證／私鑰及完整連線設定行省略。
- 保留來源 SHA／行號核對、完整變更覆蓋、跨 Repo 固定本機版本、機密遮蔽、本機修改隔離及成功／失敗暫存清理。
- 工作台 Group／repository 安裝、新版單檔報告、saved workspace 的人工 Checklist 編輯、舊版升級／回復、來源封裝一致性及未知修改拒絕均通過。

## 示範與本機交付

[v3 示範](demo/v3/README.md)保留 base／head 原始來源，重現兩項程式失敗；唯一報告含三項設定、三項 DB、四項外部資源及 39 個初始待確認核對項。範例屬人工判斷 fixture，非正式業務審查。

2026-10-14 本機交付 ZIP 的 SHA-256（歷史證據）：

`aeee49157e16d876ca5ca9650f51f10448b568b84a8a25c35b2c9028e8960098`

已同步 `C:/Users/denni/.codex/skills/release-reviewer`；原 0.2.0 的 14 個檔案與摘要備份於工作區 `.tmp/release-reviewer-install-0.3.0/backup-0.2.0/`，其他使用者內容保留。舊 0.1.0／0.2.0 發行包及歷史報告保留。

本機另產生 GitlabWorkSpace 的 0.23.0 VSIX 與組合 ZIP，包含相同的 0.3.0 技能來源。

## 2026-10-15 正式發版驗證

Windows／Python 3.14 的完整原生回歸再次完成，60 項測試通過。最終技能來源重新打包為 `release-reviewer-0.3.0.zip`，SHA-256 為 `a482db2395462c1848d9bdb9b60e7eee781d9bafbe646cad7c715c350c3618a4`，另附同名 `.zip.sha256` 校驗檔。發佈前逐檔比對最終 Git Commit，並要求 Linux／Windows × Python 3.10／3.14 的 CI 全數通過。

GitlabWorkSpace 0.24.0 將固定同一正式 ZIP 與來源 Commit。前述 0.23.0 封裝、安裝紀錄及歷史摘要保留，不能用來代替本次重新建置的安裝包。

## 驗證界線

測試與獨立檢查使用隔離 fixture，驗證 helpers 的來源、判斷、報告、人工編輯及安裝契約；不等於完整自動語意分析或正式業務功能鏈驗收。未連線 DB、FTP、SFTP、Redis、Kafka 或部署目標環境，實際上版準備由負責人核對。
