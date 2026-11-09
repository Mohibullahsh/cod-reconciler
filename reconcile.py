#!/usr/bin/env python3
"""Reconcile courier COD payouts against ecommerce orders on tracking number.

Standard library only. Money is handled as Decimal so totals stay exact.
Run `python reconcile.py --help` for usage.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

CENTS = Decimal("0.01")

MATCHED = "MATCHED"
COD_MISMATCH = "COD_MISMATCH"
FEE_MISMATCH = "FEE_MISMATCH"
NET_MISMATCH = "NET_MISMATCH"
DUPLICATE_PAYOUT = "DUPLICATE_PAYOUT"
UNKNOWN_PAYOUT = "UNKNOWN_PAYOUT"
OVERDUE = "OVERDUE"
AWAITING = "AWAITING_SETTLEMENT"

NOT_EXPECTED_TO_SETTLE = {"voided", "refunded"}

OUTPUT_FIELDS = [
    "status", "tracking_number", "order", "order_date", "receipt_id",
    "order_cod", "collected", "shipping", "gst", "platform_fee", "net_paid",
    "age_days", "shopify_status_stale", "notes",
]


@dataclass(frozen=True)
class RateCard:
    weight_break_kg: Decimal = Decimal("0.5")
    shipping_light: Decimal = Decimal("125.00")
    shipping_heavy: Decimal = Decimal("137.50")
    gst_light: Decimal = Decimal("20.00")
    gst_heavy: Decimal = Decimal("22.00")
    platform_rate: Decimal = Decimal("0.04")

    @classmethod
    def from_json(cls, path: Path) -> "RateCard":
        raw = json.loads(path.read_text(encoding="utf-8"))
        unknown = set(raw) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"Unknown rate card keys: {sorted(unknown)}")
        return cls(**{key: Decimal(str(value)) for key, value in raw.items()})

    def expected_fees(self, weight_kg: Decimal, collected: Decimal) -> tuple[Decimal, Decimal, Decimal]:
        heavy = weight_kg >= self.weight_break_kg
        shipping = self.shipping_heavy if heavy else self.shipping_light
        gst = self.gst_heavy if heavy else self.gst_light
        return shipping, gst, (collected * self.platform_rate).quantize(CENTS, ROUND_HALF_UP)


@dataclass
class Result:
    rows: list[dict] = field(default_factory=list)

    @property
    def exceptions(self) -> list[dict]:
        return [row for row in self.rows if row["status"] not in (MATCHED, AWAITING)]

    def counts(self) -> Counter:
        return Counter(row["status"] for row in self.rows)


def money(value: str, label: str) -> Decimal:
    try:
        return Decimal(value.replace(",", "").strip()).quantize(CENTS, ROUND_HALF_UP)
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError(f"Bad amount in {label}: {value!r}") from exc


def read_csv(path: Path, required: set[str]) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path.name} is missing columns: {sorted(missing)}")
        return list(reader)


def key(tracking: str) -> str:
    return tracking.strip().upper()


def load_orders(path: Path) -> dict[str, dict]:
    required = {"order", "order_date", "tracking_number", "cod_amount", "financial_status", "weight_kg"}
    orders: dict[str, dict] = {}
    for line, row in enumerate(read_csv(path, required), start=2):
        tracking = key(row["tracking_number"])
        if not tracking:
            continue
        if tracking in orders:
            raise ValueError(f"{path.name} line {line}: tracking number {tracking} is on two orders")
        orders[tracking] = {
            "order": row["order"].strip(),
            "order_date": date.fromisoformat(row["order_date"].strip()),
            "cod_amount": money(row["cod_amount"], f"{path.name} line {line}"),
            "financial_status": row["financial_status"].strip().lower(),
            "weight_kg": Decimal(row["weight_kg"].strip()),
        }
    return orders


def load_payouts(path: Path) -> dict[str, list[dict]]:
    amounts = ("collected", "shipping", "gst", "platform_fee", "net_paid")
    payouts: dict[str, list[dict]] = defaultdict(list)
    for line, row in enumerate(read_csv(path, {"receipt_id", "tracking_number", *amounts}), start=2):
        payouts[key(row["tracking_number"])].append({
            "receipt_id": row["receipt_id"].strip(),
            **{name: money(row[name], f"{path.name} line {line}") for name in amounts},
        })
    return dict(payouts)


def payout_row(status: str, tracking: str, payout: dict, order: dict | None, notes: str = "") -> dict:
    stale = bool(order and order["financial_status"] == "pending")
    return {
        "status": status,
        "tracking_number": tracking,
        "order": order["order"] if order else "",
        "order_date": order["order_date"].isoformat() if order else "",
        "receipt_id": payout["receipt_id"],
        "order_cod": order["cod_amount"] if order else "",
        "collected": payout["collected"],
        "shipping": payout["shipping"],
        "gst": payout["gst"],
        "platform_fee": payout["platform_fee"],
        "net_paid": payout["net_paid"],
        "age_days": "",
        "shopify_status_stale": "yes" if stale else "",
        "notes": notes,
    }


def check_payout(payout: dict, order: dict, rates: RateCard, tolerance: Decimal) -> tuple[str, str]:
    """Return the first failing check, most serious first."""
    gap = payout["collected"] - order["cod_amount"]
    if abs(gap) > tolerance:
        return COD_MISMATCH, f"Courier collected {gap:+} against the order value"

    deductions = payout["shipping"] + payout["gst"] + payout["platform_fee"]
    net_gap = payout["net_paid"] - (payout["collected"] - deductions)
    if abs(net_gap) > tolerance:
        return NET_MISMATCH, f"Net paid is {net_gap:+} against collected less deductions"

    expected = rates.expected_fees(order["weight_kg"], payout["collected"])
    actual = (payout["shipping"], payout["gst"], payout["platform_fee"])
    problems = [
        f"{label} charged {got}, rate card {want}"
        for label, got, want in zip(("shipping", "gst", "platform fee"), actual, expected)
        if abs(got - want) > tolerance
    ]
    if problems:
        return FEE_MISMATCH, "; ".join(problems)
    return MATCHED, ""


def unsettled_row(tracking: str, order: dict, as_of: date, grace_days: int) -> dict:
    age = (as_of - order["order_date"]).days
    return {
        "status": OVERDUE if age > grace_days else AWAITING,
        "tracking_number": tracking,
        "order": order["order"],
        "order_date": order["order_date"].isoformat(),
        "receipt_id": "",
        "order_cod": order["cod_amount"],
        "collected": "", "shipping": "", "gst": "", "platform_fee": "", "net_paid": "",
        "age_days": age,
        "shopify_status_stale": "",
        "notes": f"No payout after {age} days" if age > grace_days else "",
    }


def reconcile(orders: dict[str, dict], payouts: dict[str, list[dict]], as_of: date,
              rates: RateCard | None = None, grace_days: int = 7,
              tolerance: Decimal = CENTS) -> Result:
    rates = rates or RateCard()
    result = Result()

    for tracking, entries in payouts.items():
        order = orders.get(tracking)
        if order is None:
            for payout in entries:
                result.rows.append(payout_row(UNKNOWN_PAYOUT, tracking, payout, None,
                                              "Paid by courier, no matching order"))
            continue
        first, *repeats = entries
        status, notes = check_payout(first, order, rates, tolerance)
        result.rows.append(payout_row(status, tracking, first, order, notes))
        for payout in repeats:
            result.rows.append(payout_row(DUPLICATE_PAYOUT, tracking, payout, order,
                                          f"Already settled on {first['receipt_id']}"))

    for tracking, order in orders.items():
        if tracking not in payouts and order["financial_status"] not in NOT_EXPECTED_TO_SETTLE:
            result.rows.append(unsettled_row(tracking, order, as_of, grace_days))

    result.rows.sort(key=lambda row: (row["status"] == MATCHED, row["status"], row["tracking_number"]))
    return result


def sum_of(rows: list[dict], column: str) -> Decimal:
    return sum((row[column] for row in rows if row[column] != ""), Decimal("0.00"))


def summary(result: Result, as_of: date) -> str:
    rows = result.rows
    first_settlements = [r for r in rows if r["status"] not in (DUPLICATE_PAYOUT, OVERDUE, AWAITING)]
    collected = sum_of(first_settlements, "collected")
    fees = sum(sum_of(first_settlements, c) for c in ("shipping", "gst", "platform_fee"))
    overdue = sum_of([r for r in rows if r["status"] == OVERDUE], "order_cod")
    awaiting = sum_of([r for r in rows if r["status"] == AWAITING], "order_cod")
    duplicates = sum_of([r for r in rows if r["status"] == DUPLICATE_PAYOUT], "net_paid")
    stale = sum(1 for r in rows if r["shopify_status_stale"])
    fee_share = (fees / collected * 100).quantize(CENTS, ROUND_HALF_UP) if collected else Decimal("0.00")

    lines = [
        f"# Reconciliation summary, as of {as_of.isoformat()}", "",
        "| Status | Parcels |", "| --- | ---: |",
        *[f"| {status} | {count} |" for status, count in sorted(result.counts().items())],
        "", "| Money (PKR) | Amount |", "| --- | ---: |",
        f"| COD collected | {collected:,.2f} |",
        f"| Courier deductions | {fees:,.2f} |",
        f"| Deductions as share of collected | {fee_share}% |",
        f"| Net paid, first settlements | {sum_of(first_settlements, 'net_paid'):,.2f} |",
        f"| Overdue COD | {overdue:,.2f} |",
        f"| Awaiting settlement | {awaiting:,.2f} |",
        f"| Possible duplicate payments | {duplicates:,.2f} |",
        "", f"Settled orders still marked pending in the store: {stale}", "",
    ]
    return "\n".join(lines)


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--orders", type=Path, required=True, help="Orders CSV")
    parser.add_argument("--payouts", type=Path, required=True, help="Courier payout rows CSV")
    parser.add_argument("--out", type=Path, default=Path("output"), help="Output folder")
    parser.add_argument("--as-of", type=date.fromisoformat, default=None,
                        help="Date used to age unsettled parcels, YYYY-MM-DD (default today)")
    parser.add_argument("--grace-days", type=int, default=7, help="Days before an unsettled parcel is overdue")
    parser.add_argument("--tolerance", type=Decimal, default=CENTS, help="Allowed rounding difference in PKR")
    parser.add_argument("--rates", type=Path, help="Optional JSON rate card")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    as_of = args.as_of or datetime.now().date()
    rates = RateCard.from_json(args.rates) if args.rates else RateCard()
    result = reconcile(load_orders(args.orders), load_payouts(args.payouts), as_of,
                       rates, args.grace_days, args.tolerance)
    args.out.mkdir(parents=True, exist_ok=True)
    write_csv(args.out / "reconciliation.csv", result.rows)
    write_csv(args.out / "exceptions.csv", result.exceptions)
    text = summary(result, as_of)
    (args.out / "summary.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
