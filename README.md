# COD Reconciler

Find the cash on delivery money your courier has not paid you, or has paid wrongly.

Most online orders in Pakistan are cash on delivery. The courier collects the cash, deducts its charges, and pays the store later through payout receipts. Those receipts list tracking numbers but no order numbers, so a store cannot easily tell which orders were paid, which were short paid, and which were never settled.

This tool joins orders to payouts on tracking number, checks every parcel, and produces a short list of exceptions for a person to chase.

## Demo result

A synthetic month of 240 parcels with 10 planted faults. The tool found all 10 and raised no false alarms.

| Status | Parcels | Meaning |
| --- | ---: | --- |
| `MATCHED` | 189 | Amount, fees and net payment all correct |
| `AWAITING_SETTLEMENT` | 43 | Unpaid, but still inside the grace period |
| `OVERDUE` | 3 | Unpaid after the grace period |
| `COD_MISMATCH` | 2 | Courier collected a different amount from the order value |
| `FEE_MISMATCH` | 2 | A charge differs from the rate card |
| `NET_MISMATCH` | 1 | Net payment does not equal collected less deductions |
| `DUPLICATE_PAYOUT` | 1 | Same parcel paid on a second receipt |
| `UNKNOWN_PAYOUT` | 1 | Courier paid for a parcel with no matching order |

| Money (PKR, synthetic) | Amount |
| --- | ---: |
| COD collected | 931,850.00 |
| Courier deductions | 67,034.00 |
| Net paid, first settlements | 864,666.00 |
| Overdue COD | 15,470.00 |
| Awaiting settlement | 197,070.00 |
| Possible duplicate payments | 2,230.90 |

It also flagged 136 settled orders still marked pending in the store. That status gap understates revenue in store reports.

See the full output in [`demo/output`](demo/output).

## Run it

Python 3.10 or newer. No packages to install.

```bash
python reconcile.py --orders demo/orders.csv --payouts demo/payouts.csv --out demo/output --as-of 2026-09-30
python -m unittest
```

To regenerate the synthetic data, run `python generate_demo.py`.

## Input files

**Orders:** `order, order_date, tracking_number, cod_amount, financial_status, weight_kg`

**Payouts:** `receipt_id, tracking_number, collected, shipping, gst, platform_fee, net_paid`

## How it decides

1. Payouts are joined to orders on tracking number, ignoring case and stray spaces.
2. Each parcel is checked in order of seriousness: cash collected, then the net arithmetic, then fees against the rate card. The first failure sets the status.
3. Only the first payout for a parcel counts toward totals. Any repeat is reported as a possible duplicate.
4. Unpaid parcels are aged from the order date. Voided and refunded orders are not expected to settle.
5. Amounts use exact decimal arithmetic, because float rounding has no place in a payout check.

## Rate card

The default rate card comes from one real courier settlement: PKR 125.00 shipping and 20.00 GST under 0.5 kg, PKR 137.50 and 22.00 at or above it, plus a 4 percent platform deduction on collected cash. Rates differ by merchant, so pass your own JSON with `--rates`, using the field names in `RateCard`. Use `--tolerance` if your courier rounds differently.

## Status and limits

This repository contains synthetic data only. The method comes from reconciling real payouts for a Pakistani clothing brand, and this packaged version has been tested on the synthetic set and unit tests, not yet on a live store's export. Payout rows must currently be supplied as CSV.

Next steps are reading payout receipt PDFs directly and pulling orders from the Shopify API.

Built with Claude Code.
