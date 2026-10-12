# Group 功能鏈審查

只比較入口 Repo 的基礎／比較分支；其他 Repo 是上下游固定來源，不同時審查其功能分支差異。catalog 為本次可檢查範圍：關聯 Repo 一律使用各自本機預設分支的固定 SHA，不 fetch、不 checkout、不混用工作檔。workspace-project 優先於本機 remote HEAD；無法確定名稱、缺少本機分支或 Repo 時保留缺口。

從入口 base 與最終預覽的變更找出功能入口、API 呼叫／回應、事件 Topic／payload、共用契約、DB／快取讀寫，搜尋所有關聯來源，沿實際上下游繼續追查。至少確認處理、錯誤／重送路徑、授權、結果與新舊版本相容性。被移除的行為也要追查既有消費端。用已訪問的 Repo／契約／路徑集合結束循環，不能因遇到回呼就省略後續結果。

Wiki／文件只供導覽，引用須回到固定程式或部署來源。找不到對應 Repo、動態服務發現或外部契約不明時寫 chain_gaps，不能宣告完整鏈路。來源已查且確定無關的 Repo 要保留判定理由，不能只以單次零命中略過。

## 草稿欄位

supporting_coverage 必須涵蓋 catalog 的每個 supporting Repo，恰一筆：

~~~json
{"repo":"repo-001","status":"reviewed","reason":"事件消費端已追查到結果與錯誤處理。","searches":["實際 supporting 搜尋的 search_id"],"evidence":[{"repo":"repo-001","source":"supporting","ref":"固定 SHA","path":"src/consumer.cs","line_start":10,"line_end":20}]}
~~~

status 為 reviewed、unrelated、not-reviewed。reviewed 須有內容證據；reviewed／unrelated 均須 supporting 全 Repo 搜尋紀錄，reason 說明語意判斷。版本未取得只能 not-reviewed，填具體原因並保留未完成。不能拿另一個 Repo 的同名檔案或入口 base 搜尋代替。

functional_chain 每筆表示實際功能關係，編號 F-001 起，kind 為 api、event、contract、database、cache、other：

~~~json
{"id":"F-001","from_repo":"entry","to_repo":"repo-001","kind":"event","status":"reviewed","description":"入口發布 invoice.created，既有消費端讀取 amount。","evidence":[{"repo":"entry","source":"merge-preview","ref":"預覽 tree SHA","path":"src/producer.cs","line_start":10,"line_end":15},{"repo":"repo-001","source":"supporting","ref":"固定 SHA","path":"src/consumer.cs","line_start":10,"line_end":20}]}
~~~

reviewed 的每條關係須附兩端固定證據；涉及入口時要有最終版本。related Repo 必須能經關係連回入口；循環可存在，但不能產生斷開且宣稱已查完的子圖。未完成關係用 not-reviewed 加 reason，其他無法定位的缺口寫 chain_gaps。語意上仍需交代功能的起點、處理與結果，結構驗證不證明沒有漏掉關係。

新增資源的 baseline 仍是入口 base_sha 整個 Repo。其他服務曾使用相同資源不代表入口服務已有網路、帳號或操作權限；按實際服務身分核對，不能在 Group 內全域去重而遺漏權限。
