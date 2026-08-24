"""Global Premium entry guard driven by recorded direction health and Market Outlook."""
from __future__ import annotations

import time
from typing import Any, Callable

import premium_quality_layer as quality

VERSION = "PREMIUM_GLOBAL_QUALITY_GUARD_V1_2026_08_24"
TIGHT_MIN_SCORE = 94


def install(bot: Any) -> None:
    original: Callable[..., Any] = bot.is_entry_still_valid
    if getattr(original, "_premium_global_quality_wrapped", False):
        return

    def wrapped(signal: dict, current_price: Any):
        now = int(time.time())
        direction = str(signal.get("direction") or "").upper()
        source = str(signal.get("source") or "").upper()
        score = int(quality._sf(signal.get("score"), 0) or 0)
        health = quality.direction_health(direction, now)
        market = quality.market_outlook_context(direction, now)
        regime_mode = str(signal.get("regime_transition_mode") or "").upper()
        is_regime_reversal = bool(
            source == "REGIME_TRANSITION_ENTRY"
            and "REVERSAL" in regime_mode
            and score >= 98
        )

        signal["global_quality_guard_version"] = VERSION
        signal["direction_health"] = health
        signal["market_outlook_quality"] = market

        reason = None
        if health.get("mode") == "PAUSE" and not is_regime_reversal:
            reason = "Yön sağlığı: son işlemlerde stop kümesi, yeni aynı yön giriş geçici durduruldu"
        elif market.get("mode") == "BLOCK" and not is_regime_reversal:
            reason = "Market Outlook: 6H/24H güçlü şekilde ters yönde"
        elif (
            health.get("mode") == "TIGHT"
            or market.get("mode") == "TIGHT"
        ) and score < TIGHT_MIN_SCORE:
            reason = f"Kalite sıkı mod: skor {score} < {TIGHT_MIN_SCORE}"

        evidence = {"health": health, "market": market}
        if reason:
            quality._record("GLOBAL_ENTRY", signal, "REJECT", reason, evidence, now)
            print(signal.get("symbol"), "PREMIUM GLOBAL QUALITY RED:", reason)
            return False, reason

        quality._record("GLOBAL_ENTRY", signal, "ALLOW", "ALLOW", evidence, now)
        return original(signal, current_price)

    wrapped._premium_global_quality_wrapped = True  # type: ignore[attr-defined]
    bot.is_entry_still_valid = wrapped
