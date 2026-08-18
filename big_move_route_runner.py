"""Büyük Hareket V1 çalıştırma koruması.

4H geçmişinde ilk anlamlı swing seviyesi doğrudan 3R+ uzakta olduğunda aynı
seviye hem 1. rota hem ana hedef seçilebilirdi. Bu runner hedefleri sıralı ve
ayrı tutar. Canlı emir açmaz; yalnız Telegram sinyal motorunu çalıştırır.
"""
from __future__ import annotations

import sys

import big_move_route as base


def apply_target_order_guard() -> None:
    original = base.build_route_projection
    if getattr(original, "_big_move_order_guard", False):
        return

    def guarded(h4, direction, entry, stop):
        projection = original(h4, direction, entry, stop)
        if not projection:
            return None

        risk = abs(float(entry) - float(stop))
        if risk <= 0:
            return None

        tp1 = float(projection["tp1"])
        main = float(projection["tp2"])
        extended = float(projection["tp3"])

        if direction == "LONG":
            if tp1 >= main:
                tp1 = entry + 1.50 * risk
                projection["tp1"] = tp1
                projection["tp1_r"] = 1.50
                projection["tp1_basis"] = "R_CHECKPOINT"
            if extended <= main:
                ext_r = max(5.0, float(projection.get("main_target_r") or 3.0) + 1.5)
                projection["tp3"] = entry + ext_r * risk
                projection["extended_target_r"] = round(ext_r, 3)
                projection["extended_basis"] = "R_EXTENSION"
            if not (float(projection["tp1"]) < float(projection["tp2"]) < float(projection["tp3"])):
                return None
        else:
            if tp1 <= main:
                tp1 = entry - 1.50 * risk
                projection["tp1"] = tp1
                projection["tp1_r"] = 1.50
                projection["tp1_basis"] = "R_CHECKPOINT"
            if extended >= main:
                ext_r = max(5.0, float(projection.get("main_target_r") or 3.0) + 1.5)
                projection["tp3"] = entry - ext_r * risk
                projection["extended_target_r"] = round(ext_r, 3)
                projection["extended_basis"] = "R_EXTENSION"
            if not (float(projection["tp1"]) > float(projection["tp2"]) > float(projection["tp3"])):
                return None

        return projection

    guarded._big_move_order_guard = True  # type: ignore[attr-defined]
    base.build_route_projection = guarded


def self_test() -> None:
    apply_target_order_guard()
    assert getattr(base.build_route_projection, "_big_move_order_guard", False)
    print("Big Move target order guard self-test BASARILI")


def main() -> None:
    apply_target_order_guard()
    if "--self-test" in sys.argv:
        self_test()
        return
    base.run()


if __name__ == "__main__":
    main()
