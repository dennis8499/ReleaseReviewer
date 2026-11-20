Use each optional operation only when its `capabilities.features` flag is true. If `status_summary` is false use regular `status`; if `read_window` is false keep strict `read`; if `updates_file` is false use the supported draft workflow.

# 快速續跑

Workbench 複製的新審查提示會帶一個新的 `request_id`。沿用它重試同一份審查；讀取 `status --summary` 後才決定恢復點。重新同步時使用新 ID，避免把新 capture 混入舊證據。

## 批次證據操作

一次 shell 呼叫先寫 UTF-8 JSON 陣列，再執行 step。即使只有一筆操作也保留 `[]`：

```powershell
$requestsFile = Join-Path $context "round-2.json"
$json = @'
[
  {"id":"billing-base","op":"search","source":"base","query":"BillingDb"},
  {"id":"service-window","op":"read-window","source":"head","path":"src/service.py","line_start":1,"line_count":80}
]
'@
[System.IO.File]::WriteAllText($requestsFile, $json, [System.Text.UTF8Encoding]::new($false))
python -X utf8 -B $script step --runtime-root $runtime --request-id $requestId --requests-file $requestsFile --response-format auto
```

不要將大型 requests、草稿或 Base64 內容放在命令列。`read` 的嚴格行範圍維持原契約；`read-window` 用起始行和筆數，回傳實際行段、全文行數與下一段游標，檔尾會自動截短。只批次連續、同 Repo／source／ref 的大小寫敏感 literal searches；大小寫忽略查詢沿用逐筆搜尋。

## 保存判斷與進度

`ReleaseReviewUpdates/v1` 的更新檔包含 request ID、預期 revision、update ID、draft JSON pointer 更新及進度：

```json
{
  "schema": "ReleaseReviewUpdates/v1",
  "request_id": "<本次 32 位 request ID>",
  "expected_revision": 0,
  "update_id": "coverage-service-1",
  "draft_updates": [
    {"pointer":"/coverage/0/status","value":"reviewed"},
    {"pointer":"/coverage/0/reason","value":"已讀取固定 head 與 merge-preview 行為。"}
  ],
  "progress": {
    "read_evidence_ids": ["<已讀的 artifact 或 search ID>"],
    "unread_cursor": {"artifact":"<artifact ID>","pointer":"/operations","offset":100},
    "excluded_questions": ["沒有變更的既有設定不屬於新增依賴"],
    "next_action": "追查 service.py 的呼叫端"
  }
}
```

將更新檔存成 UTF-8，再與下一批 `--requests-file` 一起傳給 step。草稿先原子保存；後續證據操作失敗時保留已提交判斷。相同 update ID 和內容可安全重試；內容改變須提高 revision 並使用新 ID。Revision 衝突時先讀狀態並合併修改，不可覆寫較新的草稿。`read_evidence_ids` 只記已讀證據；只有明確語意判斷可把 draft 項目標記為 reviewed。

## 唯讀恢復

`managed_paging` 可用時，`step --response-format auto` 的大型回應及 `status --summary` 會提供 `paging.paging_requests_file`、待讀數與已確認數。把固定檔交給唯讀 `view --requests-file`，有 `next_index` 時沿同一檔加 `--offset` 批次續讀。讀完後，把對應 ID 合併寫入 `ReleaseReviewUpdates/v1` 頂層的 `paging_acknowledgements`（每次最多 100 筆），並可同時提交草稿判斷。用當前 revision 執行 `step --updates-file ... --response-format auto`，再用回傳的新續頁檔處理子頁面。回應遺失時重取原頁面，或以相同 update ID 重送原更新檔。確認只代表頁面已讀。

```powershell
python -X utf8 -B $script view --runtime-root $runtime --request-id $requestId --requests-file $pagingFile
python -X utf8 -B $script step --runtime-root $runtime --request-id $requestId --updates-file $updatesFile --response-format auto
```

支援 `features.read_only_batch_view` 時，把最多 100 筆已保存 artifact 的續讀放進同一 UTF-8 JSON 陣列；每筆保留自己的 `id`、pointer 與游標：

```json
[
  {"id":"diff-page","op":"view","artifact":"<artifact SHA-256>","pointer":"/operations","offset":0,"page_size":40},
  {"id":"source-window","op":"view","artifact":"<另一個 artifact SHA-256>","pointer":"/lines","offset":80,"page_size":80}
]
```

```powershell
python -X utf8 -B $script view --runtime-root $runtime --request-id $requestId --requests-file "<views.json>" --offset 0
```

回應為 `ReleaseReviewViewBatch/v1`，整體含換行不超過 24 KiB。`next_index` 是陣列中尚未回傳的第一筆，下次以同一檔案及該 `--offset` 續跑；比對 `requests_sha256` 確認清單未變。每筆 `result` 內的 `continue`、`fields_continue`、`next_page` 仍要各自讀完。Batch 的 `complete` 只代表清單已回傳，不代表頁面或語意審查完成。把剩餘 batch index 和各頁游標存入 progress 的 `unread_cursor`，同時保存已完成判斷；恢復時只讀待讀項目。舊 helper 繼續使用下方單頁命令。

```powershell
python -X utf8 -B $script status --summary --runtime-root $runtime --request-id $requestId
python -X utf8 -B $script view --runtime-root $runtime --request-id $requestId --artifact $artifact --pointer "/operations" --offset 0 --page-size 100
```

摘要的 `continue` 先從起點取得未回傳的第一頁。第一頁若另有大型欄位的 `continue`、`fields_continue` 或後續 `next_page`，分別讀取每個游標，直到涵蓋內容與所有兄弟項目。`view` 和 `status --summary` 是唯讀查詢，不跑 Git、不寫 artifact 或 manifest。舊 helper 沒宣告的功能不要使用；依 `capabilities.features` 逐項選用，新舊 helper 不相容時保留原 request 並回報具體欄位。

## 修稿

用同一個 `check` 的欄位錯誤、`expected_ref` 與 `diff_root_candidates` 合併修正。選差異起點前逐一核對引用與風險的因果關係。收到 `repair_stalled` 且可用證據未變時，保留該項待補證據及草稿，交代誰能提供什麼資料。取得新證據後從原 request 接續。其餘獨立項目可繼續審查。
