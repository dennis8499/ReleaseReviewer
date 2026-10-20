# 0.6.0 本機候選驗證

本輪新增跨語言 Git 程序樹配額、精確 fetch 防護與前置檢查、作者文字 passwd／PEM 遮蔽、選用 v2 小型 catalog／完整搜尋分頁、manifest generation 與必要 merge-parent 完成對帳。既有 CLI 預設、v3 證據及單一報告保持相容。文字打包統一 LF，二進位附件保持 exact bytes。

`tests/create_quality_fixture.py` 建立 p0、p1、safe、net-zero、gap、secrets 六種 source-only 工作區；oracle 留在工作區外，不能交給模型。來源可執行驗證與 helper 測試不等於模型語意驗收。

實際模型比較沿用既有 `gpt-6-luna / max` 驗收設定，不更改產品執行模型。以相同來源、任務、工具、預算比較無技能 prose、初始 HEAD 0.5.0、候選 0.6.0；關鍵案例各三個 fresh 盲測，保留全部失敗、修稿及重取成本。嚴重漏報／無根據阻擋、假完成、真缺口消失與合成機密外洩均不通過；按根因及必要證據判斷，不要求固定問題數或 Checklist 拆項粒度。

Helper normal／large 各五次交替重播，使用本輪初始 HEAD；只正規化擷取身分、generation 與空的 parent ledger，固定來源、引用、覆蓋、非空必要 parent 義務、問題及報告保持等價。報告端到端、helper、輸出 bytes 與 token 計量分開；取得不到 tokens 時填 null。

本輪證據留在 `.tmp/reviewer-runtime-*`。既有 performance.md 的舊樣本與 A/B 原始資料保留，不能代替現行候選結果。新模型盲測未完成前不宣稱語意品質已驗收；本機版本與 ZIP 不代表發布或正式上版核准。
