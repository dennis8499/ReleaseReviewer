"""Deployment questions generated for every validated new dependency."""

COMMON = [
    ("network", "確認目標主機、連接埠、DNS／路由與防火牆已開通；本機依賴可記錄不適用理由。"),
    ("transport", "確認 TLS／憑證／加密方式與目標環境相符；不適用時說明原因。"),
]
CONFIG = [
    ("configuration", "確認必要設定已提供，設定檔路徑、載入與部署注入正確，預設值適用目標環境。"),
]
DB = [
    ("schema", "確認目標 DB／schema 與所需資料表、欄位、View 或預存程序已建立或套用。"),
    ("migration", "確認 migration／DDL 執行順序、鎖定與資料回填，以及新舊程式版本相容性。"),
    ("permissions", "確認實際服務帳號具備本次操作所需 DB／物件權限；其他服務可用不代表本服務已授權。"),
]
EXTERNAL = {
    "ftp": [("passive-channel", "確認 FTP 主動／被動模式及資料通道連接埠的防火牆規則。"),
            ("authentication", "確認 FTP 帳號與憑證來源已配置且有效。"),
            ("directory", "確認目錄存在及上傳、下載、搬移、刪除所需權限。")],
    "sftp": [("authentication", "確認 SFTP 帳號／金鑰及憑證部署來源有效。"),
             ("host-key", "確認已核對 SFTP 主機金鑰及輪替安排。"),
             ("directory", "確認遠端目錄及檔案操作權限符合服務帳號需求。")],
    "redis": [("authentication", "確認 Redis 認證與服務帳號 ACL 涵蓋所需命令、key 與 channel。"),
              ("namespace", "確認 Redis DB／key prefix 隔離、TTL 與資料保留策略。"),
              ("compatibility", "確認序列化格式、新舊版本相容性及必要初始化／快取重建。")],
    "kafka": [("authentication", "確認 Kafka 認證與 Producer／Consumer／Group ACL 已開通。"),
              ("topic", "確認 Topic 已建立，partition、replication 與 retention 符合需求。"),
              ("consumer-group", "確認 Consumer Group、offset、重送與冪等處理符合上板安排。"),
              ("contract", "確認事件 key／payload／schema 與上下游新舊版本相容。")],
    "other": [("provisioning", "確認新增外部資源已建立、帳號與操作權限已開通，並核對服務契約。")],
}


def build_checklist(result: dict) -> list[dict]:
    checks = []
    for field in ("config_changes", "db_changes", "external_changes"):
        for dependency in result[field]:
            rules = CONFIG if field == "config_changes" else COMMON + (
                DB if field == "db_changes" else EXTERNAL[dependency["kind"]])
            for operation, question in rules:
                checks.append({"id": f"K-{len(checks) + 1:03}", "dependency_id": dependency["id"],
                               "resource": dependency["name"], "connection": dependency.get("connection", "入口服務設定"),
                               "operation": operation, "question": question,
                               "status": "待確認", "owner": "", "date": "", "evidence": "", "reason": ""})
    return checks
