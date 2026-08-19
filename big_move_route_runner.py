"""Büyük Hareket canlı Telegram giriş noktası.

Mevcut Büyük Hareket kalite hesabını korur. Ek olarak:
- aynı coinde eski ters-yön sanal rota varken yeni güçlü yönü analiz etmeye devam eder,
- ters yön adayını eski rota yüzünden susturmaz,
- yeni yön Telegram'a başarıyla gönderilirse eski ters-yön sanal rotayı kapatır,
- her derin taranan coin için son eleme nedenini state'e yazar.

Gerçek emir açmaz; yalnız Telegram + sanal sonuç takibi yapar.
"""
from __future__ import annotations

import sys
from typing import Any, Dict, List

import big_move_route as base


_SYMBOL_DIAGNOSTICS: Dict[str, Dict[str, Any]] = {}


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


def apply_reversal_message_guard() -> None:
    original = base.signal_message
    if getattr(original, "_big_move_reversal_message_guard", False):
        return

    def wrapped(signal: Dict[str, Any]) -> str:
        message = original(signal)
        previous = str(signal.get("reversal_from_direction") or "").strip()
        if not previous:
            return message
        return (
            "🔄 YÖN DÖNÜŞÜ TESPİTİ\n"
            f"{signal.get('symbol')}: önceki {previous} rota yeni "
            f"{signal.get('direction')} fırsatını ENGELLEMEDİ.\n"
            "Yeni yön bağımsız kalite koşullarını geçti.\n\n"
            + message
        )

    wrapped._big_move_reversal_message_guard = True  # type: ignore[attr-defined]
    base.signal_message = wrapped


def persist_symbol_diagnostics() -> None:
    state = base.load_state()
    state["status"] = "ACTIVE"
    state["diagnostics_generated_at"] = base.core.utc_text()
    state["symbol_diagnostics"] = dict(sorted(_SYMBOL_DIAGNOSTICS.items()))
    base.core.atomic_save_json(base.STATE_FILE, state)


def same_direction_open_ids(state: Dict[str, Any], signal: Dict[str, Any]) -> List[str]:
    symbol = str(signal.get("symbol") or "")
    direction = str(signal.get("direction") or "")
    return [
        trade_id
        for trade_id, trade in state.get("open_routes", {}).items()
        if str(trade.get("symbol") or "") == symbol
        and str(trade.get("direction") or "") == direction
    ]


def opposite_open_ids(state: Dict[str, Any], signal: Dict[str, Any]) -> List[str]:
    symbol = str(signal.get("symbol") or "")
    direction = str(signal.get("direction") or "")
    return [
        trade_id
        for trade_id, trade in state.get("open_routes", {}).items()
        if str(trade.get("symbol") or "") == symbol
        and str(trade.get("direction") or "") in {"LONG", "SHORT"}
        and str(trade.get("direction") or "") != direction
    ]


def mark_reversal_context(state: Dict[str, Any], signal: Dict[str, Any]) -> None:
    opposite_ids = opposite_open_ids(state, signal)
    if not opposite_ids:
        return
    directions = sorted({
        str(state["open_routes"][trade_id].get("direction") or "")
        for trade_id in opposite_ids
    })
    signal["reversal_from_direction"] = "/".join(directions)
    signal["reversal_open_count"] = len(opposite_ids)


def close_opposite_virtual_routes(
    exchange: Any,
    state: Dict[str, Any],
    ledger: Dict[str, Any],
    signal: Dict[str, Any],
) -> int:
    closed = 0
    price = base.safe_float(signal.get("entry"))
    if price <= 0:
        return 0

    for trade_id in list(opposite_open_ids(state, signal)):
        trade = state.get("open_routes", {}).get(trade_id)
        if not isinstance(trade, dict):
            continue
        try:
            closed_trade = base.close_at_market(
                dict(trade),
                price,
                "OPPOSITE_ROUTE_REVERSAL",
            )
            closed_trade["reversed_by_trade_id"] = base.make_trade_id(signal)
            closed_trade["reversal_direction"] = signal.get("direction")
            closed_trade = base.core.finalize_closed_trade(exchange, closed_trade)
            ledger.setdefault("closed_routes", []).append(closed_trade)
            state.setdefault("open_routes", {}).pop(trade_id, None)
            closed += 1
        except Exception as exc:
            print("Ters yön sanal rota kapatma hatası:", trade_id, type(exc).__name__)
    return closed


def run_capture() -> None:
    import ccxt

    exchange = ccxt.okx({
        "enableRateLimit": True,
        "timeout": 20000,
        "options": {"defaultType": "swap"},
    })

    state = base.load_state()
    ledger = base.load_ledger()

    print("=== BÜYÜK HAREKET / FİYAT ROTASI - YÖN DÖNÜŞÜ AKTİF ===")
    print("Version:", base.VERSION)
    print("Mode:", base.MODE)

    base.manage_open_routes(exchange, state, ledger)
    all_rows, universe_meta = base.build_full_universe(exchange)
    market_regime = base.core.get_market_regime(exchange)

    reasons: Dict[str, int] = {}
    candidates: List[Dict[str, Any]] = []

    # Evren her zaman analiz edilir. Açık rota limiti yalnız yeni aynı-yön rota
    # eklemeyi sınırlar; ters-yön fırsatı eski rota nedeniyle körleşmez.
    for row in all_rows:
        if base.safe_float(row.get("notional_usdt")) < base.MIN_LIQUIDITY_NOTIONAL_USDT:
            reasons["LOW_LIQUIDITY"] = reasons.get("LOW_LIQUIDITY", 0) + 1
            continue

        signal, reason = base.analyze_big_move(exchange, row, market_regime)
        reasons[reason] = reasons.get(reason, 0) + 1
        if not signal:
            continue

        if same_direction_open_ids(state, signal):
            reasons["ALREADY_OPEN_SAME_DIRECTION"] = reasons.get("ALREADY_OPEN_SAME_DIRECTION", 0) + 1
            continue

        if not base.cooldown_ok(state, signal["symbol"], signal["direction"]):
            reasons["SIGNAL_COOLDOWN"] = reasons.get("SIGNAL_COOLDOWN", 0) + 1
            continue

        mark_reversal_context(state, signal)
        candidates.append(signal)

    candidates.sort(
        key=lambda item: (
            1 if item.get("reversal_from_direction") else 0,
            base.safe_float(item.get("score")),
            base.safe_float(item.get("main_target_r")),
            base.safe_float(item.get("potential_percent")),
            base.safe_float(item.get("notional_24h_usdt")),
        ),
        reverse=True,
    )

    selected: List[Dict[str, Any]] = []
    projected_open = len(state.get("open_routes", {}))

    for signal in candidates:
        if len(selected) >= base.MAX_NEW_SIGNALS_PER_RUN:
            break

        reversal_count = len(opposite_open_ids(state, signal))
        if reversal_count > 0:
            selected.append(signal)
            projected_open = max(0, projected_open - reversal_count) + 1
            continue

        if projected_open < base.MAX_OPEN_ROUTES:
            selected.append(signal)
            projected_open += 1

    opened = 0
    reversal_opened = 0

    for signal in selected:
        if not base.send_signal(signal):
            print("Telegram gönderilemedi; rota açılmadı:", signal.get("symbol"))
            continue

        reversed_count = close_opposite_virtual_routes(exchange, state, ledger, signal)
        trade_id = base.open_route(state, signal)
        opened += 1
        reversal_opened += int(reversed_count > 0)
        print(
            "TELEGRAM BIG MOVE:",
            trade_id,
            "score",
            signal["score"],
            "mainR",
            signal["main_target_r"],
            "potential%",
            signal["potential_percent"],
            "reversal_closed",
            reversed_count,
        )

    summary = base.rebuild_summary(ledger)
    state["last_run"] = base.core.utc_text()
    state["universe"] = {
        **universe_meta,
        "market_regime": market_regime,
    }
    state["run_stats"] = {
        "eligible_seen": len(all_rows),
        "deep_scan_liquid": universe_meta.get("liquid_for_deep_scan"),
        "candidate_count": len(candidates),
        "reversal_candidate_count": sum(1 for item in candidates if item.get("reversal_from_direction")),
        "telegram_opened": opened,
        "reversal_opened": reversal_opened,
        "open_routes": len(state.get("open_routes", {})),
        "reason_counts": dict(sorted(reasons.items())),
    }

    base.core.atomic_save_json(base.STATE_FILE, state)
    base.core.atomic_save_json(base.LEDGER_FILE, ledger)

    print("Eligible seen:", len(all_rows))
    print("Liquid deep scan:", universe_meta.get("liquid_for_deep_scan"))
    print("Candidates:", len(candidates))
    print("Reversal candidates:", state["run_stats"]["reversal_candidate_count"])
    print("Telegram opened:", opened)
    print("Open routes:", len(state.get("open_routes", {})))
    print("Closed routes:", summary.get("total_closed"))
    print("Net R:", summary.get("net_r_after_costs"))
    print("Gerçek emir: KAPALI")


def self_test() -> None:
    apply_target_order_guard()
    apply_symbol_diagnostics()
    apply_reversal_message_guard()
    assert getattr(base.build_route_projection, "_big_move_order_guard", False)
    assert getattr(base.analyze_big_move, "_big_move_diagnostic_guard", False)
    assert getattr(base.signal_message, "_big_move_reversal_message_guard", False)
    print("Big Move target + diagnostics + reversal self-test BASARILI")


def main() -> None:
    apply_target_order_guard()
    apply_symbol_diagnostics()
    apply_reversal_message_guard()
    if "--self-test" in sys.argv:
        self_test()
        return
    _SYMBOL_DIAGNOSTICS.clear()
    run_capture()
    persist_symbol_diagnostics()


if __name__ == "__main__":
    main()
