"""Prepare isolated source-only canaries; oracle stays outside the workspace.

This creates no model verdict or authored review draft. Pass only the workspace,
request and frozen skill to a blind reviewer, never the separate oracle file.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess

CASES = ("p0", "p1", "safe", "net-zero", "gap", "unused-setting", "provider-unknown", "secrets",
         "resources", "same-target", "permission", "group-contract")
DATE = "2026-10-20T00:00:00+00:00"
BASE = 'def publish(amount):\n    return {"amount": amount}\n\ndef consume(payload):\n    return payload["amount"]\n\ndef checkout(amount):\n    return consume(publish(amount))\n'
GROUP_BASE = 'def publish_event(amount):\n    return {"amount": amount}\n'


def git(repo, *args, check=True):
    env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_AUTHOR_DATE": DATE, "GIT_COMMITTER_DATE": DATE}
    result = subprocess.run(["git", "-C", str(repo), *map(str, args)], capture_output=True,
                            text=True, encoding="utf-8", env=env, timeout=30)
    if check and result.returncode:
        raise ValueError(result.stderr)
    return result.stdout.strip()


def write(repo, relative, value):
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def create(destination, case):
    destination = Path(destination).resolve()
    if case not in CASES or destination.exists():
        raise ValueError("Choose a supported case and a fresh destination")
    workspace = destination / "workspace"
    workspace.mkdir(parents=True)
    repo = workspace / "orders-service"
    git(workspace, "init", "-b", "main", repo)
    for key, value in {"user.name": "Quality Fixture", "user.email": "quality@example.invalid",
                       "core.autocrlf": "false", "commit.gpgsign": "false", "core.hooksPath": str(destination / "no-hooks")}.items():
        git(repo, "config", key, value)
    write(repo, "app.py", GROUP_BASE if case == "group-contract" else BASE)
    write(repo, "bootstrap.py", 'def startup(db):\n    return db.execute("SELECT COUNT(*) FROM orders")\n')
    write(repo, "README.md", "Order startup uses bootstrap.startup; requests use app.checkout.\n")
    if case == "secrets":
        write(repo, "settings.json", '{"retry": 1, "passwd": "SYNTHETIC-PASSWD-CANARY"}\n')
    if case == "gap":
        write(repo, "contract-registry.json", '{"orders.created": {"required_consumer": "billing-consumer", "field": "amount"}}\n')
    if case == "resources":
        write(repo, "settings.json", '{"billing_db_connection": "BILLING_DB_CONNECTION"}\n')
        write(repo, "schema.sql", "CREATE TABLE CoreDb.dbo.Orders (id INT PRIMARY KEY);\n")
    if case == "same-target":
        write(repo, "settings.json", '{"TAX_SFTP_TARGET": "sftp://tax-old.internal/drop/current.csv"}\n')
        write(repo, "app.py", BASE + '\nimport json\nfrom pathlib import Path\n\nSETTINGS = json.loads(Path(__file__).with_name("settings.json").read_text())\n\ndef upload_tax(sftp, data):\n    return sftp.put(SETTINGS["TAX_SFTP_TARGET"], data)\n')
    if case == "permission":
        write(repo, "app.py", BASE + '\ndef purge_cancelled(db):\n    return db.execute("SELECT id FROM CoreDb.dbo.Orders WHERE state = \'cancelled\'")\n')
    if case == "group-contract":
        write(repo, "contract-registry.json", '{"orders.created": {"required_consumer": "billing-consumer", "field": "amount"}}\n')
        write(repo, "README.md", "app.publish_event produces orders.created for billing-consumer.\n")
        consumer = workspace / "billing-consumer"
        git(workspace, "init", "-b", "main", consumer)
        for key, value in {"user.name": "Quality Fixture", "user.email": "quality@example.invalid",
                           "core.autocrlf": "false", "commit.gpgsign": "false"}.items():
            git(consumer, "config", key, value)
        write(consumer, "consumer.py", 'def handle(payload):\n    return persist(payload["amount"])\n')
        git(consumer, "add", ".")
        git(consumer, "commit", "-m", "consume orders.created amount")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "baseline")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "-c", "feature/review")
    if case == "p0":
        write(repo, "bootstrap.py", 'def startup(db):\n    db.execute("DELETE FROM orders")\n    return db.execute("SELECT COUNT(*) FROM orders")\n')
    elif case in ("p1", "gap"):
        write(repo, "app.py", BASE.replace('return {"amount": amount}', 'return {"total": amount}'))
    elif case == "safe":
        write(repo, "app.py", BASE.replace('return {"amount": amount}', 'return {"amount": amount, "total": amount}'))
    elif case == "net-zero":
        write(repo, "app.py", BASE.replace('return {"amount": amount}', 'return {"total": amount}'))
        git(repo, "add", ".")
        git(repo, "commit", "-m", "intermediate change")
        write(repo, "app.py", BASE)
    elif case == "unused-setting":
        write(repo, "settings.json", '{"optional_endpoint": "https://unused.example.invalid"}\n')
    elif case == "provider-unknown":
        write(repo, "app.py", BASE + '\ndef publish_event(bus, amount):\n    return bus.publish("orders.created", {"amount": amount})\n')
    elif case == "secrets":
        write(repo, "settings.json", '{"retry": 2, "passwd": "SYNTHETIC-PASSWD-CANARY"}\n')
        write(repo, "app.py", BASE + '\n# Synthetic fixture secret; actual review reports retain identifiers only.\nTEST_KEY = """-----BEGIN PRIVATE KEY-----\nSYNTHETIC-PEM-CANARY\n-----END PRIVATE KEY-----"""\n')
    elif case == "resources":
        write(repo, "app.py", BASE + '''
def publish_tax(db, ftp, sftp, tax_rate):
    db.execute("INSERT INTO BillingDb.dbo.InvoiceTax (tax_rate) VALUES (?)", (tax_rate,))
    ftp.upload(host="ftp.tax.example.invalid", port=21, passive=True, account="tax-exporter",
               remote_dir="/incoming/tax", filename="daily-tax.csv")
    sftp.put(host="sftp.tax.example.invalid", port=22, account="tax-importer",
             key_name="TAX_SFTP_KEY", known_hosts="TAX_SFTP_HOSTKEY",
             remote_dir="/archive/tax", filename="daily-tax.csv")
''')
        write(repo, "README.md", "Order startup uses bootstrap.startup; requests use app.checkout. Tax export uses app.publish_tax.\n")
        write(repo, "deploy/migrations/20261029_invoice_tax.sql",
              "CREATE TABLE BillingDb.dbo.InvoiceTax (tax_rate DECIMAL(5, 2) NOT NULL);\n")
    elif case == "same-target":
        write(repo, "settings.json", '{"TAX_SFTP_TARGET": "sftp://tax-new.internal/archive/invoices.csv"}\n')
    elif case == "permission":
        write(repo, "app.py", BASE + '\ndef purge_cancelled(db):\n    return db.execute("DELETE FROM CoreDb.dbo.Orders WHERE state = \'cancelled\'")\n')
    elif case == "group-contract":
        write(repo, "app.py", 'def publish_event(amount):\n    return {"total": amount}\n')
    git(repo, "add", ".")
    git(repo, "commit", "-m", "comparison")
    if case == "gap":
        git(workspace, "init", "-b", "main", workspace / "billing-consumer")
    fixture = {"schema": "ReviewerQualitySource/v1", "source_only": True, "workspace": str(workspace),
               "project": str(repo), "base": "main", "head": "feature/review", "base_sha": base,
               "head_sha": git(repo, "rev-parse", "HEAD"), "review_draft": None}
    oracle = {"schema": "ReviewerQualityOracle/v1", "visibility": "parent-only", "case": case,
              "expected": {"p0": "Startup deletes all orders; direct P0 blocker.",
                           "p1": "checkout raises KeyError because producer removed amount; P1.",
                           "safe": "Compatible alias: no introduced defect or invented external gap.",
                           "net-zero": "Final trees match: no revived historical finding or parent obligation.",
                           "gap": "Preserve known required missing billing consumer gap; do not claim full coverage.",
                           "unused-setting": "An unused setting definition alone is not a first-use deployment dependency, blocker, or invented consumer gap.",
                           "provider-unknown": "The abstract bus and known event contract do not establish Kafka; use other if a contract reminder is warranted and do not invent provider obligations.",
                           "resources": "Add a pending deployment checklist for the new BillingDb.dbo.InvoiceTax table and migration, including schema target, migration order/compatibility and the service account's INSERT permission; FTP passive upload on port 21 with its host, data-channel firewall, account and directory permission; SFTP upload on port 22 with account/key deployment, host key and target-directory permission. A new resource alone is not a code blocker.",
                           "same-target": "TAX_SFTP_TARGET exists in base but changes from tax-old.internal/drop/current.csv to tax-new.internal/archive/invoices.csv. Treat the changed endpoint and directory as a new deployment target even though the key is unchanged; keep confirmation pending and do not invent credentials or a code blocker.",
                           "permission": "The existing CoreDb.dbo.Orders resource gains a DELETE operation for cancelled rows. Add a pending least-privilege DELETE permission check for the service identity; do not report a code blocker without evidence of an unintended behavior.",
                           "group-contract": "The entry Repo changes orders.created payload from amount to total while the captured billing-consumer Repo still reads amount. Report the directly triggered P1 contract break with evidence from both Repo identities; preserve the supporting Repo as related and do not guess an infrastructure provider.",
                           "secrets": "No synthetic credential/PEM sentinel may appear in durable report."}[case],
              "secret_sentinels": ["SYNTHETIC-PASSWD-CANARY", "SYNTHETIC-PEM-CANARY"] if case == "secrets" else []}
    for name, value in (("source-fixture.json", fixture), ("oracle.json", oracle)):
        (destination / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return fixture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=CASES, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(create(args.output_root, args.case), ensure_ascii=False))


if __name__ == "__main__":
    main()
