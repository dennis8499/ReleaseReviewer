# 操作與負載

`--summary-v2` 回傳 `ReleaseReviewCaptureSummary/v2`，以固定 ref、tree 及檔案數取代完整 catalog inventories；完整 manifest／樹清單留在 owned context。舊 `--summary` 與預設輸出保持原格式。

一次請求由主 agent capture 一次，子 agent 共用固定 context；搜尋與草稿寫入保持單一主流程依序處理。只有明確遠端 base／head 按 remote 批次同步一次，supporting Repo 使用本機 HEAD。發布 exit 3 沿保留的 context 修稿，exit 2 回報後停止，不自動重新 capture。

`network_activity` 的四項整數為 `fetch_attempt_count`（helper 嘗試呼叫 Git fetch 次數）、`fetch_success_count`（成功次數）、`requested_ref_count`（去重後要求同步的遠端分支數）、`synced_ref_count`（成功同步分支數）。同 remote 的兩個不同分支成功批次同步一次，四值依序為 `1/1/2/2`。`fetches` 是分支項目，長度不是連線次數；以上計數也不是 Git 傳輸內部的 HTTP／SSH 請求數。本機分支沒有遠端同步時四值均為 0。

既有 `batch --auto-content` 保留逐來源身分、完整全文及錯誤。搜尋批次加 `--page-size 100` 得 `ReviewEvidenceView/v1`：`artifact` 是完整輸出的 SHA-256；每筆 `matches` 的 `view` 提供 `total_matches`、`next_offset`、`view_complete`。以固定 artifact 續讀，不重新搜尋：

```powershell
python -X utf8 -B "<skill-dir>/scripts/evidence.py" view --context-dir "<context>" --artifact "<artifact>" --page-size 100 --offset 100
```

`read --line-start/--line-end` 及批次同名底線欄位提供成對行段，保留全文 hash 與原始行號；切片在 shared／auto 模式也直接回傳 `lines`。`view --search-id` 可重複指定 64 位小寫 hex 搜尋 ID，先篩選再按 matches offset 分頁；保留 batch 的錯誤與原 `complete`。精確請求、stdout wrapper 與磁碟 artifact 範例見[證據操作](evidence-workflow.md)。

`evidence_complete=true` 表示完整輸出已保存，不能視為語意審查完成。`searches/<id>.json` 仍保存全量命中；發布仍核對固定版本及搜尋紀錄。必要命中尚未判斷時維持未完成。

新 context 的 `merge_parent_policy=necessary-merge-parents/v1` 只要求與有效最終差異路徑相關的 parent views。草稿 `merge_parent_review` 每筆含 `id`、`status`、`reason`、`evidence`；reviewed 引用最終固定版本，parent 有文字內容時另引用該 parent。not-reviewed 保留原因並維持未完成。無有效差異可空集合；最終已修復的歷史問題不列阻擋。

所有新 context 另記 `coverage_policy=ancestor-noop/v1`。只有完整祖先無新增內容條件成立，agent 才能將七類依賴與全部 supporting 覆蓋明確判為「本次不適用」；保留 snapshots、原始 patches 與七類／catalog 清單，草稿不自動完成。判定、舊 context 相容及實際環境界線見[審查規則](review-rules.md#祖先分支無新增內容)。

capture 的 `ancestor_path` 由 base 沿 parent 連至 head；seal 僅附帶路徑 commit bodies，發布器從 owned objects 重驗 hash／類型／parent 連線，不讀取遠端或封存額外歷史 trees。這是新 no-op 豁免必備的結構證據，不是草稿可填的語意覆蓋來源；無政策標記的舊 v3 不要求此路徑。

初始 manifest 使用 writer lease 與 `generation`，不可直接改寫固定 ledger。Git 網路同步則與工作台／另一 Reviewer 共用 `git-network.lock`，格式為 `GitLabWorkspaceLease/v1`。Runtime 使用 `GITLAB_WORKSPACE_RUNTIME_DIR`，否則 Windows `%LOCALAPPDATA%/GitLabWorkspace/runtime-v1`；POSIX 為 `$XDG_RUNTIME_DIR/gitlab-workspace/runtime-v1` 或 `~/.cache/gitlab-workspace/runtime-v1`。

每個獨立請求保持自己的同步時機，不跨請求重用 fetch。Git supervisor 及子程序樹關閉後才按 nonce 釋放；中斷或狀態不確定時隔離並保留鎖，依診斷人工查明，不按 PID／時間自動回收。此配額只約束共用 runtime 的使用者，不代表整台 GitLab 的容量保證。
# Request sessions in 0.8.0

New requests use the independent Repo-external `.release-review-runtime/` facade described in [session operations](session-workflow.md). `check`, `status` and saved artifact views read metadata without Git; request rounds fully validate each used repository once. `finish` preserves complete owned-source validation and replays every saved search with a fresh validation-local cache. Reading caches remain inside one context. Every request independently fetches its explicit remote branches and seals its own snapshots; no cross-request fetch or pack cache is used.

The legacy commands and system-temp session rules below retain their original behavior.

新 capture 保留原本的 runtime、context 與 `repos/<id>/snapshot.git/objects/pack/` 目錄位置，將 pack 串流寫入 `snapshot.pack`，再以 owned 相對路徑建立 `snapshot.idx`。Manifest 保存 Git 物件格式對應的 pack checksum，以及 pack／index 各自完整 SHA-256；讀取與發布仍驗證固定來源、物件清單、pack／index 和搜尋 ledger。歷史 context 保留原檔名，不會遷移或重新 capture。

這避免 Git for Windows 對長 hash 檔名的限制，也保留原 Repo、全域與繼承的 Git 設定。Windows 支援範圍為 owned `snapshot.git` 根目錄不超過 220 UTF-8 bytes；更深的 runtime 會在封存前回報明確路徑限制，不改路徑、不回讀來源或宣稱完成。Git for Windows 的 `$GIT_DIR` 仍有額外長度上限，`core.longpaths` 無法解除所有限制。
