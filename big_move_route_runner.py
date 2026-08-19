"""Büyük Hareket V1 çalıştırma koruması.

4H geçmişinde ilk anlamlı swing seviyesi doğrudan 3R+ uzakta olduğunda aynı
seviye hem 1. rota hem ana hedef seçilebilirdi. Bu runner hedefleri sıralı ve
ayrı tutar. Ayrıca her derin taranan coin için son eleme nedenini state'e yazar.
Canlı emir açmaz; yalnız Telegram sinyal motorunu çalıştırır.
"""
from __future__ import annotations

import sys

import big_move_route as base


_SYMBOL_DIAGNOSTICS = {}


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


def apply_symbol_diagnostics() -> None:
    original = base.analyze_big_move
    if getattr(original, "_big_move_diagnostic_guard", False):
        return

    def diagnosed(exchange, row, market_regime):
        signal, reason = original(exchange, row, market_regime)
        symbol = str((row or {}).get("symbol") or "")
        if symbol:
            item = {
                "reason": str(reason),
                "notional_24h_usdt": round(base.safe_float((row or {}).get("notional_usdt")), 2),
                "last_price": base.safe_float((row or {}).get("last")),
                "checked_at": base.core.utc_text(),
            }
            if signal:
                item.update({
                    "candidate": True,
                    "direction": signal.get("direction"),
                    "score": signal.get("score"),
                    "setup_type": signal.get("setup_type"),
                    "risk_percent": signal.get("risk_percent"),
                    "main_target_r": signal.get("main_target_r"),
                    "potential_percent": signal.get("potential_percent"),
                })
            else:
                item["candidate"] = False
            _SYMBOL_DIAGNOSTICS[symbol] = item
        return signal, reason

    diagnosed._big_move_diagnostic_guard = True  # type: ignore[attr-defined]
    base.analyze_big_move = diagnosed


def persist_symbol_diagnostics() -> None:
    state = base.load_state()
    state["status"] = "ACTIVE"
    state["diagnostics_generated_at"] = base.core.utc_text()
    state["symbol_diagnostics"] = dict(sorted(_SYMBOL_DIAGNOSTICS.items()))
    base.core.atomic_save_json(base.STATE_FILE, state)


def self_test() -> None:
    apply_target_order_guard()
    apply_symbol_diagnostics()
    assert getattr(base.build_route_projection, "_big_move_order_guard", False)
    assert getattr(base.analyze_big_move, "_big_move_diagnostic_guard", False)
    print("Big Move target order + symbol diagnostics self-test BASARILI")


def main() -> None:
    apply_target_order_guard()
    apply_symbol_diagnostics()
    if "--self-test" in sys.argv:
        self_test()
        return
    _SYMBOL_DIAGNOSTICS.clear()
    base.run()
    persist_symbol_diagnostics()


if __name__ == "__main__":
    main()
