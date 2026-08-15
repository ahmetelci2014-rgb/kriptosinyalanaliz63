import unittest

import dashboard_business_app as business
import dashboard_commercial_app as commercial
import dashboard_lifecycle_app as app


class FakeStore:
    def __init__(self, users=None, payments=None):
        self.users = list(users or [])
        self.payments = list(payments or [])

    def list_commercial_users(self):
        return list(self.users)

    def list_payments(self):
        return list(self.payments)


class DashboardLifecycleV37Tests(unittest.TestCase):
    def test_merge_created_metadata_is_safe(self):
        rows = [{"username": "ali", "role": "MEMBER", "plan": "FREE", "password_hash": "SHOULD-NOT-LEAK"}]
        raw = [{"username": "ali", "created_at": 123, "updated_at": 456, "password_hash": "SECRET"}]
        merged = app._merge_created_metadata(rows, raw)
        self.assertEqual(merged[0]["created_at"], 123)
        self.assertEqual(merged[0]["updated_at"], 456)
        self.assertNotIn("password_hash", merged[0])
        self.assertNotIn("SECRET", repr(merged))

    def test_lifecycle_segments_and_counts(self):
        now = 2_000_000_000
        store = FakeStore(
            users=[
                {"username": "p2", "role": "MEMBER", "plan": "PREMIUM", "active": True, "created_at": now - 10, "expires_at": now + 2 * app.DAY},
                {"username": "p6", "role": "MEMBER", "plan": "PREMIUM", "active": True, "created_at": now - 9 * app.DAY, "expires_at": now + 6 * app.DAY},
                {"username": "p20", "role": "MEMBER", "plan": "PREMIUM", "active": True, "created_at": now - 20 * app.DAY, "expires_at": now + 20 * app.DAY},
                {"username": "p60", "role": "MEMBER", "plan": "PREMIUM", "active": True, "created_at": now - 20 * app.DAY, "expires_at": now + 60 * app.DAY},
                {"username": "old", "role": "MEMBER", "plan": "FREE", "active": True, "created_at": now - 100 * app.DAY, "expires_at": now - app.DAY},
                {"username": "free", "role": "MEMBER", "plan": "FREE", "active": True, "created_at": now - app.DAY, "expires_at": None},
                {"username": "off", "role": "MEMBER", "plan": "FREE", "active": False, "created_at": now - app.DAY, "expires_at": None},
                {"username": "admin", "role": "ADMIN", "plan": "ADMIN", "active": True, "created_at": now, "expires_at": None},
            ],
            payments=[
                {"id": "PAY1", "username": "free", "status": commercial.PAYMENT_PENDING, "created_at": now - 3600},
            ],
        )
        rows = app.build_lifecycle_rows(store, now=now)
        summary = app.lifecycle_summary(rows, now=now)
        self.assertEqual(summary["total"], 7)
        self.assertEqual(summary["premium"], 4)
        self.assertEqual(summary["free"], 2)
        self.assertEqual(summary["expiring3"], 1)
        self.assertEqual(summary["expiring7"], 2)
        self.assertEqual(summary["expiring30"], 3)
        self.assertEqual(summary["expired"], 1)
        self.assertEqual(summary["pending"], 1)
        self.assertEqual(summary["inactive"], 1)
        self.assertEqual(summary["new7"], 3)

    def test_attention_sort_puts_pending_first_then_inactive_and_expired(self):
        now = 2_000_000_000
        store = FakeStore(
            users=[
                {"username": "pending", "role": "MEMBER", "plan": "FREE", "active": True},
                {"username": "inactive", "role": "MEMBER", "plan": "FREE", "active": False},
                {"username": "expired", "role": "MEMBER", "plan": "FREE", "active": True, "expires_at": now - 1},
                {"username": "soon", "role": "MEMBER", "plan": "PREMIUM", "active": True, "expires_at": now + app.DAY},
            ],
            payments=[{"id": "PAY", "username": "pending", "status": commercial.PAYMENT_PENDING, "created_at": now - 100}],
        )
        rows = app.build_lifecycle_rows(store, now=now)
        selected = app.filter_lifecycle_rows(rows, segment="action", sort="attention")
        self.assertEqual([row["username"] for row in selected], ["pending", "inactive", "expired", "soon"])

    def test_search_and_segment_filter(self):
        rows = [
            {"username": "ali", "premium": True, "free": False, "priority": 6, "expires_at": 10},
            {"username": "veli", "premium": False, "free": True, "priority": 7, "expires_at": None},
        ]
        selected = app.filter_lifecycle_rows(rows, segment="premium", query="AL", sort="username")
        self.assertEqual([row["username"] for row in selected], ["ali"])
        self.assertEqual(app.filter_lifecycle_rows(rows, segment="free", query="ali"), [])

    def test_lifecycle_page_has_filters_and_pending_payment_safety(self):
        now = 2_000_000_000
        store = FakeStore(
            users=[
                {"username": "pending", "role": "MEMBER", "plan": "FREE", "active": True, "created_at": now - 100},
                {"username": "renew", "role": "MEMBER", "plan": "PREMIUM", "active": True, "created_at": now - 100, "expires_at": now + 5 * app.DAY},
            ],
            payments=[{"id": "P1", "username": "pending", "status": commercial.PAYMENT_PENDING, "created_at": now - 10}],
        )
        session = {"username": "admin", "csrf": "csrf123", "role": "ADMIN"}
        settings = {"days": 30}
        body = app.lifecycle_page(store, session, settings, segment="all")
        self.assertIn("Müşteri Yaşam Döngüsü", body)
        self.assertIn("Ödeme kararı verilmeden hızlı süre ekleme kapalı", body)
        self.assertIn('href="/admin/memberships">Ödemeyi incele', body)
        self.assertIn('action="/admin/lifecycle/plan"', body)
        self.assertIn('value="30"', body)
        self.assertIn('value="90"', body)
        self.assertIn("≤3 gün", body)
        self.assertIn("≤7 gün", body)
        self.assertIn("≤30 gün", body)
        self.assertNotIn("password_hash", body)

    def test_admin_center_shortcut_is_idempotent(self):
        base = '<html><body><div class="quick"><a href="/admin/users">Users</a></div></body></html>'
        summary = {"action": 4, "expiring7": 2, "pending": 1}
        first = app.enhance_admin_center_lifecycle(base, summary)
        second = app.enhance_admin_center_lifecycle(first, summary)
        self.assertEqual(first, second)
        self.assertIn('id="v37LifecycleShortcut"', first)
        self.assertIn("4 aksiyon", first)
        self.assertIn("2 yakında bitecek", first)

    def test_v36_new_user_metric_works_when_created_at_is_available(self):
        now = 2_000_000_000
        store = FakeStore(users=[
            {"username": "new", "role": "MEMBER", "plan": "FREE", "active": True, "created_at": now - app.DAY},
            {"username": "old", "role": "MEMBER", "plan": "FREE", "active": True, "created_at": now - 20 * app.DAY},
        ])
        metrics = business.build_business_metrics(store, now=now)
        self.assertEqual(metrics["new_users_7d"], 1)

    def test_version(self):
        self.assertEqual(app.VERSION, "KRIPTO_KONTROL_MERKEZI_V3_7_LIFECYCLE_2026_08_15")


if __name__ == "__main__":
    unittest.main()
