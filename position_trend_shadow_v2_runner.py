"""Ana Trend Pozisyon Shadow V2 — risk bandı düzeltmesi.

V1 gölge kayıtlarında bazı yapısal stoplar %4-%5 bandına kadar çıkabildi.
Bu, doğru trend fikrinde bile tek işlemin riskini gereksiz büyütüyor.
V2 yalnız YENİ gölge pozisyonlarda maksimum yapısal stop yüzdesini %3.0'a
indirir. Açık V1 gölge işlemlerinin stop/TP yönetimine dokunmaz.

Telegram yok, gerçek emir yok, canlı filtre değişikliği yok.
"""
from __future__ import annotations

import sys

import position_trend_shadow as base

VERSION = "POSITION_TREND_SHADOW_V2_RISK_CAP_2026_08_18"
MAX_RISK_PERCENT_V2 = 3.00


def apply_v2_overrides() -> None:
    base.VERSION = VERSION
    base.MAX_RISK_PERCENT = MAX_RISK_PERCENT_V2


def self_test() -> None:
    apply_v2_overrides()
    assert base.VERSION == VERSION
    assert base.MAX_RISK_PERCENT == MAX_RISK_PERCENT_V2
    assert base.MIN_RISK_PERCENT < base.MAX_RISK_PERCENT
    print("Position Trend Shadow V2 runner self-test BASARILI")


def main() -> None:
    apply_v2_overrides()
    if "--self-test" in sys.argv:
        self_test()
        return
    base.main()


if __name__ == "__main__":
    main()
