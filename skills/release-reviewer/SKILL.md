---
name: release-reviewer
description: Review direct pre-release blockers and new deployment dependencies for one explicit Repo and base/head branches. Produce one Traditional Chinese report with an editable dependency checklist inside the reviewed Repo; general merge review follows its own workflow.
---

# Release Reviewer｜上版檢查

針對明確 Repo、基礎分支與比較分支，使用同一個 Skill Agent 執行預設靜態 Code Review，交付一份繁體中文 Release Report。只將本次變更直接造成、最終仍可觸發的 P0／P1 列為上板阻擋；新增依賴另列於人工部署 Checklist。沿用使用者選定的模型，不呼叫 GitLab API，不把上板核准或環境狀態推定為已確認。

使用者必須提供三項輸入：**Repo、基礎分支、比較分支**。缺漏或 Repo 有歧義時先釐清。工作區邊界取自 VSCode；一次審查只選一個入口 Repo。已儲存工作區用 --workspace-file，Group 的 supporting Repo 僅能取自明確直屬 Git 根目錄或 VSCode roots。

## 1. 固定版本與證據

從入口 Repo 的 cwd 啟動 session：

~~~powershell
python -X utf8 -B "<skill-dir>/scripts/review_session.py" start --workspace "<工作區>" --project "<Repo path>" --base "<基礎分支>" --head "<比較分支>"
~~~

保存回傳的 request_id、runtime_root、context_dir、draft_path 與 files。先讀 owned workspace instructions 和所有固定 Repo 的適用 AGENTS.md 證據，再開始語意審查。從頭到尾沿用同一 request、固定 SHA、證據及搜尋紀錄；完整 capture 後才進入語意審查。

短分支名稱只解析本機分支，保持離線。只有明確的遠端 base/head 才在本機預檢成功後依 remote 精確 fetch 一次；同步失敗保留錯誤或缺口，絕不以舊 ref 替代。Supporting Repo 只固定擷取當下已提交 HEAD，不 fetch。capture 完成後，step、修稿、check、finish 和重試都不得新增網路讀取或 lazy fetch。啟動流程見[session 操作](references/session-workflow.md)；遇到修稿或錯誤再讀[續跑細節](references/session-details.md)。

## 2. 審查與依賴

先讀[審查規則及草稿欄位](references/review-rules.md)。只有 scope=group 才讀[Group 契約與功能鏈](references/group-review.md)；遇到新增或可能改變目標的資源時讀[依賴與上板 Checklist](references/resource-checks.md)。

- 覆蓋全部必要變更、最終合併結果、適用指示與必要 merge parents。刪除／改名需查舊呼叫端和最終版本；其他歷史只沿具體線索追查。未知版本、必要 caller／consumer／mapping 缺證據時保留缺口。
- P0／P1 必須由證據串起本次變更、觸發路徑、最終錯誤行為、上板影響與修正方向。只引用最小必要固定行段；不列既有缺陷、風格問題或單純缺少測試。
- 新依賴以入口 base 的整個 Repo 為基準，查首次使用、部署定義、目標及操作權限。已使用資源、未使用設定、等價搬移與其他 Repo 已使用的資源，按既有語意判斷；單次搜尋零命中不足以排除關聯。未知 provider 不推定為 Kafka。
- 對七類依賴與每個 supporting Repo 明確完成語意判斷或保留缺口。靜態檢查與實際環境確認分開；Checklist 的確認須有核對人、日期及驗證證據，不適用須寫理由。新增資源本身不自動成為程式阻擋。

先使用 capture 產生的 worklist，之後依發現的 caller、consumer、alias 和契約批次讀取／搜尋。逐筆核對 Repo、source、ref、path、SHA 及行號。大型回應依 artifact 的 JSON pointer 和 continuation 續讀；完整證據已保存不代表 reviewer 已讀完。需要完整操作與 JSON 範例時載入[續跑細節](references/session-details.md)及[證據操作](references/evidence-workflow.md)。

~~~powershell
python -X utf8 -B "<skill-dir>/scripts/review_session.py" step --runtime-root "<runtime>" --request-id "<id>" --worklist "<context>/review-worklist.json" --response-format auto
~~~

## 3. 完成草稿、核對並發布

完成 context 內的 ReleaseReviewDraft/v3 草稿。只在逐項審查後標記 reviewed；保留結構、來源、搜尋、merge-parent、依賴與 Group 契約缺口。只記非機密鍵名和邏輯識別，勿將密碼、Token、金鑰或完整連線字串寫入草稿或報告。預檢欄位與 validator 規則以[審查規則](references/review-rules.md)為準；遇到預檢錯誤，依訊息修稿，不得把缺口改成完成。

完成後依序執行：

~~~powershell
python -X utf8 -B "<skill-dir>/scripts/review_session.py" check --runtime-root "<runtime>" --request-id "<id>"
python -X utf8 -B "<skill-dir>/scripts/review_session.py" finish --runtime-root "<runtime>" --request-id "<id>"
~~~

只在入口 Repo 的 review-reports/ 產生一份含可編輯 Checklist 的報告，樣式見[報告風格](references/report-style.md)。Checklist 初始全為待確認；確認需填人、日期與實際證據，不適用需理由。程式靜態完整、尚有檢查缺口、等待人工核對上板條件、已檢查範圍未發現阻擋，須分別呈現。靜態完整不等於環境就緒、MR 驗證或核准上板。

預設不執行專案測試或部署驗證；僅在使用者明確要求時執行並記錄。exit 3 表示同一 request 可修正並續跑，exit 2 表示終止錯誤。用 status 恢復工作；修稿途中保留 context，成功、取消、終止或最終放棄時清理 owned context。完整修稿、重試及清理規則見[續跑細節](references/session-details.md)。

完成後回覆報告連結、直接阻擋與尚待核對／補查事項。