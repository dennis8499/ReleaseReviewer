# 固定證據批次操作

擷取後先讀 `review-worklist.json`：`requests` 是去重後的文字待讀清單；`metadata_only` 保留二進位／submodule；`required_coverage` 列逐路徑必要來源；`merge_parent_views` 保留全部 parent 比較。清單不會自動宣稱已審查，也不含尚待語意追查的所有呼叫與契約。

初始批次直接傳入工作清單。後續批次在 context 寫一個 JSON 陣列，例：

PowerShell 透過管線傳送含中文的 Python／JSON 前，在同一次命令設定 `$OutputEncoding = [System.Text.UTF8Encoding]::new($false)`，檔案明確以 UTF-8 寫入。這可避免中文在到達 Python 前已轉成問號；`python -X utf8` 本身不會修復已失真的輸入。

~~~json
[
  {"action":"search","source":"base","query":"BillingDb","ignore_case":true},
  {"action":"search","source":"supporting","repo":"repo-001","query":"invoice.created"},
  {"action":"read","source":"merge-preview","path":"src/service.cs"}
]
~~~

~~~powershell
python "<skill-dir>/scripts/evidence.py" batch --context-dir "<context_dir>" --requests "<context_dir>/followup-requests.json"
~~~

每筆沿用 `list`／`read`／`search` 與 `source`、`repo`、`ref`、`path`、`query`、`ignore_case`；省略 source／repo 分別為 base／entry。merge-parent 必須帶固定 parent 的完整 ref。requests 與草稿檔案接受 UTF-8（含 BOM）。回傳 `results` 按原順序保存 index、ok 與 result 或 error，`complete` 只有全部成功才是 true。任一失敗 exit 2，其他結果仍保留；必須處理失敗項，不可把它當零命中。

搜尋仍針對固定版本整個 Repo；完整命中保存於 owned context，成功項回傳自己的 `search_id`。同一紀錄可用於多個依賴的 baseline_searches，但各項 baseline_reason 仍須說明實際使用、定義、別名與存取語意。補充搜尋與內容讀取依新線索執行，不能只靠字串命中決定新增或無關。

單筆 CLI 保持可用，例如：

~~~powershell
python "<skill-dir>/scripts/evidence.py" read --context-dir "<context_dir>" --source merge-preview --path "src/service.cs"
python "<skill-dir>/scripts/evidence.py" search --context-dir "<context_dir>" --source base --query "BillingDb" --ignore-case
~~~

快取只存在單次操作內，跨命令重新驗證固定來源；發佈前仍核對搜尋紀錄與原始版本。由同一主流程依序落盤，所有證據操作結束後才發佈或清理 context。
