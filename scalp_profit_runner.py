"""Scalp Profit Mode V1.

Live Telegram receives only confirmed TEPKI_SCALP entries and their outcomes.
PREWATCH/EARLY remain silent and ATAK is removed from the live path. A strong
opposing all-market impulse blocks countertrend reactions. No exchange orders.
"""
from __future__ import annotations

from typing import Any, Callable

import live_entry_safety as safety
import market_impulse_guard as impulse
import opportunity_capture as capture
import scalp_radar as radar

REACTION_MIN_1M_REVERSAL_PERCENT = 0.05
MAX_OPEN_SCALP_PROFIT_MODE = 1


def _safe_number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _attack_live_disabled(*args: Any, **kwargs: Any):
    return None, {
        "reason": "PROFIT_MODE_V1_ATAK_LIVE_DISABLED",
        "live": False,
    }


def _make_reaction_confirmation_guard(
    original: Callable[..., tuple[Any, Any]],
) -> Callable[..., tuple[Any, Any]]:
    def wrapped(*args: Any, **kwargs: Any) -> tuple[Any, Any]:
        signal, debug = original(*args, **kwargs)
        if not isinstance(signal, dict):
            return signal, debug

        direction = str(signal.get("direction") or "").upper()
        move1 = _safe_number(signal.get("move1"))

        if direction == "SHORT" and move1 > -REACTION_MIN_1M_REVERSAL_PERCENT:
            print(
                "PROFIT MODE TEPKI: SHORT engellendi | "
                f"1M %{move1:+.3f}, kırmızı dönüş teyidi yok"
            )
            return None, debug

        if direction == "LONG" and move1 < REACTION_MIN_1M_REVERSAL_PERCENT:
            print(
                "PROFIT MODE TEPKI: LONG engellendi | "
                f"1M %{move1:+.3f}, yeşil dönüş teyidi yok"
            )
            return None, debug

        return signal, debug

    return wrapped


def _make_impulse_reaction_guard(
    original: Callable[..., tuple[Any, Any]],
) -> Callable[..., tuple[Any, Any]]:
    def wrapped(*args: Any, **kwargs: Any) -> tuple[Any, Any]:
        signal, debug = original(*args, **kwargs)
        if not isinstance(signal, dict):
            return signal, debug

        symbol = str(signal.get("symbol") or "")
        direction = str(signal.get("direction") or "").upper()
        opposing = impulse.recent_opposing_strong_impulse(symbol, direction)
        if opposing:
            print(
                "PROFIT MODE TEPKI:",
                symbol,
                direction,
                "engellendi | ters canlı impuls",
                opposing.get("direction"),
            )
            return None, debug
        return signal, debug

    return wrapped


def _make_clear_signal_sender(original: Callable[..., Any]) -> Callable[..., Any]:
    def wrapped(message: Any, *args: Any, **kwargs: Any) -> Any:
        text = str(message or "")
        if text.startswith("🚀 SCALP SİNYALİ"):
            text = (
                "✅ İŞLEM GİRİŞİ — SCALP\n"
                "Giriş + TP + SL hazır. Erken izleme mesajı değildir.\n\n"
                + text
            )
        return original(text, *args, **kwargs)

    return wrapped


def run() -> None:
    # Early layers may still be measured by the core ledger, but never notify.
    radar.SEND_EARLY_ALERTS_TO_TELEGRAM = False
    radar.SEND_PREWATCH_ALERTS_TO_TELEGRAM = False
    radar.MAX_NEW_SIGNALS_PER_RUN = 1
    radar.MAX_OPEN_SCALP_SIGNALS = MAX_OPEN_SCALP_PROFIT_MODE

    # A previous opposite-direction idea must not make the symbol invisible.
    # Same-direction duplicate and portfolio exposure rules still apply later.
    radar.has_open_same_symbol = lambda state, symbol: False
    radar.evaluate_portfolio_risk = capture.make_opposite_direction_evaluator(
        radar.evaluate_portfolio_risk
    )

    # Historical live evidence for ATAK is too weak for the profit-only path.
    radar.analyze_attack_side = _attack_live_disabled

    # TEPKI survives only with actual 1M reversal and no strong opposing impulse.
    radar.analyze_reaction_side = _make_reaction_confirmation_guard(
        radar.analyze_reaction_side
    )
    radar.analyze_reaction_side = _make_impulse_reaction_guard(
        radar.analyze_reaction_side
    )

    radar.send_telegram = safety.make_entry_safety_sender(radar.send_telegram)
    radar.send_telegram = _make_clear_signal_sender(radar.send_telegram)

    print(
        "PROFIT MODE V1 / SCALP | Telegram: sadece TEPKI_SCALP gerçek giriş | "
        "PREWATCH/EARLY sessiz | ATAK canlı KAPALI | max açık 1"
    )
    radar.main()


if __name__ == "__main__":
    run()
