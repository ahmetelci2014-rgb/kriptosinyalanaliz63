#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
import prescription_engine as pe


def make_record(ts, outcome, r, **kwargs):
    record = {
        "sent_at": ts,
        "trade_outcome": outcome,
        "trade_result_r": r,
    }
    record.update(kwargs)
    return record


class PrescriptionEngineTests(unittest.TestCase):
    def test_overfit_candidate_rejected_when_holdout_negative(self):
        train = []
        holdout = []
        # Train: low ADX mostly losers => blocking low is good.
        for i in range(20):
            if i < 8:
                train.append(make_record(1000+i, "STOP", -1.0, adx_1h=15+i*0.1, score=90))
            else:
                train.append(make_record(1000+i, "TP3", 1.075, adx_1h=30+i*0.1, score=90))
        # Holdout: low ADX winners => same filter is bad.
        for i in range(10):
            if i < 4:
                holdout.append(make_record(2000+i, "TP3", 1.075, adx_1h=15+i*0.1, score=90))
            else:
                holdout.append(make_record(2000+i, "TP3", 1.075, adx_1h=30+i*0.1, score=90))

        cand = {"kind": "NUM_LT", "field": "adx_1h", "value": 20.0}
        te = pe.evaluate_candidate_on(train, cand)
        he = pe.evaluate_candidate_on(holdout, cand)
        status, _ = pe.classify_candidate(train+holdout, train, holdout, cand, te, he)
        self.assertEqual(status, "REDDET_OVERFIT")

    def test_robust_loser_filter_can_be_shadow_or_live_candidate(self):
        records = []
        # 70 records: low vol losers, high vol winners; both train and holdout.
        for i in range(70):
            low = (i % 5 == 0)
            records.append(
                make_record(
                    1000+i,
                    "STOP" if low else "TP3",
                    -1.0 if low else 1.075,
                    vol_15m=0.3 if low else 1.4,
                    score=95,
                )
            )
        train, holdout = pe.chronological_split(records)
        cand = {"kind": "NUM_LT", "field": "vol_15m", "value": 0.8}
        te = pe.evaluate_candidate_on(train, cand)
        he = pe.evaluate_candidate_on(holdout, cand)
        status, reasons = pe.classify_candidate(records, train, holdout, cand, te, he)
        self.assertIn(status, {"GOLGE_TEST", "CANLI_ADAY"})
        self.assertTrue(reasons)

    def test_winner_killing_filter_not_live(self):
        records = []
        for i in range(70):
            records.append(
                make_record(
                    1000+i,
                    "TP3" if i < 50 else "STOP",
                    1.075 if i < 50 else -1.0,
                    score=80 if i < 35 else 100,
                    adx_1h=25,
                )
            )
        train, holdout = pe.chronological_split(records)
        cand = {"kind": "NUM_LT", "field": "score", "value": 90}
        te = pe.evaluate_candidate_on(train, cand)
        he = pe.evaluate_candidate_on(holdout, cand)
        status, _ = pe.classify_candidate(records, train, holdout, cand, te, he)
        self.assertNotEqual(status, "CANLI_ADAY")

    def test_leakage_fields_not_discovered(self):
        records = [{
            "score": 95,
            "adx_1h": 22,
            "best_favorable_r": 3.0,
            "worst_adverse_r": 1.0,
            "latest_directional_move_percent": 2.0,
            "features": {
                "vol_15m": 1.2,
                "post_result_magic": 99,
            }
        } for _ in range(10)]
        fields = pe.discover_numeric_features(records)
        self.assertIn("score", fields)
        self.assertIn("adx_1h", fields)
        self.assertIn("features.vol_15m", fields)
        self.assertNotIn("best_favorable_r", fields)
        self.assertNotIn("worst_adverse_r", fields)
        self.assertNotIn("latest_directional_move_percent", fields)
        self.assertNotIn("features.post_result_magic", fields)

    def test_v1_keep_downgrades_live_candidate_to_shadow(self):
        records = []
        for i in range(80):
            low = (i % 6 == 0)
            records.append(
                make_record(
                    1000+i,
                    "STOP" if low else "TP3",
                    -1.0 if low else 1.075,
                    vol_15m=0.2 if low else 1.5,
                    score=95,
                    source="15M_ENTRY",
                )
            )
        result = pe.analyze_filter_component(
            "PREMIUM",
            records,
            {"decision_code": "KORU"},
        )
        for item in result.get("prescriptions", []):
            self.assertNotEqual(item.get("status"), "CANLI_ADAY")

    def test_post_result_creates_runner_shadow_candidate(self):
        trades = {}
        for i in range(12):
            trades[str(i)] = {
                "final_result": "TP1_SONRASI_BE",
                "post_result_shadow": {
                    "status": "COMPLETED",
                    "final_result": "TP1_SONRASI_BE",
                    "reached_levels": {
                        "TP2": {"first_reached_at": 1},
                        **({"TP3": {"first_reached_at": 2}} if i < 4 else {}),
                    },
                    "max_favorable_r": 0.9,
                },
            }
        result = pe.analyze_post_result({"trades": trades})
        self.assertEqual(result["status"], "GOLGE_TEST")
        self.assertTrue(result["prescriptions"])

    def test_auto_apply_always_false(self):
        records = [
            make_record(1000+i, "TP3", 1.075, score=95, vol_15m=1.2)
            for i in range(20)
        ]
        result = pe.analyze_filter_component("TEST", records, {})
        self.assertFalse(result["auto_apply"])


if __name__ == "__main__":
    unittest.main()
