# Release Reviewer｜上板檢查

獨立 Codex Skill，使用 Repo、基礎分支與比較分支檢查：

1. 本次變更與合併後行為的具體程式風險。
2. 新增使用的設定、環境變數與設定檔路徑，及版本內是否有對應定義。
3. 新增使用的 DB 連線、資料庫、資料表、欄位、View 與預存程序。

新增判斷以基礎版本整個 Repo 的原有程式使用為準。DB 清單是上板提醒，環境是否已建立或套用未查證。預設只審查已提交內容，保留本機修改，輸出繁體中文 Markdown。

## 安裝與使用

從 [GitHub Releases](https://github.com/dennis8499/ReleaseReviewer/releases/latest) 下載 `release-reviewer-0.1.0.zip` 與對應的 `.sha256` 校驗檔。

需要 Python 3.10+、Git，以及可使用本機 Skills 的 Codex。合併前模式需要支援 merge-tree --write-tree 與 --merge-base 的 Git；能力不足會回報預覽缺口，明確指定直接比較可略過預覽。

將 skills/release-reviewer 複製到 Codex 的 Skills 目錄，或將發行 ZIP 解壓至該目錄。CODEX_HOME 有設定時使用其 skills 子目錄，否則使用使用者的 .codex/skills。

~~~text
$release-reviewer 專案名稱=OrderService 基礎分支=origin/main 比較分支=origin/feature/payment
$release-reviewer Repo=C:\projects\OrderService 基礎分支=v1.0.0 比較分支=v1.1.0 比較模式=直接比較 輸出JSON
~~~

短分支名代表本機分支；origin/main 等遠端輸入會取得該分支最新版本。版本無法解析或遠端取得失敗時停止，不使用過期資料。預設不連 GitLab API、實際環境或 DB。

報告預設存於所選 Repo 的 review-reports，並有獨立 release-review 檔名前綴。報告產生後清理本次暫存證據。程式、設定與 DB 的語意由執行 Skill 的 Codex 審查；Python helpers 負責固定版本、搜尋、引用及輸出驗證，不是完整的自動語意分析器。

可先閱讀[示範報告](docs/demo/report.md)、[示範案例說明](docs/demo/README.md)及[驗證紀錄](docs/verification.md)。

## 開發驗證與打包

在此目錄執行：

~~~powershell
python -X utf8 -B -m unittest discover -s tests -v
python -X utf8 -B scripts/package_release.py
~~~

打包輸出 dist/release-reviewer-0.1.0.zip 與 SHA-256 檔，ZIP 只有可安裝的 release-reviewer 目錄。測試不需要第三方 Python 套件。Skill frontmatter 可另用 skill-creator 的 quick_validate.py 驗證。

GitHub Actions 在 Linux、Windows 的 Python 3.10、3.14 執行相同測試與打包檢查。

Git primitives 與 owned-session 邏輯擷取自本機 MergeReviewer 0.8.0（commit efabb07f054b0dfe0a3ba1f5f8cd9f5aa9438f6a），必要程式已內附；沒有對原專案的執行期依賴。獨立試用完成後，再另行整合 GitlabWorkSpace。
