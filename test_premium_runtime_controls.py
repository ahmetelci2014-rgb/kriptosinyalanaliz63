import time
import unittest

import premium_crypto_profit_runner as runtime


class FakeMovementV2:
    def __init__(self, state):
        self._data = state

    def _state(self):
        return self._data


class FakeRunner:
    def __init__(self, state):
        self.movement_start_v2 = FakeMovementV2(state)


class PremiumRuntimeControlTests(unittest.TestCase):
    def test_trade_only_sender_allows_entries_and_suppresses_results(self):
        delivered = []

        def original(message, *args, **kwargs):
            delivered.append((message, kwargs))
            return True

        sender = runtime._make_trade_only_sender(original)

        self.assertTrue(sender("✅ İŞLEM GİRİŞİ — PREMIUM ERKEN HAREKET\nRIVERUSDT"))
        self.assertTrue(sender("🚀 PREMIUM FUTURES\nLONG | BTCUSDT"))
        self.assertTrue(sender("✅ TP1 GELDİ\nBTCUSDT", delivery_key="x|TP1"))
        self.assertTrue(sender("❌ STOP OLDU\nBTCUSDT", delivery_key="x|SL"))
        self.assertTrue(sender("📊 GÜNLÜK RAPOR"))

        self.assertEqual(len(delivered), 2)
        self.assertIn("İŞLEM GİRİŞİ", delivered[0][0])
        self.assertTrue(delivered[1][0].startswith("🚀 PREMIUM FUTURES"))

    def test_recent_trigger_and_armed_are_scanned_first(self):
        now = int(time.time())
        runner = FakeRunner({
            "open": {
                "BUSDT_LONG": {
                    "symbol": "BUSDT",
                    "best_stage": "ARMED",
                    "best_score": 84,
                    "last_updated_at": now - 60,
                    "first_resolution": None,
                },
                "CUSDT_SHORT": {
                    "symbol": "CUSDT",
                    "best_stage": "TRIGGER",
                    "best_score": 91,
                    "last_updated_at": now - 90,
                    "first_resolution": None,
                },
                "DUSDT_LONG": {
                    "symbol": "DUSDT",
                    "best_stage": "PREP",
                    "best_score": 99,
                    "last_updated_at": now - 30,
                    "first_resolution": None,
                },
            }
        })

        ordered = runtime._prioritize_movement_symbols(
            runner,
            ["AUSDT", "BUSDT", "CUSDT", "DUSDT"],
        )
        self.assertEqual(
            ordered,
            ["CUSDT", "BUSDT", "AUSDT", "DUSDT"],
        )

    def test_stale_or_resolved_candidates_do_not_get_priority(self):
        now = int(time.time())
        runner = FakeRunner({
            "open": {
                "BUSDT_LONG": {
                    "symbol": "BUSDT",
                    "best_stage": "TRIGGER",
                    "best_score": 99,
                    "last_updated_at": now - runtime.PRIORITY_STAGE_MAX_AGE_SECONDS - 1,
                    "first_resolution": None,
                },
                "CUSDT_SHORT": {
                    "symbol": "CUSDT",
                    "best_stage": "TRIGGER",
                    "best_score": 99,
                    "last_updated_at": now - 30,
                    "first_resolution": "R2_FIRST",
                },
            }
        })

        original = ["AUSDT", "BUSDT", "CUSDT"]
        self.assertEqual(
            runtime._prioritize_movement_symbols(runner, original),
            original,
        )


if __name__ == "__main__":
    unittest.main()
