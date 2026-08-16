import inspect
import unittest
from types import SimpleNamespace

import dashboard_adminhub_app as adminhub
import dashboard_adminux_app as adminux
import dashboard_compact_app as compact
import dashboard_roleboundary_app as roles


class FakeStore:
    def list_commercial_users(self):
        return [
            {"username": "member", "role": "MEMBER", "plan": "PREMIUM", "active": True, "expires_at": 0, "updated_at": 1}
        ]

    def list_payments(self):
        return []


class FakeService:
    def get_data(self):
        return {
            "open_trades": [],
            "recent_results": [],
            "health": {"overall": "GREEN"},
            "data_quality": {"ok": True},
        }


class RoleBoundaryTests(unittest.TestCase):
    def test_real_current_member_nav_gets_role_boundary_layer(self):
        body = compact.compact_dashboard_page(
            {"username": "member", "role": "MEMBER", "csrf": "csrf"},
            "nonce",
        )
        self.assertIn('href="/advanced"', body)
        enhanced = roles.enhance_role_ui(body, "nonce", is_admin=False)
        self.assertIn('html[data-admin="false"] .nav-item[href="/advanced"]', enhanced)
        self.assertIn('id="v331-role-script"', enhanced)
        self.assertIn("link.remove()", enhanced)

    def test_admin_nav_keeps_technical_view_but_renames_it(self):
        body = compact.compact_dashboard_page(
            {"username": "admin", "role": "ADMIN", "csrf": "csrf"},
            "nonce",
        )
        enhanced = roles.enhance_role_ui(body, "nonce", is_admin=True)
        self.assertIn("Teknik Görünüm", enhanced)
        self.assertIn("Yalnız yönetici teknik görünümü", enhanced)

    def test_real_admin_center_keeps_tools_but_collapses_analysis_by_default(self):
        base = adminux.admin_center_page(
            SimpleNamespace(username="ahmet"),
            FakeStore(),
            FakeService(),
            {"username": "ahmet", "role": "ADMIN", "csrf": "csrf"},
            {"days": 30, "package_name": "Premium 30 Gün", "price_label": "—", "package_code": "PREMIUM_30D"},
        )
        with_hub = adminhub.enhance_admin_center(base)
        self.assertIn('id="v321AdminAnalysisHub"', with_hub)
        enhanced = roles.enhance_role_ui(with_hub, "nonce", is_admin=True)
        self.assertIn("v331-collapsed", enhanced)
        self.assertIn("Analiz araçlarını göster", enhanced)
        self.assertIn("YALNIZ ADMIN", enhanced)
        self.assertIn("/admin/memberships", enhanced)

    def test_advanced_admin_banner_has_safe_return_paths(self):
        body = '<!doctype html><html><head><style></style></head><body><main>advanced</main></body></html>'
        enhanced = roles.enhance_advanced_admin(body)
        self.assertIn("ADMIN · TEKNİK GÖRÜNÜM", enhanced)
        self.assertIn('href="/admin/center"', enhanced)
        self.assertIn('href="/"', enhanced)

    def test_server_contract_guards_advanced_and_preserves_live_core(self):
        source = inspect.getsource(roles)
        self.assertIn('if path == "/advanced"', source)
        self.assertIn("if not self._is_admin_session(session)", source)
        self.assertIn('self._redirect("/")', source)
        self.assertNotIn("def do_POST", source)
        self.assertIn("accountux.make_v330_handler", source)
        self.assertIn('"signal_engine": "unchanged"', source)
        self.assertIn('"telegram": "unchanged"', source)
        self.assertIn('"trade_management": "unchanged"', source)
        self.assertIn('"ledger_write": "unchanged"', source)


if __name__ == "__main__":
    unittest.main()
