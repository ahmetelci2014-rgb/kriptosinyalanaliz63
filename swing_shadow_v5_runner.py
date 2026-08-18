"""Swing Shadow V5 — örneklem hızlandırma revizyonu.

V4'ün ana problemi henüz performanstan çok örneklem yetersizliği: yalnız
5 kapanmış sanal işlem var. Çekirdek D1/4H/1H/15M kurulum şartlarını
gevşetmeden taranan likit coin sayısını artırır ve tekrar süresini biraz
kısaltır. Böylece kalite mantığı aynı kalırken 30 kapanış hedefine daha
hızlı veri toplanır.

Telegram yok, gerçek emir yok, canlı filtre değişikliği yok.
"""
from __future__ import annotations

import sys

import swing_shadow_v4 as base

VERSION = "SWING_SHADOW_V5_SAMPLE_EXPANSION_2026_08_18"
MAX_SCAN_COINS_V5 = 90
DUPLICATE_HOURS_V5 = 18


def apply_v5_overrides() -> None:
    base.VERSION = VERSION
    base.MAX_SCAN_COINS = MAX_SCAN_COINS_V5
    base.DUPLICATE_HOURS = DUPLICATE_HOURS_V5


def self_test() -> None:
    apply_v5_overrides()
    assert base.VERSION == VERSION
    assert base.MAX_SCAN_COINS == MAX_SCAN_COINS_V5
    assert base.DUPLICATE_HOURS == DUPLICATE_HOURS_V5
    # Çekirdek kalite/risk hedefleri bilerek korunuyor.
    assert base.MIN_SCORE == 82
    assert base.MIN_RISK_PERCENT == 0.80
    assert base.MAX_RISK_PERCENT == 2.50
    assert base.TARGETS["minimum_closed"] == 30
    print("Swing Shadow V5 runner self-test BASARILI")


def main() -> None:
    apply_v5_overrides()
    if "--self-test" in sys.argv:
        self_test()
        return
    base.main()


if __name__ == "__main__":
    main()
