import json
import os
from pathlib import Path

SETTINGS = json.loads(Path(__file__).with_name("settings.json").read_text())

def average(total, count):
    return total / count

def billing_timeout():
    return SETTINGS["billing_timeout_seconds"]

def invoice_url():
    return os.getenv("INVOICE_API_URL", "http://invoice.internal")

def discount():
    return SETTINGS["discount_rate"]

def query_tax(db):
    return db.execute("SELECT tax_rate FROM BillingDb.dbo.InvoiceTax")

def read_orders_again(db):
    return db.execute("SELECT id FROM CoreDb.dbo.Orders")
