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
  {"action":"read","source":"merge-preview","path":"src/service.cs","line_start":18,"line_end":24}
]
~~~

~~~powershell
python -X utf8 -B "<skill-dir>/scripts/evidence.py" batch --context-dir "<context_dir>" --requests "<context_dir>/followup-requests.json" --auto-content
~~~

每筆沿用 `list`／`read`／`search` 與 `source`、`repo`、`ref`、`path`、`query`、`ignore_case`；省略 source／repo 分別為 base／entry。merge-parent 必須帶固定 parent 的完整 ref。requests 與草稿檔案接受 UTF-8（含 BOM）。回傳 `results` 按原順序保存 index、ok 與 result 或 error，`complete` 只有全部成功才是 true。任一失敗 exit 2，其他結果仍保留；必須處理失敗項，不可把它當零命中。

read 可指定 `line_start`／`line_end`，須成對且皆為整數，從 1 起算並包含兩端，滿足 `1 <= line_start <= line_end <= 全文行數`；布林、null、缺一端、顛倒或超界均報錯。批次僅將非法項目記為 error，後續項目繼續。單筆 CLI 使用 `--line-start`／`--line-end`。不指定行段時維持原本全文輸出，沒有新增欄位。

只有切片結果新增 `content_sha256`（原始全文 bytes 的 SHA-256）、`line_start`、`line_end`、`total_lines`，並保留原始行號。即使用 shared／auto，切片仍 inline 回傳 `lines`，不以全文 `content_id` 代替。以下假設固定檔共有 40 行：

~~~json
{"results":[{"index":0,"ok":true,"result":{"repo":"entry","source":"merge-preview","ref":"<固定預覽 tree SHA>","path":"src/service.cs","content_sha256":"<全文 SHA-256>","line_start":18,"line_end":19,"total_lines":40,"lines":[{"line":18,"text":"var orderId = request.OrderId;"},{"line":19,"text":"return service.Find(orderId);"}]}}],"complete":true}
~~~

`--shared-content` 僅適用於 batch，回傳 `schema=ReleaseReviewEvidenceBatch/v1` 與 `contents`。成功的全文 read 保留自己的 `repo`、`source`、`ref`、`path`，以 `content_id` 連到 `contents[content_id].lines`；行號與完整文字沿用原格式。同一份原始 bytes 只解碼及輸出一次；每個來源仍獨立驗證及審查，coverage 引用保留各來源。`content_id` 是原始 bytes 的 SHA-256，不以 UTF-8 replacement 解碼後的文字判斷相同。切片維持上方 inline 格式。list／search、搜尋紀錄、錯誤及結束碼保持原樣。省略旗標時仍回傳原本每筆 read 內含 lines 的 batch；無行段的單筆 reader 不變。

`--auto-content` 與 `--shared-content` 互斥，僅適用於 batch。證據執行一次，再比較上述 normal／shared 格式的 compact UTF-8 JSON bytes，選較小者，相等選 normal。收到 `contents` 時照共享格式取全文，否則直接讀各筆 `lines`；結果、搜尋 ID、canonical ledger 與錯誤退出碼均相同。短檔或無重複內容可回傳 normal，不能假定啟用 auto 就一定有 `contents`。

搜尋仍針對固定版本整個 Repo；完整命中保存於 owned context，成功項回傳自己的 `search_id`。同一紀錄可用於多個依賴的 baseline_searches，但各項 baseline_reason 仍須說明實際使用、定義、別名與存取語意。補充搜尋與內容讀取依新線索執行，不能只靠字串命中決定新增或無關。

單筆 CLI 保持可用，例如：

~~~powershell
python -X utf8 -B "<skill-dir>/scripts/evidence.py" read --context-dir "<context_dir>" --source merge-preview --path "src/service.cs"
python -X utf8 -B "<skill-dir>/scripts/evidence.py" read --context-dir "<context_dir>" --source merge-preview --path "src/service.cs" --line-start 18 --line-end 24
python -X utf8 -B "<skill-dir>/scripts/evidence.py" search --context-dir "<context_dir>" --source base --query "BillingDb" --ignore-case
~~~

## 搜尋 artifact 與篩選續讀

加 `--page-size` 時，stdout 是 `ReviewEvidenceView/v1` wrapper；批次項目在 `result.results[]`，搜尋 ID 在每項 `result.search_id`。例如以下保留第 0 項搜尋與第 1 項錯誤；ID 是 64 位小寫 hex，實際操作須取工具回傳值：

~~~json
{"schema":"ReviewEvidenceView/v1","artifact":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","artifact_path":"<context>/views/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.json","result":{"results":[{"index":0,"ok":true,"result":{"repo":"entry","source":"base","ref":"<固定完整提交 SHA>","query":"BillingDb","ignore_case":true,"scope":"repo","matches":[],"search_id":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","view":{"total_matches":0,"offset":0,"next_offset":null,"view_complete":true,"evidence_complete":true}}},{"index":1,"ok":false,"error":"<實際項目錯誤>"}],"complete":false}}
~~~

`artifact_path` 的檔案保存完整原始結果，頂層直接是 `results`／`complete`，沒有 stdout 的 `schema`／`artifact`／`result` wrapper 或分頁 `view`；若使用 shared 格式，原始結果本身另有 `ReleaseReviewEvidenceBatch/v1` 的 `schema`／`contents`。單筆搜尋 artifact 則直接是含 `search_id`／`matches` 的搜尋結果。例如上述 normal batch 的原始檔案形狀：

~~~json
{"results":[{"index":0,"ok":true,"result":{"repo":"entry","source":"base","ref":"<固定完整提交 SHA>","query":"BillingDb","ignore_case":true,"scope":"repo","matches":[],"search_id":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}},{"index":1,"ok":false,"error":"<實際項目錯誤>"}],"complete":false}
~~~

以原 artifact 續讀，搜尋 ID 可重複指定：

~~~powershell
python -X utf8 -B "<skill-dir>/scripts/evidence.py" view --context-dir "<context_dir>" --artifact "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" --search-id "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" --page-size 100 --offset 0
~~~

篩選只選 batch 的 `results[].result.search_id` 或單筆搜尋 artifact。同一 ID 對應多個 batch index 時全部保留，維持原始順序及 index；重複指定 ID 不重複追加項目。未知 ID、非法 ID 或非搜尋 artifact 報錯。先篩選再對 matches 分頁，batch 的所有 error 項及原 `complete` 都保留，未被引用的 `contents` 只從回傳視圖移除。原 artifact ID／path／hash、完整檔案與 `searches/<id>.json` 搜尋紀錄不改；未指定 ID 沿用原本 view 格式。

快取只存在單次操作內，跨命令重新驗證固定來源；發佈前仍核對搜尋紀錄與原始版本。單次發佈驗證共用相同內容及路徑分類的整檔快照分析，引用仍逐筆核對來源與行號；私鑰、設定值及認證遮蔽規則相同。diff.patch／release.patch 維持既有生成條件。由同一主流程依序落盤，所有證據操作結束後才發佈或清理 context。


## Session windows and efficient request files

When `features.read_window` is true, the session facade accepts `read-window` with `line_start >= 1` and `line_count` from 1 through 1000. Otherwise keep using strict `read`. It returns the actual line range, total lines, and a next-window cursor; a request beyond EOF returns an empty window. Keep legacy `read` bounds strict. When `features.literal_search_batch` is true, consecutive case-sensitive literal searches for the same Repo/source/ref can share a Git grep call while retaining one result and search ID per query.

Write every request file as a UTF-8 JSON array, including a single operation, then create it and call `step` in the same shell invocation. See [session workflow](session-workflow.md) for the ready-to-run PowerShell form, revisioned draft updates, read-only status/view, and recovery behavior.
