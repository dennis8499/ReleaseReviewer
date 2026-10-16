# Release Report 輸出風格

輸出前確認目前選定 Skills root 是否有 `output-style/SKILL.md`。已安裝時先載入
`$output-style`，由它依任務或使用者明確選擇的模式取代以下通用呈現規則；
報告契約、Checklist、固定判斷及證據要求仍完整保留。未安裝時沿用下列既有規則。

## 原有呈現規則（僅未安裝 Output Style 時）

本規則適用於繁體中文進度、問答及新增或更新的人類可讀文件，沿用
[Claude Code Concise](https://code.claude.com/docs/en/output-styles#concise) 的結果先行原則。

- 首句回答問題或陳述目前結果，省略逐步旁白與收尾重述。
- 簡單問答預設一至三句；要求解釋時完整說明，正式文件不限句數。
- 在既有摘要或首個敘述段落給重點，保留必要章節、欄位、Checklist、證據及原始紀錄。
- 完整保留阻擋、待確認項、覆蓋缺口、限制、錯誤、失敗測試輸出、安全警告及破壞性操作確認。

## 報告契約（所有模式）

報告先交代產生時上版判斷與阻擋／待確認／缺口數，再列已確認阻擋、新增依賴與
Checklist、限制；完整 SHA、搜尋、功能鏈、覆蓋與執行紀錄放可展開證據區。
機器欄位、命令、原始輸出及人工回覆保持原值。

## Claude Code 設定

本節僅適用於未安裝 Output Style 的原有呈現規則。只有在 Claude Code 中套用本規範或處理其設定問題時讀取本節。Codex 直接遵循上面的
寫作規則，不需要變更其他工具的設定。

Claude Code 使用者可選內建 `Concise`（v2.1.237 以上）：在 `/config` → `Output style`
選擇 `Concise`；v2.1.269 以上亦可用 `/output-style concise`。如需保存專案設定，在既有
`.claude/settings.local.json` 加入 `"outputStyle": "Concise"`，保留其他設定並維持大小寫。
這是使用指引；只有使用者要求設定變更時才修改該檔案。
