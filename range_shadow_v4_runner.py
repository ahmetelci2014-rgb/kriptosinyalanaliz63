"""Range Cycle Shadow V4 kalite revizyonu.

Bu dosya yalnız gölge motorunu ayarlar. Canlı Premium/Scalp/Pump/Swing
kurallarına, Telegram'a veya emir akışına dokunmaz.

V3 teşhisinde görülen ana sorunlar:
- %0.35 altı riskte işlem maliyeti R'yi aşırı büyütüyor.
- Tek teyitli girişler zayıf.
- Düşük hacim ve trend baskısı range yapısını sık bozuyor.

V4 bu grupları daha sanal pozisyon açılmadan eler ve ayrı ledger kullanır.
"""
from __future__ import annotations

import sys
from typing import Any, Dict, Optional

import range_shadow as base

VERSION = "RANGE_CYCLE_SHADOW_V4_QUALITY_REDESIGN_2026_08_18"
LEDGER_FILE = "range_shadow_v4.json"

# V3 kanıtına dayalı, yalnız gölge V4 eşikleri.
MIN_RISK_PERCENT_V4 = 0.35
MIN_CONFIRMATIONS_V4 = 2
MIN_VOLUME_RATIO_V4 = 0.70
MAX_ADX_5M_V4 = 22.0
MAX_ADX_15M_V4 = 24.0
MIN_EXPECTED_TARGET_R_V4 = 1.30
MIN_RANGE_WIDTH_PERCENT_V4 = 0.75
MIN_CONTAINMENT_RATIO_V4 = 0.88


def apply_v4_overrides() -> None:
    base.VERSION = VERSION
    base.LEDGER_FILE = LEDGER_FILE
    base.MIN_RANGE_WIDTH_PERCENT = MIN_RANGE_WIDTH_PERCENT_V4
    base.MIN_CONTAINMENT_RATIO = MIN_CONTAINMENT_RATIO_V4
    base.MIN_VOLUME_RATIO = MIN_VOLUME_RATIO_V4
    base.MAX_ADX_5M = MAX_ADX_5M_V4
    base.MAX_ADX_15M = MAX_ADX_15M_V4
    base.MIN_EXPECTED_TARGET_R = MIN_EXPECTED_TARGET_R_V4

    original = base.evaluate_entry_candidate

    # Aynı process içinde iki kez patch edilmesini engelle.
    if getattr(original, "_range_v4_wrapped", False):
        return

    def evaluate_entry_candidate_v4(
        symbol: str,
        frame_5m: Any,
        range_info: Dict[str, Any],
        guard_15m: Optional[Dict[str, Any]] = None,
        quote_volume: float = 0.0,
    ) -> Optional[Dict[str, Any]]:
        candidate = original(
            symbol,
            frame_5m,
            range_info,
            guard_15m,
            quote_volume,
        )
        if not candidate:
            return None

        risk_percent = base.safe_float(candidate.get("risk_percent"))
        volume_ratio = base.safe_float(candidate.get("volume_ratio_5m"))
        expected_r = base.safe_float(candidate.get("expected_target_r"))
        adx_5m = base.safe_float(candidate.get("adx_5m"))
        adx_15m = base.safe_float(candidate.get("adx_15m"))
        confirmations = list(candidate.get("confirmation") or [])

        # V3'te en büyük maliyet kaynağı çok dar stoplardı.
        if risk_percent < MIN_RISK_PERCENT_V4:
            return None

        # Tek mum/tek fitil teyidi artık sanal pozisyon açmak için yetmez.
        if len(confirmations) < MIN_CONFIRMATIONS_V4:
            return None

        if volume_ratio < MIN_VOLUME_RATIO_V4:
            return None

        # Range içinde trend basıncı yükselmişse bant dönüşü varsayımı zayıflar.
        if adx_5m >= MAX_ADX_5M_V4 or adx_15m >= MAX_ADX_15M_V4:
            return None

        if expected_r < MIN_EXPECTED_TARGET_R_V4:
            return None

        candidate["shadow_revision"] = "V4_QUALITY_REDESIGN"
        candidate["v4_quality_gate"] = {
            "min_risk_percent": MIN_RISK_PERCENT_V4,
            "min_confirmations": MIN_CONFIRMATIONS_V4,
            "min_volume_ratio": MIN_VOLUME_RATIO_V4,
            "max_adx_5m": MAX_ADX_5M_V4,
            "max_adx_15m": MAX_ADX_15M_V4,
            "min_expected_target_r": MIN_EXPECTED_TARGET_R_V4,
        }
        return candidate

    evaluate_entry_candidate_v4._range_v4_wrapped = True  # type: ignore[attr-defined]
    base.evaluate_entry_candidate = evaluate_entry_candidate_v4


def self_test() -> None:
    apply_v4_overrides()
    assert base.VERSION == VERSION
    assert base.LEDGER_FILE == LEDGER_FILE
    assert base.MIN_RANGE_WIDTH_PERCENT == MIN_RANGE_WIDTH_PERCENT_V4
    assert base.MIN_VOLUME_RATIO == MIN_VOLUME_RATIO_V4
    assert base.MAX_ADX_5M == MAX_ADX_5M_V4
    assert base.MAX_ADX_15M == MAX_ADX_15M_V4
    assert base.MIN_EXPECTED_TARGET_R == MIN_EXPECTED_TARGET_R_V4
    # Eski motorun saf hesap/testleri de çalışmaya devam etmeli.
    base.self_test()
    print("Range Shadow V4 runner self-test BASARILI")


def main() -> None:
    apply_v4_overrides()
    if "--self-test" in sys.argv:
        self_test()
        return
    base.run_cycle(LEDGER_FILE)


if __name__ == "__main__":
    main()
