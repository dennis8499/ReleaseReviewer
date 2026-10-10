from existing import read_orders
from service import average

def calculate_order_stats(db, total, count):
    read_orders(db)
    return average(total, count)
