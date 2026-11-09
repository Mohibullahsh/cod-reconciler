#!/usr/bin/env python3
"""Generate fully synthetic orders and payouts. No real store or customer data."""
from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from reconcile import RateCard

SEED = 54
AS_OF = date(2026, 9, 30)
PARCELS = 240
OUT = Path(__file__).parent / "demo"
ORDER_FIELDS = ["order", "order_date", "tracking_number", "cod_amount", "financial_status", "weight_kg"]
PAYOUT_FIELDS = ["receipt_id", "tracking_number", "collected", "shipping", "gst", "platform_fee", "net_paid"]


def clean_payout(receipt: str, order: dict, rates: RateCard) -> dict:
    collected = Decimal(order["cod_amount"])
    shipping, gst, platform = rates.expected_fees(Decimal(order["weight_kg"]), collected)
    return {
        "receipt_id": receipt, "tracking_number": order["tracking_number"], "collected": collected,
        "shipping": shipping, "gst": gst, "platform_fee": platform,
        "net_paid": collected - shipping - gst - platform,
    }


def main() -> None:
    rng = random.Random(SEED)
    rates = RateCard()
    orders = []
    for number in range(1, PARCELS + 1):
        orders.append({
            "order": f"#DEMO{1000 + number}",
            "order_date": (AS_OF - timedelta(days=rng.randint(1, 30))).isoformat(),
            "tracking_number": f"DEMO{100000 + number}",
            "cod_amount": f"{rng.choice(range(2490, 7490, 500))}.00",
            "financial_status": rng.choice(["pending", "pending", "paid"]),
            "weight_kg": rng.choice(["0.40", "0.45", "0.75", "1.10"]),
        })

    # Parcels past the seven day grace period settle; newer ones are still with the courier.
    settled = [o for o in orders if (AS_OF - date.fromisoformat(o["order_date"])).days > 7]
    payouts = [clean_payout(f"DEMO-CPR-{1 + i % 6:03d}", o, rates) for i, o in enumerate(settled)]

    # Planted faults, so the demo proves each check fires.
    payouts[3]["collected"] -= 500
    payouts[3]["net_paid"] -= 500
    payouts[17]["collected"] -= 200
    payouts[17]["net_paid"] -= 200
    payouts[29]["shipping"] += 40
    payouts[29]["net_paid"] -= 40
    payouts[41]["platform_fee"] += 25
    payouts[41]["net_paid"] -= 25
    payouts[55]["net_paid"] -= 150
    payouts.append({**payouts[8], "receipt_id": "DEMO-CPR-007"})
    payouts.append({**clean_payout("DEMO-CPR-007", orders[0], rates), "tracking_number": "DEMO999001"})
    dropped = {payouts[12]["tracking_number"], payouts[63]["tracking_number"], payouts[88]["tracking_number"]}
    payouts = [p for p in payouts if p["tracking_number"] not in dropped]

    OUT.mkdir(exist_ok=True)
    for name, fields, rows in (("orders.csv", ORDER_FIELDS, orders), ("payouts.csv", PAYOUT_FIELDS, payouts)):
        with (OUT / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    print(f"Wrote {len(orders)} orders and {len(payouts)} payout rows to {OUT}")


if __name__ == "__main__":
    main()
