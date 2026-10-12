# Release Reviewer｜上板檢查

獨立 Codex Skill 0.2.0，對外只接受必填的 Repo、基礎分支與比較分支，固定合併前審查：

1. 本次變更與合併後行為的具體程式風險。
2. 新增使用的設定、環境變數與設定檔路徑，及版本內是否有對應定義。
3. 新增使用的 DB 連線、資料庫、資料表、欄位、View 與預存程序。
4. Group 中與入口變更相關的 API、事件、契約、DB 與快取功能鏈，包括未變更消費端。
5. FTP、SFTP、Redis、Kafka 與其他新增資源；自動產生防火牆、權限、建表／欄位／Topic 與相容性人工 Checklist。

新增判斷以入口基礎版本整個 Repo 為準，涵蓋首次使用、部署定義、目標變更及新增權限需求。關聯 Repo 固定各自本機預設分支的已提交版本，不 fetch、不混入 checkout／工作檔。缺少版本或鏈路保持檢查未完成。主報告與可填寫 Checklist 分開，靜態完成與實際環境準備分開。

## 安裝與使用

本機打包產物為 `dist/release-reviewer-0.2.0.zip` 與對應 `.sha256` 校驗檔；公開發行後也可從 GitHub Releases 取得。

需要 Python 3.10+、Git，以及可使用本機 Skills 的 Codex。Git 須支援 merge-tree --write-tree 與 --merge-base；能力不足會回報預覽缺口，不能改用直接比較略過。

將 skills/release-reviewer 複製到 Codex 的 Skills 目錄，或將發行 ZIP 解壓至該目錄。CODEX_HOME 有設定時使用其 skills 子目錄，否則使用使用者的 .codex/skills。

~~~text
$release-reviewer 專案名稱=OrderService 基礎分支=origin/main 比較分支=origin/feature/payment
$release-reviewer Repo=C:\projects\OrderService 基礎分支=main 比較分支=feature/payment
~~~

短分支名代表本機分支；origin/main 等遠端輸入會取得該分支最新版本。版本無法解析或遠端取得失敗時停止，不使用過期資料。預設不連 GitLab API、實際環境或 DB。

不接受 tag、commit、HEAD、直接比較與 JSON 選項。Group 範圍與各 Repo 預設分支名稱由目前 VSCode 工作區／工作台內部資料提供；原生使用也可由明確 Group 目錄與本機遠端 HEAD 辨識。無法辨識時不猜 main／master。

報告預設存於入口 Repo 的 review-reports，產生 release-review 主報告與 -checklist.md。Checklist 初始待確認；人工填核對人、日期及開通／套用後證據，不適用填理由。主報告保持原始證據，舊 v1 報告保留。報告成功或失敗後清理本次 owned 暫存證據。語意由執行 Skill 的 Codex 審查；Python helpers 驗證固定版本、搜尋、引用、鏈路結構、覆蓋及核對項，不是完整的自動語意分析器。

可先閱讀 [v2 示範與 Checklist](docs/demo/v2/README.md)、[歷史 v1 案例](docs/demo/README.md)及[驗證紀錄](docs/verification.md)。

## 開發驗證與打包

在此目錄執行：

~~~powershell
python -X utf8 -B -m unittest discover -s tests -v
python -X utf8 -B scripts/package_release.py
~~~

打包輸出 dist/release-reviewer-0.2.0.zip 與 SHA-256 檔，ZIP 只有可安裝的 release-reviewer 目錄，不覆寫 0.1.0 發行包。測試不需要第三方 Python 套件。Skill frontmatter 可另用 skill-creator 的 quick_validate.py 驗證。

GitHub Actions 在 Linux、Windows 的 Python 3.10、3.14 執行相同測試與打包檢查。

Git primitives 與 owned-session 邏輯原擷取自本機 MergeReviewer 0.8.0（commit efabb07f054b0dfe0a3ba1f5f8cd9f5aa9438f6a），必要程式已內附；沒有對原專案的執行期依賴。GitlabWorkSpace 整合保留相同原生 Skill 封裝。
