#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import tempfile
import unittest
from pathlib import Path

import decision_engine as de


class DecisionEngineTests(unittest.TestCase):
    def write_json(self, root: Path, name: str, data):
        (root / name).write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def seed_empty_sources(self, root: Path):
        for filename in de.FILES.values():
            self.write_json(root, filename, {})

    def test_swing_stop_heavy_becomes_live_stop(self):
        state = {}
        ledger = {
            "summary": {
                "total": 44,
                "direction_correct": 16,
                "direction_wrong": 23,
                "direction_mixed": 3,
                "tp3": 3,
                "stop": 23,
                "breakeven": 15,
                "expired": 0,
                "early_15m": 10,
                "confirmed_1h": 34,
                "long": 0,
                "short": 44,
            }
        }
        result = de.summarize_swing(state, ledger)
        self.assertEqual(result["decision_code"], "CANLI_DURDUR")
        self.assertFalse(result["auto_apply"])

    def test_range_negative_large_sample_is_rejected_for_live(self):
        data = {
            "summary": {
                "total_opened": 281,
                "total_closed": 280,
                "completed_cycle_legs": 42,
                "sl_count": 230,
                "gross_r": -25.2767,
                "net_r": -188.825,
                "average_cost_r_per_closed": 0.5841,
                "win_rate_percent": 17.14,
            }
        }
        result = de.summarize_range(data)
        self.assertEqual(result["decision_code"], "CANLIYA_ALMA_YENIDEN_TASARLA")
        self.assertEqual(result["confidence"], "YUKSEK")

    def test_momentum_that_blocks_more_winners_stays_shadow(self):
        data = {
            "summary": {
                "total_records": 13,
                "resolved_records": 13,
                "would_block_records": 3,
                "blocked_winners": 2,
                "blocked_losers": 1,
                "passed_winners": 9,
                "passed_losers": 1,
            }
        }
        result = de.summarize_momentum(data)
        self.assertEqual(result["decision_code"], "GOLGEDE_TUT_CANLIYA_ALMA")

    def test_pump_small_clean_sample_is_keep_observe(self):
        ledger = {
            "summary": {
                "total": 11,
                "tp3": 4,
                "stop": 2,
                "breakeven": 4,
                "expired": 1,
            }
        }
        result = de.summarize_pump({}, ledger)
        self.assertEqual(result["decision_code"], "KORU_IZLE")

    def test_scalp_current_profile_requests_setup_split(self):
        ledger = {
            "summary": {
                "total": 600,
                "real_signal": 26,
                "tp3": 4,
                "stop": 9,
                "breakeven": 13,
                "expired": 0,
            }
        }
        result = de.summarize_scalp({}, ledger)
        self.assertEqual(result["decision_code"], "SETUPLARI_AYIR_GOLGE_TEST")

    def test_portfolio_favorable_block_records_trigger_relax_shadow_test(self):
        data = {
            "summary": {
                "tracked_records": 200,
                "completed_records": 180,
                "by_decision": {
                    "BLOCK": {
                        "records": 100,
                        "first_0_5_favorable": 65,
                        "first_0_5_adverse": 35,
                        "first_0_5_ambiguous": 0,
                        "first_0_5_none": 0,
                    },
                    "ALLOW": {
                        "records": 100,
                        "first_0_5_favorable": 50,
                        "first_0_5_adverse": 50,
                        "first_0_5_ambiguous": 0,
                        "first_0_5_none": 0,
                    },
                },
                "by_block_code": {},
            }
        }
        result = de.summarize_portfolio(data)
        self.assertEqual(result["decision_code"], "LIMIT_GEVSETME_GOLGE_TEST")
        self.assertFalse(result["auto_apply"])

    def test_post_result_requires_completed_samples_before_change(self):
        trades = {}
        for i in range(5):
            trades[f"T{i}"] = {
                "final_result": "TP1_SONRASI_BE",
                "post_result_shadow": {
                    "final_result": "TP1_SONRASI_BE",
                    "status": "COMPLETED",
                    "reached_levels": {"TP2": {"first_reached_at": 1}},
                    "max_favorable_r": 1.0,
                    "max_adverse_r": 0.2,
                    "checkpoints": {"240": {"directional_r_from_reference": 0.5}},
                },
            }
        result = de.summarize_post_result({"trades": trades})
        self.assertEqual(result["decision_code"], "IZLE")
        self.assertFalse(result["auto_apply"])

    def test_post_result_reads_v3_model_report_without_auto_apply(self):
        trades = {}
        for i in range(20):
            trades[f"T{i}"] = {
                "final_result": "TP3",
                "post_result_shadow": {
                    "final_result": "TP3",
                    "status": "COMPLETED",
                    "reached_levels": {},
                    "max_favorable_r": 1.0,
                    "max_adverse_r": 0.5,
                    "checkpoints": {},
                },
            }
        v3 = {
            "models": {
                "TP3_RUNNER_TRAIL_0_5R": {
                    "sample": 20,
                    "average_incremental_r": 0.25,
                }
            }
        }
        result = de.summarize_post_result({"trades": trades}, {}, v3)
        self.assertEqual(result["decision_code"], "YONETIM_ALTERNATIFI_GOLGE_TEST")
        self.assertEqual(
            result["metrics"]["v3_report"]["models"]["TP3_RUNNER_TRAIL_0_5R"]["sample"],
            20,
        )
        self.assertFalse(result["auto_apply"])

    def test_build_report_never_auto_applies(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.seed_empty_sources(root)
            report = de.build_report(str(root), current_ts=1786389000)
            self.assertFalse(report["auto_apply"])
            self.assertFalse(report["executive"]["auto_apply"])
            for component in report["components"].values():
                self.assertFalse(component["auto_apply"])


if __name__ == "__main__":
    unittest.main()
