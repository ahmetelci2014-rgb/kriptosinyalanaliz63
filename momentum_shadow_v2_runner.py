"""Momentum Shadow V2 — daha konservatif WOULD_BLOCK hipotezi.

V1'de engellenmek istenen 9 işlemin 5'i kazanan çıktı. Bu yüzden V2,
tekil momentum zayıflıklarını canlı engel adayı saymaz; ancak aynı anda
birden fazla yapısal terslik varsa WOULD_BLOCK üretir.

Yalnız gölge kararı değişir. Canlı 5M_RADAR sinyali, Telegram ve emir akışı
etkilenmez.
"""
from __future__ import annotations

import sys
from typing import Any, Dict

import momentum_shadow as base

VERSION = "MOMENTUM_SHADOW_V2_CONSERVATIVE_BLOCK_2026_08_18"


def apply_v2_overrides() -> None:
    base.MOMENTUM_VERSION = VERSION
    original = base.evaluate_feature_snapshot

    if getattr(original, "_momentum_v2_wrapped", False):
        return

    def evaluate_feature_snapshot_v2(features: Dict[str, Any]) -> Dict[str, Any]:
        result = dict(original(features))

        entry_distance = base.safe_float(features.get("entry_distance_percent"), 0.0) or 0.0
        market_bad = not bool(features.get("market_guard_allowed"))
        slopes_bad = (
            not bool(features.get("slope_5m_ok"))
            and not bool(features.get("slope_15m_ok"))
        )
        macds_bad = (
            not bool(features.get("macd_5m_ok"))
            and not bool(features.get("macd_15m_ok"))
        )
        severe_entry = entry_distance > base.SEVERE_ENTRY_DISTANCE
        shadow_score = int(base.safe_float(result.get("shadow_score"), 0) or 0)

        # V1 tek/orta zayıflıkları fazla cezalandırdı. V2 yalnız birleşik
        # yapısal terslikleri blok hipotezi olarak işaretler.
        structural_conflicts = sum(
            int(flag)
            for flag in (market_bad, slopes_bad, macds_bad, severe_entry)
        )

        would_block = bool(
            shadow_score < 40
            or (market_bad and (slopes_bad or macds_bad))
            or (severe_entry and slopes_bad and macds_bad)
            or structural_conflicts >= 3
        )

        result["version"] = VERSION
        result["would_block"] = would_block
        result["decision"] = (
            "WOULD_BLOCK"
            if would_block
            else ("CAUTION" if shadow_score < 75 or result.get("cautions") else "PASS")
        )
        result["v2_block_diagnostics"] = {
            "market_guard_bad": market_bad,
            "both_ema_slopes_bad": slopes_bad,
            "both_macd_timeframes_bad": macds_bad,
            "severe_entry_distance": severe_entry,
            "structural_conflict_count": structural_conflicts,
            "rule": "Yalniz birlesik yapisal tersliklerde WOULD_BLOCK",
        }
        return result

    evaluate_feature_snapshot_v2._momentum_v2_wrapped = True  # type: ignore[attr-defined]
    base.evaluate_feature_snapshot = evaluate_feature_snapshot_v2


def self_test() -> None:
    apply_v2_overrides()

    healthy = {
        "direction": "LONG",
        "adx_4h": 30.0,
        "adx_1h": 24.0,
        "volume_ratio_15m": 1.2,
        "entry_distance_percent": 0.10,
        "slope_5m_ok": True,
        "slope_15m_ok": True,
        "macd_5m_ok": True,
        "macd_15m_ok": True,
        "candle_direction_ok": True,
        "rejection_ok": True,
        "recent_retest": True,
        "market_guard_allowed": True,
    }
    healthy_result = base.evaluate_feature_snapshot(healthy)
    assert healthy_result["would_block"] is False

    # Tek başına piyasa guard zayıflığı artık otomatik blok değildir.
    one_warning = dict(healthy)
    one_warning["market_guard_allowed"] = False
    warning_result = base.evaluate_feature_snapshot(one_warning)
    assert warning_result["would_block"] is False

    # Piyasa ters + iki zaman diliminde EMA ve MACD ters ise blok hipotezi.
    broken = dict(one_warning)
    broken.update({
        "slope_5m_ok": False,
        "slope_15m_ok": False,
        "macd_5m_ok": False,
        "macd_15m_ok": False,
    })
    broken_result = base.evaluate_feature_snapshot(broken)
    assert broken_result["would_block"] is True
    assert broken_result["version"] == VERSION
    print("Momentum Shadow V2 runner self-test BASARILI")


def main() -> None:
    apply_v2_overrides()
    if "--self-test" in sys.argv:
        self_test()
        return
    base.main()


if __name__ == "__main__":
    main()
