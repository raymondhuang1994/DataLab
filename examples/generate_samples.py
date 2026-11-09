"""Generate deterministic synthetic fixtures, not real business evidence."""
from pathlib import Path
import math
import random
import csv

ROOT = Path(__file__).resolve().parent


def save(name, columns, rows):
    with (ROOT / name).open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream); writer.writerow(columns); writer.writerows(rows)


def main():
    sales = []
    for month in range(1, 13):
        for region, multiplier in [("华东", 1.2), ("华南", 1.0), ("华北", .8)]:
            for product, base in [("办公用品", 12000), ("数码配件", 18000), ("生活用品", 9000)]:
                revenue = round(base * multiplier * (1 + .035 * month) * (1.12 if month == 11 else 1), 2)
                orders = round(revenue / (180 if product == "数码配件" else 90))
                sales.append([f"2025-{month:02d}-15", region, product, revenue, orders, round(revenue * .31, 2)])
    save("sales.csv", ["date", "region", "product", "revenue", "orders", "gross_profit"], sales)
    rng = random.Random(20261108)
    customers = []
    for i in range(300):
        tenure = rng.randint(1, 60); spend = round(rng.uniform(20, 400), 2); tickets = rng.randint(0, 8)
        plan = rng.choice(["基础版", "专业版", "企业版"])
        probability = 1 / (1 + math.exp(-(-1.1 - .035 * tenure + .45 * tickets - .003 * spend)))
        customers.append([f"C{i:04d}", tenure, spend, tickets, plan, int(rng.random() < probability)])
    save("customers.csv", ["customer_id", "tenure", "spend", "tickets", "plan", "churn"], customers)
    from datetime import date, timedelta
    rows = [[(date(2024, 1, 1) + timedelta(days=i)).isoformat(), round(120 + .4 * i + 12 * math.sin(i * 2 * math.pi/7) + rng.gauss(0, 2), 3)] for i in range(180)]
    save("series.csv", ["date", "sales"], rows)


if __name__ == "__main__":
    main()
