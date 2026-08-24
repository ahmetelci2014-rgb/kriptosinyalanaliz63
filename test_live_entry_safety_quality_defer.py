import unittest
from types import SimpleNamespace

import live_entry_safety as safety


class LiveEntrySafetyQualityDeferTests(unittest.TestCase):
    def test_deferred_fast_candidate_releases_same_run_claim(self):
        claimed = {("TESTUSDT", "LONG")}

        def duplicate(signal, radar=False):
            key = (
                str(signal.get("symbol") or "").upper(),
                str(signal.get("direction") or "").upper(),
            )
            return key in claimed

        fake_runner = SimpleNamespace(
            bot=SimpleNamespace(is_duplicate=duplicate)
        )
        delivered = []

        def original(message, *args, **kwargs):
            delivered.append(message)
            return True

        sender = safety.make_entry_safety_sender(original)
        message = (
            "✅ İŞLEM GİRİŞİ — PREMIUM ERKEN HAREKET\n"
            "🟢 LONG | TESTUSDT\n"
            "⚡ Yapı: ARMED • V2 86/100\n"
            "💰 Giriş: 1.0000\n"
            "🎯 TP1: 1.0100\n"
            "🎯 TP2: 1.0200\n"
            "🎯 TP3: 1.0300\n"
            "🛑 SL: 0.9900\n"
            "⭐ Premium skor: 97/100 • A+ ERKEN BREAKOUT\n"
            "🧬 Order Flow: 53/100\n"
        )

        def _try_fast_send():
            runner = fake_runner
            signal = {"symbol": "TESTUSDT", "direction": "LONG"}
            return sender(message)

        self.assertFalse(_try_fast_send())
        self.assertNotIn(("TESTUSDT", "LONG"), claimed)
        self.assertEqual(delivered, [])

    def test_elite_fast_candidate_is_delivered_compact(self):
        delivered = []

        def original(message, *args, **kwargs):
            delivered.append(message)
            return True

        sender = safety.make_entry_safety_sender(original)
        message = (
            "✅ İŞLEM GİRİŞİ — PREMIUM ERKEN HAREKET\n"
            "🟢 LONG | TESTUSDT\n"
            "⚡ Yapı: TRIGGER • V2 95/100\n"
            "💰 Giriş: 1.0000\n"
            "🎯 TP1: 1.0100\n"
            "🎯 TP2: 1.0200\n"
            "🎯 TP3: 1.0300\n"
            "🛑 SL: 0.9900\n"
            "⭐ Premium skor: 100/100 • A+ ERKEN BREAKOUT\n"
            "📊 5M hacim: 2.00x\n"
            "🧬 Order Flow: 50/100\n"
            "📍 Anchor sapması: %0.10\n"
        )

        def _try_fast_send():
            runner = SimpleNamespace(bot=SimpleNamespace(is_duplicate=lambda *a, **k: False))
            signal = {"symbol": "TESTUSDT", "direction": "LONG"}
            return sender(message)

        self.assertTrue(_try_fast_send())
        self.assertEqual(len(delivered), 1)
        text = delivered[0]
        self.assertIn("Giriş: 1.0000", text)
        self.assertIn("TP3: 1.0300", text)
        self.assertIn("SL: 0.9900", text)
        self.assertNotIn("Premium skor", text)
        self.assertNotIn("Order Flow", text)


if __name__ == "__main__":
    unittest.main()
