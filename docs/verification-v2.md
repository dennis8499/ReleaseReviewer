# ReleaseReviewer 0.2.0 驗證

日期：2026-10-12（Asia/Taipei）。Windows 本機驗證；本次同步 GitlabWorkSpace 0.20.0，MergeReviewer 固定來源維持 0.10.0。

## 原生 Skill

- `python -X utf8 -B -m unittest discover -s tests -v`：48 項通過。
- 移除 context 的舊 JSON 格式選項後，再執行 `test_context.py`：16 項通過；直接比較、JSON、Tag、commit、HEAD 與 revision expression 均拒絕。
- 新增 VSCode multi-root JSONC／Git worktree 測試：1 項通過；只讀取明確工作區根目錄，仍固定本機預設分支。目前共 49 項測試均已執行通過。
- 最後補強空白／未知 Repo 引用的拒絕檢查，對應證據測試重跑通過；最終 0.2.0 ZIP SHA-256 為 `0042d7519827a9ad350b8259ee111588cab7e4c70311158d9447872973f78179`。
- skill-creator `quick_validate.py`：通過。
- 獨立封裝解壓後，在清空 PYTHONPATH 的程序擷取版本並產生報告，無其他 Skill 執行期依賴；ZIP 可重複產生相同 bytes。
- [v2 範例](demo/v2/README.md)保留固定 base／head 原始碼，最小重現兩項程式失敗，列出三項設定、三項 DB、四項外部資源提醒及 39 項初始待確認 Checklist。舊 v1 範例及 0.1.0 ZIP 保留。

## 功能與契約

- 三個 Repo 的入口 → relay → consumer 契約案例：基礎事件可消費；入口改名欄位後，未變更 consumer 發生 KeyError。無關 Repo 不引用為風險，循環可結束；斷開的已審查子圖被拒絕。
- 關聯 Repo checkout 在功能分支且有未提交檔案時，reader 仍讀本機預設分支固定 SHA；擷取後該分支前進也不改變本次來源。關聯來源不 fetch、不 checkout。
- 缺少預設分支、本機分支、工作區 Repo 或動態功能鏈證據時保留未完成；不猜 main／master。
- 設定、DB、FTP、SFTP、Redis、Kafka、其他服務各有新增與既有來源案例；核對定義已存在但首次使用、新部署 DDL、目標改變、不同連線的同名物件與新增帳號操作。
- 同一 Kafka Topic 已在 consumer 使用時，入口首次使用仍產生入口 Producer 權限核對；其他 Repo 的授權不能代替入口服務。
- 每項提醒需要入口全基礎版本搜尋與最終來源證據，Checklist 綁定同次版本、依賴編號與連線識別。初始一律待確認，不能由草稿預填已完成；人工改清單不改主報告與正文摘要。
- 保留機密遮蔽、SHA／行號／Repo 核對、完整變更覆蓋、成功／失敗暫存清理及 staged／unstaged／untracked 隔離。

## 已安裝版

- 已更新 `C:/Users/denni/.codex/skills/release-reviewer` 為 0.2.0，14 個檔案與獨立封裝逐檔一致；更新前核對原始 0.1.0，9 個原檔的精確 bytes 與雜湊備份在本機 `.tmp/local-skill-update-0.2.0/`，未刪除其他使用者內容。
- 已安裝版 `quick_validate.py` 通過；對本機 ReleaseReviewer `main`／`main` 做冒煙檢查，得到「沒有差異」，主報告與 Checklist 均產生於本機測試暫存目錄，owned context 已清理，Git index 不變。

## GitlabWorkSpace 整合

- `npm.cmd run test:unit`：272 項通過；含三輸入、舊表單移轉、預設分支情境及 MergeReviewer 選項保留。
- `npm.cmd run test:behavior`：63 項畫面測試、40 個行為情境通過；編譯與 Webview 型別檢查通過。
- 整包回歸已通過 26 項發行工具、18 項舊工具安裝器、11 項整包安裝器、20 項交易／回復、7 項 profile、6 項 ReleaseReviewer、21 項 AutoHook、1 項已安裝 Group 工作流程、88 項 Megin Group、10 項 MergeReviewer 測試，及三個需求探索回歸腳本。
- 最終固定來源更新後，6 項 ReleaseReviewer 整合再次通過：兩種 profile 的報告與 Checklist、本機修改隔離、預設分支版本、逐檔封裝一致、未知修改拒絕，以及 0.19.1 升級／回復。
- 完整 `test:release` 初次執行在最後的交付證據測試載入失敗，原因是舊測試 helper 硬編碼 MergeReviewer 0.9.0，缺少目前 0.10.0 的 legacy adapter。已改為從實際 Group 封裝讀取並核對逐檔雜湊；未改 MergeReviewer 行為。最後一組獨立重跑，26 項交付證據測試通過。
- 已產生 [獨立 Skill ZIP](../dist/release-reviewer-0.2.0.zip)、[VSIX](../../GitlabWorkSpace/dist/gitlab-workspace-0.20.0.vsix) 與 [整包 ZIP](../../GitlabWorkSpace/dist/gitlab-workspace-kit-0.20.0.zip)。`verify-vsix.py` 通過，VSIX 內 TAR.XZ 與整包 ZIP 的兩種 profile 逐檔一致，SHA256SUMS 有效；舊發行包保留。

## 驗證界線

上述程式案例與報告由本次實作者檢視；測試驗證固定證據、報告契約、鏈路結構與核對項，並非自動語意分析或獨立模型盲測。未對使用者正式業務 Group 做人工驗收，也未連線 DB、FTP、SFTP、Redis、Kafka 或目標環境。正式功能鏈的語意審查品質仍需以實際業務案例驗收。

環境準備一律交由人工 Checklist 核對，僅有定義、送出工單或靜態檢查完成不代表已可上板。
