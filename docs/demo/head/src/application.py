from existing import read_orders
from service import average

def calculate_order_stats(db, total, count):
    read_orders(db)
    return average(total, count)

from service import billing_timeout, discount, invoice_url, query_tax

def prepare_invoice(db):
    tax = query_tax(db)
    timeout = billing_timeout()
    return {"tax": tax, "timeout": timeout, "discount": discount(), "url": invoice_url()}
