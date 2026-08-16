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
                {
                    "symbol": "BTCUSDT",
                    "direction": "LONG",
                    "entry": 100,
                    "tp1": 102,
                    "tp2": 104,
                    "tp3": 106,
                    "sl": 98,
                    "score": 91,
                    "system_label": "Premium",
                }
            ],
            "recent_results": [
                {"symbol": "ETHUSDT", "direction": "SHORT", "outcome": "TP1", "r_result": 1.0, "system_label": "Scalp"}
            ],
            "health": {"overall": "GREEN"},
            "data_quality": {"ok": True},
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

    def test_signal_primary_levels_are_entry_tp1_sl_and_secondary_levels_are_collapsed(self):
        body = mobile.mobile_page(
            self.session, self.data,
            plan=commercial.PLAN_PREMIUM,
            plan_label="Premium",
            view="signals",
            is_admin=False,
        )
        card = mobile.signal_card(self.data["open_trades"][0])
        self.assertIn("Giriş</small>", card)
        self.assertEqual(card.count("TP1</small>"), 1)
        self.assertEqual(card.count("SL</small>"), 1)
        self.assertIn("Detayları göster", card)
        self.assertIn("TP2</small>", card)
        self.assertIn("TP3</small>", card)
        self.assertIn("Skor</small>", card)
        self.assertIn("<details", body)
        self.assertNotIn("<script", body.lower())

    def test_long_lists_are_progressively_disclosed(self):
        data = dict(self.data)
        data["recent_results"] = [
            {"symbol": f"C{i}USDT", "direction": "LONG", "outcome": "TP1", "r_result": 1.0, "system_label": "Scalp"}
            for i in range(20)
        ]
        body = mobile.mobile_page(
            self.session, data,
            plan=commercial.PLAN_PREMIUM,
            plan_label="Premium",
            view="results",
            is_admin=False,
        )
        self.assertIn("Daha eski sonuçları göster (8)", body)
        self.assertIn("C0USDT", body)
        self.assertIn("C19USDT", body)
        self.assertIn('class="more-list"', body)

    def test_bottom_navigation_is_plain_links_and_fixed_without_javascript(self):
        body = mobile.mobile_page(
            self.session, self.data,
            plan=commercial.PLAN_PREMIUM,
            plan_label="Premium",
            view="trades",
            is_admin=False,
        )
        self.assertIn('class="bottomnav nav5"', body)
        self.assertIn('href="/mobile?view=signals"', body)
        self.assertIn('href="/mobile?view=trades"', body)
        self.assertIn('href="/mobile?view=results"', body)
        self.assertIn("position:fixed", body)
        self.assertNotIn("<script", body.lower())

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
        self.assertIn("Premium'da neler açılır?", body)
        self.assertIn('class="bottomnav nav4"', body)
        self.assertNotIn("BTCUSDT", body)
        self.assertNotIn("ETHUSDT", body)
        self.assertNotIn('/mobile?view=signals', body)
        self.assertNotIn('href="/coin-center', body)
        self.assertNotIn("<script", body.lower())

    def test_free_and_premium_outputs_are_materially_different(self):
        free = mobile.mobile_page(self.session, self.data, plan="FREE", plan_label="Ücretsiz", view="home", is_admin=False)
        premium = mobile.mobile_page(self.session, self.data, plan="PREMIUM", plan_label="Premium", view="home", is_admin=False)
        self.assertNotEqual(free, premium)
        self.assertNotIn("Giriş</small>", free)
        self.assertIn("Giriş</small>", premium)
        self.assertNotIn('href="/coin-center', free)
        self.assertIn('href="/coin-center', premium)

    def test_admin_has_management_link(self):
        body = mobile.mobile_page(self.session, self.data, plan="ADMIN", plan_label="Yönetici", view="home", is_admin=True)
        self.assertIn('/admin/center', body)

    def test_runtime_is_presentation_only(self):
        source = Path("dashboard_mobile_server_app.py").read_text(encoding="utf-8")
        self.assertNotIn("def do_POST", source)
        self.assertIn("desktop.make_v3321_handler", source)
        self.assertIn('"mobile_progressive_disclosure":True', source)
        self.assertIn('"mobile_primary_levels":"entry_tp1_sl"', source)
        self.assertIn('"signal_engine":"unchanged"', source)
        self.assertIn('"telegram":"unchanged"', source)
        self.assertIn('"trade_management":"unchanged"', source)
        self.assertIn('"ledger_write":"unchanged"', source)


if __name__ == "__main__":
    unittest.main()
