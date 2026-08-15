import unittest

import dashboard_billing_app as billing
import dashboard_business_app as app
import dashboard_commercial_app as commercial


class FakeStore:
    def __init__(self, users=None, payments=None):
        self._users = list(users or [])
        self._payments = list(payments or [])

    def list_commercial_users(self):
        return list(self._users)

    def list_payments(self):
        return list(self._payments)


class DashboardBusinessV36Tests(unittest.TestCase):
    def test_product_proof_is_aggregate_only(self):
        proof = app.build_product_proof({
            "open_trades": [
                {"symbol": "BTCUSDT", "entry": 100, "tp1": 101, "sl": 99},
                {"symbol": "ETHUSDT", "entry": 200, "tp1": 198, "sl": 204},
            ],
            "recent_results": [
                {"symbol": "BTCUSDT", "outcome": "TP1"},
                {"symbol": "ETHUSDT", "outcome": "SL"},
                {"symbol": "SOLUSDT", "outcome": "TP1_SONRASI_BE"},
            ],
            "health": {"overall": "GREEN"},
        })
        self.assertEqual(proof["open_count"], 2)
        self.assertEqual(proof["recent_count"], 3)
        self.assertEqual(proof["tp_count"], 1)
        self.assertEqual(proof["sl_count"], 1)
        self.assertEqual(proof["be_count"], 1)
        self.assertEqual(proof["tp_rate_percent"], 33.3)
        text = repr(proof).lower()
        for forbidden in ("btcusdt", "ethusdt", "entry", "tp1", "sl", "score"):
            self.assertNotIn(forbidden, text)

    def test_business_metrics_use_real_operational_counts(self):
        now = 2_000_000_000
        store = FakeStore(
            users=[
                {"username": "admin2", "role": "ADMIN", "plan": "ADMIN", "active": True, "created_at": now - 100},
                {"username": "p1", "role": "MEMBER", "plan": "PREMIUM", "active": True, "created_at": now - 1000, "expires_at": now + 2 * 86400},
                {"username": "p2", "role": "MEMBER", "plan": "PREMIUM", "active": True, "created_at": now - 10 * 86400, "expires_at": now + 20 * 86400},
                {"username": "f1", "role": "MEMBER", "plan": "FREE", "active": True, "created_at": now - 2 * 86400},
                {"username": "off", "role": "MEMBER", "plan": "FREE", "active": False, "created_at": now - 3 * 86400},
            ],
            payments=[
                {"id": "x1", "username": "f1", "status": commercial.PAYMENT_PENDING, "created_at": now - 7200},
                {"id": "x2", "username": "p1", "status": commercial.PAYMENT_APPROVED, "created_at": now - 5000, "decided_at": now - 4000},
                {"id": "x3", "username": "old", "status": commercial.PAYMENT_REJECTED, "created_at": now - 20 * 86400, "decided_at": now - 19 * 86400},
            ],
        )
        metrics = app.build_business_metrics(store, now=now)
        self.assertEqual(metrics["customers"], 4)
        self.assertEqual(metrics["active_customers"], 3)
        self.assertEqual(metrics["premium"], 2)
        self.assertEqual(metrics["free"], 1)
        self.assertEqual(metrics["conversion_percent"], 66.7)
        self.assertEqual(metrics["new_users_7d"], 3)
        self.assertEqual(metrics["expiring_7d"], 1)
        self.assertEqual(metrics["expiring_users"], ["p1"])
        self.assertEqual(metrics["pending_payments"], 1)
        self.assertEqual(metrics["oldest_pending_hours"], 2.0)
        self.assertEqual(metrics["approved_7d"], 1)
        self.assertEqual(metrics["rejected_7d"], 0)

    def test_premium_sales_keeps_existing_payment_flow(self):
        store = FakeStore()
        session = {"username": "demo", "csrf": "csrf123", "role": "MEMBER"}
        info = {"plan": commercial.PLAN_FREE, "expires_at": 0}
        settings = {
            "package_name": "Premium 30 Gün",
            "price_label": "499 TL",
            "days": 30,
            "package_code": "PREMIUM_30D",
            "instructions": "Ödeme bilgisini kullan ve bildir.",
        }
        base = billing.premium_page_v31(session, info, store, settings, False)
        body = app.enhance_premium_sales(base, session, info, settings, {
            "health": "GREEN", "open_count": 4, "recent_count": 8, "tp_rate_percent": 62.5,
        })
        self.assertIn('id="v36PremiumSales"', body)
        self.assertIn("Premium ile açılan çalışma alanı", body)
        self.assertIn("Nasıl Premium olunur?", body)
        self.assertIn('id="payment"', body)
        self.assertIn('action="/payment/notify"', body)
        self.assertIn('name="package" value="PREMIUM_30D"', body)
        self.assertIn("499 TL", body)
        self.assertIn("Otomatik emir açılmaz", body)
        self.assertIn("v36-mobile", body)

    def test_admin_business_layer_is_idempotent_and_has_attention_queue(self):
        base = '<!doctype html><html><head><style>.x{}</style></head><body><div class="shell"><div class="kpis">KPI</div></div></body></html>'
        metrics = {
            "customers": 10,
            "conversion_percent": 30.0,
            "new_users_7d": 2,
            "pending_payments": 1,
            "expiring_7d": 2,
            "approved_7d": 3,
            "oldest_pending_hours": 4.5,
            "expiring_users": ["ali", "veli"],
        }
        first = app.enhance_admin_business(base, metrics)
        second = app.enhance_admin_business(first, metrics)
        self.assertEqual(first, second)
        self.assertIn('id="v36BusinessOps"', first)
        self.assertIn("Premium dönüşüm", first)
        self.assertIn("%30.0", first)
        self.assertIn("Ödeme önceliği", first)
        self.assertIn("ali, veli", first)
        self.assertIn('/admin/memberships', first)

    def test_version(self):
        self.assertEqual(app.VERSION, "KRIPTO_KONTROL_MERKEZI_V3_6_BUSINESS_QUALITY_2026_08_15")


if __name__ == "__main__":
    unittest.main()
