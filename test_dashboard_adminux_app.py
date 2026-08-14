import unittest

import dashboard_adminux_app as app


class FakeStore:
    def list_commercial_users(self):
        return [
            {"username": "premium1", "role": "MEMBER", "plan": "PREMIUM", "active": True, "expires_at": 2_000_000_000, "updated_at": 30},
            {"username": "free1", "role": "MEMBER", "plan": "FREE", "active": True, "expires_at": None, "updated_at": 20},
            {"username": "admin2", "role": "ADMIN", "plan": "ADMIN", "active": False, "expires_at": None, "updated_at": 10},
        ]

    def list_payments(self):
        return [
            {"id": "P1", "username": "free1", "package": "PREMIUM_30D", "status": "PENDING", "created_at": 100},
            {"id": "P2", "username": "premium1", "package": "PREMIUM_30D", "status": "APPROVED", "created_at": 90, "decided_at": 95},
            {"id": "P3", "username": "free1", "package": "PREMIUM_30D", "status": "REJECTED", "created_at": 80, "decided_at": 85},
        ]


class FakeService:
    def get_data(self):
        return {
            "open_trades": [{"symbol": "BTCUSDT"}, {"symbol": "ETHUSDT"}],
            "recent_results": [
                {"outcome": "TP2"},
                {"outcome": "SL"},
                {"outcome": "BE"},
            ],
            "health": {"overall": "GREEN"},
            "data_quality": {"ok": True},
        }


class FakeConfig:
    username = "founder"


class DashboardAdminUXTests(unittest.TestCase):
    def test_version(self):
        self.assertIn("V3_2_ADMIN_UX", app.VERSION)

    def test_admin_dashboard_gets_visible_logout_and_mobile_admin(self):
        body = """<html><head><style>.x{}</style></head><body><aside><a class=\"nav-item\" href=\"/account\"><span>○</span><b>Hesabım</b></a></aside><header class=\"topbar\"></header><nav class=\"mobile-nav\"><a href=\"/account\"><span>○</span>Hesap</a></nav></body></html>"""
        session = {"username": "ahmet", "role": "ADMIN", "csrf": "csrf-1"}
        result = app.enhance_dashboard(body, session, is_admin=True, pending_count=3)
        self.assertIn('action="/logout"', result)
        self.assertIn('value="csrf-1"', result)
        self.assertIn('/admin/center', result)
        self.assertIn('v32-mobile-admin-nav', result)
        self.assertIn('>3</i>', result)
        self.assertIn('Yönetim Merkezi', result)

    def test_member_dashboard_gets_logout_but_not_admin_links(self):
        body = """<html><head><style></style></head><body><header></header><nav class=\"mobile-nav\"></nav></body></html>"""
        session = {"username": "uye", "role": "MEMBER", "csrf": "csrf-2"}
        result = app.enhance_dashboard(body, session, is_admin=False)
        self.assertIn('action="/logout"', result)
        self.assertNotIn('v32-mobile-admin-nav', result)
        self.assertNotIn('href="/admin/center"', result)

    def test_standalone_controls_include_logout_on_mobile_and_desktop_pages(self):
        body = "<html><head><style></style></head><body><main>Sayfa</main></body></html>"
        session = {"username": "uye", "role": "MEMBER", "csrf": "csrf-3"}
        result = app.enhance_standalone(body, session, is_admin=False)
        self.assertIn('v32-session-float', result)
        self.assertIn('action="/logout"', result)
        self.assertIn('href="/"', result)

    def test_admin_snapshot_is_detailed_and_aggregate(self):
        snap = app.admin_snapshot(FakeConfig(), FakeStore(), FakeService())
        self.assertEqual(snap["dynamic_users"], 3)
        self.assertEqual(snap["premium"], 1)
        self.assertEqual(snap["free"], 1)
        self.assertEqual(snap["active"], 2)
        self.assertEqual(snap["passive"], 1)
        self.assertEqual(snap["admins"], 2)  # founder + dynamic admin
        self.assertEqual(snap["payment_counts"]["pending"], 1)
        self.assertEqual(snap["payment_counts"]["approved"], 1)
        self.assertEqual(snap["payment_counts"]["rejected"], 1)
        self.assertEqual(snap["open_count"], 2)
        self.assertEqual(snap["tp"], 1)
        self.assertEqual(snap["sl"], 1)
        self.assertEqual(snap["health"], "GREEN")

    def test_admin_center_contains_users_payments_system_and_logout(self):
        session = {"username": "founder", "role": "ADMIN", "csrf": "csrf-admin"}
        settings = {"package_name": "Premium 30 Gün", "package_code": "PREMIUM_30D", "days": 30, "price_label": "499 TL"}
        body = app.admin_center_page(FakeConfig(), FakeStore(), FakeService(), session, settings)
        self.assertIn("Yönetim Merkezi", body)
        self.assertIn("Kullanıcı Yönetimi", body)
        self.assertIn("Üyelik &amp; Ödemeler", body)
        self.assertIn("Teknik Sistem", body)
        self.assertIn("Dinamik kullanıcı", body)
        self.assertIn("Bekleyen ödeme", body)
        self.assertIn("Sistem sağlığı", body)
        self.assertIn("499 TL", body)
        self.assertIn('action="/logout"', body)
        self.assertNotIn("password_hash", body)


if __name__ == "__main__":
    unittest.main()
