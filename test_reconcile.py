import unittest
from datetime import date
from decimal import Decimal as D

import reconcile as r

AS_OF = date(2026, 9, 30)


def order(cod="3000.00", status="pending", weight="0.40", day=date(2026, 9, 1)):
    return {"order": "#1", "order_date": day, "cod_amount": D(cod),
            "financial_status": status, "weight_kg": D(weight)}


def payout(collected="3000.00", shipping="125.00", gst="20.00", platform="120.00", net=None, receipt="R1"):
    values = {"collected": D(collected), "shipping": D(shipping), "gst": D(gst), "platform_fee": D(platform)}
    values["net_paid"] = D(net) if net else values["collected"] - values["shipping"] - values["gst"] - values["platform_fee"]
    return {"receipt_id": receipt, **values}


def status_of(orders, payouts, **kwargs):
    return r.reconcile(orders, payouts, AS_OF, **kwargs).rows[0]["status"]


class ReconcileTests(unittest.TestCase):
    def test_clean_parcel_matches(self):
        self.assertEqual(status_of({"A": order()}, {"A": [payout()]}), r.MATCHED)

    def test_heavy_parcel_uses_heavy_tier(self):
        heavy = payout(shipping="137.50", gst="22.00")
        self.assertEqual(status_of({"A": order(weight="0.50")}, {"A": [heavy]}), r.MATCHED)

    def test_light_rate_on_heavy_parcel_is_fee_mismatch(self):
        self.assertEqual(status_of({"A": order(weight="0.75")}, {"A": [payout()]}), r.FEE_MISMATCH)

    def test_short_collection(self):
        short = payout(collected="2500.00", platform="100.00")
        self.assertEqual(status_of({"A": order()}, {"A": [short]}), r.COD_MISMATCH)

    def test_net_does_not_add_up(self):
        self.assertEqual(status_of({"A": order()}, {"A": [payout(net="2000.00")]}), r.NET_MISMATCH)

    def test_tolerance_allows_rounding(self):
        slightly = payout(net="2734.50")
        self.assertEqual(status_of({"A": order()}, {"A": [slightly]}, tolerance=D("1.00")), r.MATCHED)

    def test_duplicate_payout_is_flagged_once(self):
        result = r.reconcile({"A": order()}, {"A": [payout(), payout(receipt="R2")]}, AS_OF)
        self.assertEqual(result.counts(), {r.MATCHED: 1, r.DUPLICATE_PAYOUT: 1})

    def test_payout_without_order(self):
        self.assertEqual(status_of({}, {"Z": [payout()]}), r.UNKNOWN_PAYOUT)

    def test_old_unsettled_parcel_is_overdue(self):
        self.assertEqual(status_of({"A": order()}, {}), r.OVERDUE)

    def test_recent_unsettled_parcel_is_awaiting(self):
        self.assertEqual(status_of({"A": order(day=date(2026, 9, 28))}, {}), r.AWAITING)

    def test_voided_order_is_not_expected(self):
        self.assertEqual(r.reconcile({"A": order(status="voided")}, {}, AS_OF).rows, [])

    def test_stale_shopify_status_is_flagged(self):
        row = r.reconcile({"A": order()}, {"A": [payout()]}, AS_OF).rows[0]
        self.assertEqual(row["shopify_status_stale"], "yes")

    def test_awaiting_is_not_an_exception(self):
        result = r.reconcile({"A": order(day=date(2026, 9, 28))}, {}, AS_OF)
        self.assertEqual(result.exceptions, [])


if __name__ == "__main__":
    unittest.main()
