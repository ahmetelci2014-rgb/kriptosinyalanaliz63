from __future__ import annotations

import unittest
from pathlib import Path

import dashboard_commercial_app as commercial
import dashboard_mobile_server_app as mobile


class HeaderStub(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class MobileServerTests(unittest.TestCase):
    def setUp(self):
        self.session = {"username": "uye", "role": "MEMBER", "csrf": "csrf-test"}
        self.data = {
            "open_trades": [
                {"symbol": "BTCUSDT", "direction": "LONG", "entry": 100, "tp1": 102, "sl": 98, "system_label": "Premium"}
            ],
            "recent_results": [
                {"symbol": "ETHUSDT", "direction": "SHORT", "outcome": "TP1", "r_result": 1.0, "system_label": "Scalp"}
            ],
            "health": {"overall": "GREEN"},
        }

    def test_mobile_detection(self):
        self.assertTrue(mobile.mobile_request(HeaderStub({"User-Agent": "Mozilla/5.0 iPhone Mobile"})))
        self.assertTrue(mobile.mobile_request(HeaderStub({"Sec-CH-UA-Mobile": "?1"})))
        self.assertFalse(mobile.mobile_request(HeaderStub({"User-Agent": "Mozilla/5.0 Windows NT 10.0"})))
        self.assertFalse(mobile.mobile_request(HeaderStub({"User-Agent": "iPhone Mobile"}), {"desktop": ["1"]}))

    def test_premium_mobile_is_server_rendered_and_has_real_levels(self):
        body = mobile.mobile_page(
            self.session, self.data,
            plan=commercial.PLAN_PREMIUM,
            plan_label="Premium",
            view="home",
            is_admin=False,
        )
        self.assertIn("BTCUSDT", body)
        self.assertIn("100", body)
        self.assertIn("ETHUSDT", body)
        self.assertIn("Premium", body)
        self.assertIn('/mobile?view=signals', body)
        self.assertNotIn("<script", body.lower())
        self.assertNotIn("javascript:", body.lower())

    def test_free_mobile_never_exposes_signal_levels_or_symbols(self):
        body = mobile.mobile_page(
            self.session, self.data,
            plan=commercial.PLAN_FREE,
            plan_label="Ücretsiz",
            view="home",
            is_admin=False,
        )
        self.assertIn("FREE", body)
        self.assertIn("Premium'u İncele", body)
        self.assertIn("Piyasa Merkezi", body)
        self.assertNotIn("BTCUSDT", body)
        self.assertNotIn("ETHUSDT", body)
        self.assertNotIn('/mobile?view=signals', body)
        self.assertNotIn("<script", body.lower())

    def test_free_and_premium_outputs_are_materially_different(self):
        free = mobile.mobile_page(self.session, self.data, plan="FREE", plan_label="Ücretsiz", view="home", is_admin=False)
        premium = mobile.mobile_page(self.session, self.data, plan="PREMIUM", plan_label="Premium", view="home", is_admin=False)
        self.assertNotEqual(free, premium)
        self.assertNotIn("Giriş</small>", free)
        self.assertIn("Giriş</small>", premium)

    def test_admin_has_management_link(self):
        body = mobile.mobile_page(self.session, self.data, plan="ADMIN", plan_label="Yönetici", view="home", is_admin=True)
        self.assertIn('/admin/center', body)

    def test_runtime_is_presentation_only(self):
        source = Path("dashboard_mobile_server_app.py").read_text(encoding="utf-8")
        self.assertNotIn("def do_POST", source)
        self.assertIn("desktop.make_v3321_handler", source)
        self.assertIn('"signal_engine":"unchanged"', source)
        self.assertIn('"telegram":"unchanged"', source)
        self.assertIn('"trade_management":"unchanged"', source)
        self.assertIn('"ledger_write":"unchanged"', source)


if __name__ == "__main__":
    unittest.main()
