from __future__ import annotations

import inspect
import unittest

import dashboard_mobile_server_app as mobile
import dashboard_share_runtime_app as runtime
import dashboard_shareui_app as shareui


OPEN = {
    "symbol": "BTCUSDT",
    "direction": "LONG",
    "system": "Premium MTF",
    "entry": 100.0,
    "tp1": 102.0,
    "tp2": 104.0,
    "tp3": 106.0,
    "sl": 98.0,
    "score": 91,
    "opened_at": 1_765_000_000,
}
RESULT = {**OPEN, "outcome": "TP2", "r_result": 1.4, "closed_at": 1_765_003_600}


class MobileShareFixTests(unittest.TestCase):
    def test_current_mobile_trade_page_is_detected_and_gets_share_link(self):
        data = {"open_trades": [OPEN], "recent_results": [RESULT]}
        raw = mobile.mobile_page(
            {"username": "member", "csrf": "csrf-test"},
            data,
            plan="PREMIUM",
            plan_label="Premium",
            view="trades",
            is_admin=False,
        )
        self.assertNotIn("Mobil · sunucu görünümü", raw)
        self.assertTrue(shareui.is_mobile_server_page(raw))
        enhanced = shareui.enhance_mobile(raw, data, view="trades")
        self.assertIn('class="share-mobile"', enhanced)
        self.assertIn("kind=open", enhanced)
        self.assertIn("stage=tracking", enhanced)
        self.assertIn("BTCUSDT", enhanced)
        self.assertNotIn("<script", enhanced.lower())

    def test_signal_and_result_pages_are_detected_too(self):
        data = {"open_trades": [OPEN], "recent_results": [RESULT]}
        for view in ("signals", "results"):
            raw = mobile.mobile_page(
                {"username": "member", "csrf": "csrf-test"},
                data,
                plan="PREMIUM",
                plan_label="Premium",
                view=view,
                is_admin=False,
            )
            self.assertTrue(shareui.is_mobile_server_page(raw))
            enhanced = shareui.enhance_mobile(raw, data, view=view)
            self.assertIn('class="share-mobile"', enhanced)

    def test_runtime_uses_structural_mobile_detection_not_removed_copy(self):
        source = inspect.getsource(runtime)
        self.assertIn("shareui.is_mobile_server_page(body)", source)
        self.assertNotIn('"Mobil · sunucu görünümü" in body', source)
        self.assertIn("V3_32_9_SHARE_CARDS_MOBILE_FIX", runtime.VERSION)
        self.assertIn('"mobile_share_injection": "structural_server_mobile_detection"', source)

    def test_free_mobile_is_not_mistaken_for_premium_trade_surface(self):
        raw = mobile.mobile_page(
            {"username": "free", "csrf": "csrf-test"},
            {"open_trades": [OPEN], "recent_results": [RESULT]},
            plan="FREE",
            plan_label="Ücretsiz",
            view="home",
            is_admin=False,
        )
        self.assertFalse(shareui.is_mobile_server_page(raw))
        self.assertNotIn("BTCUSDT", raw)
        self.assertNotIn("share-mobile", raw)


if __name__ == "__main__":
    unittest.main()
