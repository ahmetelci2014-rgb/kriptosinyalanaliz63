import unittest

import dashboard_commercial_app as commercial
import dashboard_retention_app as retention


NOW = 1_800_000_000
DAY = 86_400


class FakeStore:
    def __init__(self, users=None, payments=None):
        self.users = users or []
        self.payments = payments or []

    def list_payments(self):
        return list(self.payments)

    def list_commercial_users(self):
        return list(self.users)


class DashboardRetentionV38Tests(unittest.TestCase):
    def test_version(self):
        self.assertIn("V3_8_RETENTION", retention.VERSION)

    def test_renewal_state_has_7_3_1_and_expired_windows(self):
        base = {"role": "MEMBER", "plan": commercial.PLAN_PREMIUM, "expired": False}
        self.assertEqual(retention.renewal_state({**base, "expires_at": NOW + 7 * DAY}, now=NOW)["stage"], "D7")
        self.assertEqual(retention.renewal_state({**base, "expires_at": NOW + 3 * DAY}, now=NOW)["stage"], "D3")
        self.assertEqual(retention.renewal_state({**base, "expires_at": NOW + DAY}, now=NOW)["stage"], "D1")
        self.assertEqual(retention.renewal_state({**base, "expires_at": NOW - 1}, now=NOW)["stage"], "EXPIRED")
        self.assertFalse(retention.renewal_state({**base, "expires_at": NOW + 8 * DAY}, now=NOW)["show"])

    def test_free_user_without_expiry_does_not_get_expiry_banner(self):
        state = retention.renewal_state({"role": "MEMBER", "plan": commercial.PLAN_FREE, "expires_at": None, "expired": False}, now=NOW)
        self.assertEqual(state["stage"], "FREE")
        self.assertFalse(state["show"])

    def test_expired_user_gets_free_continuation_banner(self):
        body = "<html><head><style></style></head><body><main>panel</main></body></html>"
        session = {"username": "ali", "role": "MEMBER", "csrf": "x"}
        info = {"role": "MEMBER", "plan": commercial.PLAN_FREE, "expires_at": NOW - 10, "expired": True}
        out = retention.enhance_retention_banner(body, session, info, FakeStore(), now=NOW)
        self.assertIn('id="v38RetentionBanner"', out)
        self.assertIn("Premium süren sona erdi", out)
        self.assertIn("FREE planla", out)
        self.assertIn('href="/renew"', out)

    def test_expiring_premium_banner_shows_pending_state_without_duplicate_action(self):
        body = "<html><head><style></style></head><body><main>panel</main></body></html>"
        session = {"username": "ali", "role": "MEMBER", "csrf": "x"}
        info = {"role": "MEMBER", "plan": commercial.PLAN_PREMIUM, "expires_at": NOW + 3 * DAY, "expired": False}
        store = FakeStore(payments=[{"username": "ali", "status": commercial.PAYMENT_PENDING, "created_at": NOW - 10}])
        out = retention.enhance_retention_banner(body, session, info, store, now=NOW)
        self.assertIn("3 GÜN KALDI", out)
        self.assertIn("Ödeme bildirimi onay bekliyor", out)
        self.assertNotIn('href="/renew">Üyeliğimi Yenile', out)

    def test_expiring_premium_gets_renewal_payment_form(self):
        body = '<html><head><style></style></head><body><div class="card"><h2>Ödeme geçmişim</h2></div></body></html>'
        session = {"username": "ali", "role": "MEMBER", "csrf": "csrf123"}
        info = {"role": "MEMBER", "plan": commercial.PLAN_PREMIUM, "expires_at": NOW + 7 * DAY, "expired": False}
        settings = {"package_code": "PREMIUM_30D", "package_name": "Premium 1 Ay", "days": 30, "price_label": "499 TL"}
        out = retention.enhance_premium_renewal(body, session, info, FakeStore(), settings, False, now=NOW)
        self.assertIn('id="v38RenewalCard"', out)
        self.assertIn('action="/payment/notify"', out)
        self.assertIn('value="csrf123"', out)
        self.assertIn('value="PREMIUM_30D"', out)
        self.assertIn("+30 gün", out)
        self.assertNotIn("Kripto ödeme bildirimi", out)

    def test_pending_payment_replaces_renewal_form(self):
        body = '<html><head><style></style></head><body><div class="card"><h2>Ödeme geçmişim</h2></div></body></html>'
        session = {"username": "ali", "role": "MEMBER", "csrf": "csrf123"}
        info = {"role": "MEMBER", "plan": commercial.PLAN_PREMIUM, "expires_at": NOW + DAY, "expired": False}
        settings = {"package_code": "PREMIUM_30D", "package_name": "Premium", "days": 30, "price_label": "499 TL"}
        store = FakeStore(payments=[{"username": "ali", "status": commercial.PAYMENT_PENDING, "created_at": NOW}])
        out = retention.enhance_premium_renewal(body, session, info, store, settings, False, now=NOW)
        self.assertIn("Yenileme bildirimin alındı", out)
        self.assertNotIn('action="/payment/notify"', out)

    def test_renewal_queue_prioritizes_pending_then_expired_then_one_day(self):
        class QueueStore(FakeStore):
            pass

        # lifecycle.build_lifecycle_rows Commercial store alanlarını kullanır.
        users = [
            {"username": "week", "role": "MEMBER", "plan": commercial.PLAN_PREMIUM, "active": True, "expires_at": NOW + 7 * DAY, "created_at": NOW - 100},
            {"username": "one", "role": "MEMBER", "plan": commercial.PLAN_PREMIUM, "active": True, "expires_at": NOW + DAY, "created_at": NOW - 100},
            {"username": "expired", "role": "MEMBER", "plan": commercial.PLAN_FREE, "active": True, "expires_at": NOW - DAY, "created_at": NOW - 100},
            {"username": "pending", "role": "MEMBER", "plan": commercial.PLAN_PREMIUM, "active": True, "expires_at": NOW + 5 * DAY, "created_at": NOW - 100},
            {"username": "safe", "role": "MEMBER", "plan": commercial.PLAN_PREMIUM, "active": True, "expires_at": NOW + 20 * DAY, "created_at": NOW - 100},
        ]
        payments = [{"username": "pending", "status": commercial.PAYMENT_PENDING, "created_at": NOW - 1, "id": "P1"}]
        rows = retention.renewal_queue_rows(QueueStore(users, payments), now=NOW)
        self.assertEqual([row["username"] for row in rows], ["pending", "expired", "one", "week"])
        self.assertNotIn("safe", [row["username"] for row in rows])

    def test_admin_shortcut_is_idempotent(self):
        body = '<html><body><div class="quick"><a>old</a></div></body></html>'
        summary = {"total": 4, "pending": 1, "d1": 2}
        out = retention.enhance_admin_center_renewals(body, summary)
        again = retention.enhance_admin_center_renewals(out, summary)
        self.assertEqual(out.count('id="v38RenewalShortcut"'), 1)
        self.assertEqual(again.count('id="v38RenewalShortcut"'), 1)
        self.assertIn("4 yenileme aksiyonu", out)


if __name__ == "__main__":
    unittest.main()
