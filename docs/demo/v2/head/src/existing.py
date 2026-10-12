import os

def read_orders(db):
    timeout = os.getenv("EXISTING_TIMEOUT", "30")
    return db.execute("SELECT id FROM CoreDb.dbo.Orders")
