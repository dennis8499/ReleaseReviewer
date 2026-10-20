# 固定證據批次操作

先確認 manifest 的 `snapshot_policy=frozen-start/v1` 與 `capture_completed_at`。所有 base／head／merge-parent／preview／supporting 內容均從 owned `repos/<id>/snapshot.git` 讀取，缺物件即明列失敗；來源 Repo 的新提交、移動或 Git 物件清理不改變已擷取內容。

適用指示也已固定：`workspace_instructions` 每筆 `file` 是 context 內可直接讀取的文字檔，`origin` 只供辨識適用路徑。各 Repo 的 `instructions` 列固定 ref／path／SHA-256；使用對應 source/ref 的 evidence reader 讀取該 AGENTS.md，遵循適用指示並保留來源身分。無須重讀可變工作檔。

擷取後先讀 `review-worklist.json`：`requests` 是去重後的文字待讀清單；`metadata_only` 保留二進位／submodule；`required_coverage` 列逐路徑必要來源；`merge_parent_views` 保留全部 parent 比較。清單不會自動宣稱已審查，也不含尚待語意追查的所有呼叫與契約。

初始批次直接傳入工作清單。後續批次在 context 寫一個 JSON 陣列，例：

請求檔與草稿明確以 UTF-8 讀寫；PowerShell 中文管線的編碼設定見技能入口。

~~~json
[
  {"action":"search","source":"base","query":"BillingDb","ignore_case":true},
  {"action":"search","source":"supporting","repo":"repo-001","query":"invoice.created"},
  {"action":"read","source":"merge-preview","path":"src/service.cs"}
]
~~~

~~~powershell
python -X utf8 -B "<skill-dir>/scripts/evidence.py" batch --context-dir "<context_dir>" --requests "<context_dir>/followup-requests.json" --auto-content
~~~

每筆沿用 `list`／`read`／`search` 與 `source`、`repo`、`ref`、`path`、`query`、`ignore_case`；省略 source／repo 分別為 base／entry。merge-parent 必須帶固定 parent 的完整 ref。requests 與草稿檔案接受 UTF-8（含 BOM）。回傳 `results` 按原順序保存 index、ok 與 result 或 error，`complete` 只有全部成功才是 true。任一失敗 exit 2，其他結果仍保留；必須處理失敗項，不可把它當零命中。

`--shared-content` 僅適用於 batch，回傳 `schema=ReleaseReviewEvidenceBatch/v1` 與 `contents`。成功 read 保留自己的 `repo`、`source`、`ref`、`path`，以 `content_id` 連到 `contents[content_id].lines`；行號與完整文字沿用原格式。同一份原始 bytes 只解碼及輸出一次；每個來源仍獨立驗證及審查，coverage 引用保留各來源。`content_id` 是原始 bytes 的 SHA-256，不以 UTF-8 replacement 解碼後的文字判斷相同。list／search、搜尋紀錄、錯誤及結束碼保持原樣。省略旗標時仍回傳原本每筆 read 內含 lines 的 batch；單筆 reader 不變。

`--auto-content` 與 `--shared-content` 互斥，僅適用於 batch。證據執行一次，再比較上述 normal／shared 格式的 compact UTF-8 JSON bytes，選較小者，相等選 normal。收到 `contents` 時照共享格式取全文，否則直接讀各筆 `lines`；結果、搜尋 ID、canonical ledger 與錯誤退出碼均相同。短檔或無重複內容可回傳 normal，不能假定啟用 auto 就一定有 `contents`。

搜尋仍針對固定版本整個 Repo；完整命中保存於 owned context，成功項回傳自己的 `search_id`。同一紀錄可用於多個依賴的 baseline_searches，但各項 baseline_reason 仍須說明實際使用、定義、別名與存取語意。補充搜尋與內容讀取依新線索執行，不能只靠字串命中決定新增或無關。

單筆 CLI 保持可用，例如：

~~~powershell
python -X utf8 -B "<skill-dir>/scripts/evidence.py" read --context-dir "<context_dir>" --source merge-preview --path "src/service.cs"
python -X utf8 -B "<skill-dir>/scripts/evidence.py" search --context-dir "<context_dir>" --source base --query "BillingDb" --ignore-case
~~~

快取只存在單次操作內，跨命令重新驗證固定來源；發佈前仍核對搜尋紀錄與原始版本。單次發佈驗證共用相同內容及路徑分類的整檔快照分析，引用仍逐筆核對來源與行號；私鑰、設定值及認證遮蔽規則相同。diff.patch／release.patch 維持既有生成條件。由同一主流程依序落盤，所有證據操作結束後才發佈或清理 context。
