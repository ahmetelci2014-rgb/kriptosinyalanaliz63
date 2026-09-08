import unittest

import market_first_observability_report as report


class MarketFirstObservabilityReportTests(unittest.TestCase):
    def _episode(self, symbol, *, condition=False, promoted=False, sent=False, tp1=False, tp2=False, tp3=False, reason=None, guard=None, best_r=0.0):
        row = {
            "episode_id": f"{symbol}:LONG:1000",
            "symbol": symbol,
            "direction": "LONG",
            "first_at": 1000,
            "prep_price": 1.0,
            "resolved": True,
            "entry_condition_met": condition,
            "entry_condition_at": 1100 if condition else None,
            "entry_promoted": promoted,
            "entry_signal_sent": sent,
            "entry_send_failed": False,
            "tp1_at": 1600 if tp1 else None,
            "tp2_at": 1700 if tp2 else None,
            "tp3_at": 1800 if tp3 else None,
            "tp1_before_entry_signal": bool(tp1 and not sent),
            "best_favorable_percent": 2.2 if tp3 else 1.4 if tp2 else 0.8 if tp1 else 0.1,
            "best_favorable_r": best_r,
            "worst_adverse_percent": 0.2,
            "outcome": "TP3_REACHED" if tp3 else "TP2_REACHED" if tp2 else "TP1_REACHED" if tp1 else "NO_TARGET",
            "latest_plan": {
                "score": 86,
                "market_regime": "CHOP",
                "structure_5m": "LONG",
                "structure_15m": "LONG",
                "structure_1h": "LONG",
                "volume_ratio_5m": 0.9,
                "volume_ratio_15m": 1.1,
                "extension_atr_5m": 0.4,
                "risk_percent": 0.7,
                "room_r": 2.0,
            },
        }
        if reason:
            row["entry_not_promoted_reason"] = reason
        if guard:
            row["entry_guard_reason"] = guard
            row["entry_guard_blocked"] = True
        return row

    def test_funnel_and_block_reasons(self):
        episodes = {
            "A": self._episode("AUSDT", tp1=True, tp2=True, tp3=True, best_r=3.0),
            "B": self._episode("BUSDT", condition=True, tp1=True, tp2=True, guard="ENTRY_PLAN_STALE_MICRO", best_r=2.0),
            "C": self._episode("CUSDT", condition=True, promoted=True, tp1=True, best_r=1.0),
            "D": self._episode("DUSDT", condition=True, promoted=True, sent=True, tp1=True, best_r=1.0),
        }
        ledger = {"version": "TEST", "episodes": episodes}
        conversion, missed = report.build_reports(
            ledger,
            {"entry_plan_clean": {}},
            {"deep_scan_count": 100, "market": {"regime": "CHOP"}, "rejection_counts": {"LOW_SCORE": 4}},
            generated_at=2000,
        )
        funnel = conversion["funnel"]
        self.assertEqual(funnel["clean_preparations"], 4)
        self.assertEqual(funnel["entry_condition_met"], 3)
        self.assertEqual(funnel["entry_promoted"], 2)
        self.assertEqual(funnel["entry_signal_sent"], 1)
        self.assertEqual(conversion["promotion_block_reasons"]["ENTRY_PLAN_STALE_MICRO"], 1)
        self.assertEqual(conversion["promoted_but_unsent_reasons"]["PROMOTED_BUT_FINAL_SEND_NOT_CONFIRMED"], 1)
        self.assertAlmostEqual(conversion["conversion_rates"]["entry_condition_to_real_signal"], 1 / 3, places=4)
        self.assertEqual(missed["total_missed_moves"], 3)

    def test_missed_moves_are_ranked_and_staged(self):
        episodes = {
            "A": self._episode("AUSDT", tp1=True, tp2=True, tp3=True, best_r=3.2),
            "B": self._episode("BUSDT", condition=True, tp1=True, tp2=True, guard="ENTRY_PLAN_STALE_MICRO", best_r=2.1),
            "C": self._episode("CUSDT", condition=True, promoted=True, tp1=True, best_r=1.1),
        }
        _, missed = report.build_reports(
            {"episodes": episodes}, {}, {}, generated_at=2000
        )
        top = missed["top_missed"]
        self.assertEqual(top[0]["symbol"], "AUSDT")
        self.assertEqual(top[0]["miss_level"], "TP3_MISSED")
        self.assertEqual(missed["stages"]["BEFORE_ENTRY_CONDITION"], 1)
        self.assertEqual(missed["stages"]["PROMOTION_GATE"], 1)
        self.assertEqual(missed["stages"]["FINAL_DELIVERY_GATE"], 1)
        self.assertEqual(missed["reasons"]["ENTRY_PLAN_STALE_MICRO"], 1)
        self.assertEqual(missed["timing"]["tp1_within_30m"], 3)

    def test_legacy_rows_are_excluded_from_clean_stats(self):
        legacy = self._episode("OLDUSDT", condition=True, tp1=True, tp2=True, tp3=True, best_r=3.0)
        legacy["exclude_from_clean_prep_stats"] = True
        current = self._episode("NEWUSDT", condition=True, promoted=True, sent=True, tp1=True, best_r=1.0)
        conversion, missed = report.build_reports(
            {"episodes": {"old": legacy, "new": current}}, {}, {}, generated_at=2000
        )
        self.assertEqual(conversion["funnel"]["clean_preparations"], 1)
        self.assertEqual(conversion["funnel"]["entry_signal_sent"], 1)
        self.assertEqual(missed["total_missed_moves"], 0)

    def test_history_is_capped_and_replaces_same_timestamp(self):
        conversion = {"generated_at": 50, "market_regime": "CHOP", "deep_scan_count": 10, "funnel": {}, "conversion_rates": {}, "diagnosis": {}}
        missed = {"total_missed_moves": 2, "miss_rate_of_clean_resolved": 0.2, "levels": {}, "stages": {}}
        history = {"snapshots": [{"generated_at": 50, "old": True}]}
        updated = report._update_history(history, conversion, missed)
        self.assertEqual(len(updated["snapshots"]), 1)
        self.assertEqual(updated["snapshots"][0]["total_missed_moves"], 2)


if __name__ == "__main__":
    unittest.main()
