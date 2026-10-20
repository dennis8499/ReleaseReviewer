# 操作與負載

`--summary-v2` 回傳 `ReleaseReviewCaptureSummary/v2`，以固定 ref、tree 及檔案數取代完整 catalog inventories；完整 manifest／樹清單留在 owned context。舊 `--summary` 與預設輸出保持原格式。

既有 `batch --auto-content` 保留逐來源身分、完整全文及錯誤。搜尋批次加 `--page-size 100` 得 `ReviewEvidenceView/v1`：`artifact` 是完整輸出的 SHA-256；每筆 `matches` 的 `view` 提供 `total_matches`、`next_offset`、`view_complete`。以固定 artifact 續讀，不重新搜尋：

```powershell
python -X utf8 -B "<skill-dir>/scripts/evidence.py" view --context-dir "<context>" --artifact "<artifact>" --page-size 100 --offset 100
```

`evidence_complete=true` 表示完整輸出已保存，不能視為語意審查完成。`searches/<id>.json` 仍保存全量命中；發布仍核對固定版本及搜尋紀錄。必要命中尚未判斷時維持未完成。

新 context 的 `merge_parent_policy=necessary-merge-parents/v1` 只要求與有效最終差異路徑相關的 parent views。草稿 `merge_parent_review` 每筆含 `id`、`status`、`reason`、`evidence`；reviewed 引用最終固定版本，parent 有文字內容時另引用該 parent。not-reviewed 保留原因並維持未完成。無有效差異可空集合；最終已修復的歷史問題不列阻擋。

初始 manifest 使用 writer lease 與 `generation`，不可直接改寫固定 ledger。Git 網路同步則與工作台／另一 Reviewer 共用 `git-network.lock`，格式為 `GitLabWorkspaceLease/v1`。Runtime 使用 `GITLAB_WORKSPACE_RUNTIME_DIR`，否則 Windows `%LOCALAPPDATA%/GitLabWorkspace/runtime-v1`；POSIX 為 `$XDG_RUNTIME_DIR/gitlab-workspace/runtime-v1` 或 `~/.cache/gitlab-workspace/runtime-v1`。

每個獨立請求保持自己的同步時機，不跨請求重用 fetch。Git supervisor 及子程序樹關閉後才按 nonce 釋放；中斷或狀態不確定時隔離並保留鎖，依診斷人工查明，不按 PID／時間自動回收。此配額只約束共用 runtime 的使用者，不代表整台 GitLab 的容量保證。
