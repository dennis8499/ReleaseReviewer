# 新增依賴與上板核對

對照入口 base_sha 整個 Repo 與最終預覽，辨識 first-use、resource-definition、target-change、access-change。搜尋需沿設定載入、環境變數、包裝 client、ORM mapping、SQL fragment、部署 manifest／IaC、呼叫與帳號用途查證。只有定義、測試、註解或範例不等於既有正式程式使用；此次新增會套用的部署定義本身仍需提醒。

既有設定鍵改指新 DB／主機、同表多用新欄位、新 Topic／Consumer Group、新 Redis DB／prefix／command、新 FTP 目錄或存取操作，皆可能引入新上板準備。等價搬檔、改名、其他檔案已使用同一資源且同一存取需求不重複提醒。不同 DB／schema、服務身分或目標不可錯誤合併。

| 類型 | 查證重點 | 人工核對 |
| --- | --- | --- |
| 設定 | 必要性、預設值、檔案路徑、部署注入與載入程式 | 確認目標環境提供設定，路徑與預設行為適用 |
| DB | 連線／DB／schema／Table／欄位／View／procedure，實際操作 | 確認建立／套用、migration 順序、鎖與回填、新舊相容性、服務帳號權限 |
| FTP | 主機邏輯識別、連接埠、模式、帳號用途、目錄及操作 | 防火牆與資料通道、認證、目錄存在與讀寫／搬移／刪除權限 |
| SFTP | 帳號／金鑰來源、主機金鑰、目錄與操作 | 防火牆、認證部署、主機金鑰核對、目錄及檔案權限 |
| Redis | DB、key prefix／channel、命令、TTL、序列化 | 網路、認證與 ACL、隔離、保留策略、新舊相容性及必要初始化 |
| Kafka | Topic、Producer／Consumer／Group、事件 key／schema、offset | Broker 網路與認證、Topic 配置、ACL、重送與冪等、新舊事件相容性 |
| 其他 | 已知的服務與資源識別、實際操作、契約 | 網路／DNS／TLS、建立資源、帳號權限及契約；未知部分列限制 |

表中網路、DNS、TLS 與初始化等依實際條件核對，不適用由人工填理由。helpers 為每個提醒產生固定必要問題，不以「已有設定」或「其他服務已可用」自動勾完成。

每項提醒至少一個入口 base 搜尋紀錄及 baseline_reason，說明如何排除既有使用與別名；最終證據引用實際使用、目標變更或此次部署定義。config_changes 與 db_changes 沿用主規則，外部類型使用 external_changes。動態目標無法辨識時保留已知邏輯名稱及 resolution=unknown，不猜主機、DB、Topic 或 key。

上板 Checklist 初始狀態一律待確認。已確認必須記錄核對人、日期、開通／套用後的驗證證據；僅有申請編號或排程仍是待確認。未通過表示目前條件不符，不適用須具體理由。這是人工記錄，helpers 不連線實際環境，也不把主報告靜態完成轉成部署核准。
