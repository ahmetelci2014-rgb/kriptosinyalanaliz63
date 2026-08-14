import unittest

import swing_shadow_v4 as v4


def candidate(direction="LONG"):
    return {
        "symbol": "TESTUSDT", "direction": direction, "score": 90,
        "entry": 100.0, "sl": 99.0 if direction == "LONG" else 101.0,
        "tp1": 100.8 if direction == "LONG" else 99.2,
        "tp2": 101.6 if direction == "LONG" else 98.4,
        "tp3": 102.5 if direction == "LONG" else 97.5,
        "risk_percent": 1.0, "signal_candle_ms": 1000,
        "setup": "TEST", "diagnostics": {},
    }


class SwingShadowV4Tests(unittest.TestCase):
    def test_quote_volume_falls_back_to_okx_info(self):
        self.assertEqual(
            v4.safe_quote_volume({"quoteVolume": None, "info": {"volCcy24h": "2500000"}}),
            2500000.0,
        )

    def test_same_candle_stop_and_tp_is_stop_first(self):
        item = v4.build_position(candidate("LONG"), current_ts=2)
        candle = {"timestamp": 2000, "open": 100, "high": 101, "low": 98.9, "close": 100}
        result = v4.simulate_position(item, [candle], current_ts=3)
        self.assertEqual(result["final_result"], "SL")

    def test_tp3_closes_positive(self):
        item = v4.build_position(candidate("LONG"), current_ts=2)
        candle = {"timestamp": 2000, "open": 100, "high": 102.6, "low": 99.2, "close": 102.5}
        result = v4.simulate_position(item, [candle], current_ts=3)
        self.assertEqual(result["final_result"], "TP3")
        self.assertGreater(result["net_r"], 0)

    def test_direction_gate_blocks_over_seventy_percent(self):
        ledger = v4.empty_ledger()
        ledger["closed_positions"] = [
            {"direction": "SHORT", "opened_at": i} for i in range(7)
        ] + [{"direction": "LONG", "opened_at": 8} for _ in range(3)]
        self.assertFalse(v4.direction_allowed(ledger, "SHORT"))
        self.assertTrue(v4.direction_allowed(ledger, "LONG"))

    def test_live_candidate_requires_all_gates(self):
        ledger = v4.empty_ledger()
        for i in range(30):
            direction = "LONG" if i % 2 == 0 else "SHORT"
            result = "TP3" if i < 9 else ("SL" if i < 18 else "TP1_SONRASI_BE")
            net_r = 1.345 if result == "TP3" else (-1.08 if result == "SL" else 0.32)
            ledger["closed_positions"].append({
                "direction": direction, "final_result": result, "net_r": net_r,
            })
        summary = v4.calculate_summary(ledger)
        self.assertTrue(summary["live_candidate"])


if __name__ == "__main__":
    unittest.main()
