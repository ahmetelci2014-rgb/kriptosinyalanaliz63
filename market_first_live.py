"""Live entry point for the single Market First V5 system.

It keeps market_first_runner as the strategy runner and installs only two safety
hooks:
- update the shadow audit from the already-fetched universe snapshot,
- recheck fresh BTC/ETH/SOL 5m momentum immediately before Telegram.
"""
from __future__ import annotations

from typing import Any, Dict

import market_first_runner as runner
from market_first_pre_send_guard import (
    evaluate_pre_send_market,
    fetch_fresh_major_moves,
    register_shadow_rejection,
    update_shadow_results,
)

_INSTALLED = False


def install_guards() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_load_universe = runner._load_universe
    original_send_trade = runner._send_trade
    guard_cache: Dict[str, Any] = {"at": 0, "moves": {}}

    def guarded_load_universe(exchange: Any):
        rows, universe = original_load_universe(exchange)
        if runner._is_live_run():
            try:
                summary = update_shadow_results(universe, runner.bot.now_ts())
                print("MARKET GUARD SHADOW:", summary)
            except Exception as exc:
                print("Market guard shadow güncelleme hatası:", type(exc).__name__, exc)
        return rows, universe

    def guarded_send_trade(exchange: Any, signal: Dict[str, Any], ml_store: Dict[str, Any]) -> bool:
        now = runner.bot.now_ts()
        # Reuse the same fresh-major snapshot for multiple signals selected in the
        # same run. This avoids unnecessary OKX calls without weakening the guard.
        if now - int(guard_cache.get("at") or 0) > 20:
            guard_cache["moves"] = fetch_fresh_major_moves(exchange)
            guard_cache["at"] = now

        guard = evaluate_pre_send_market(
            str(signal.get("direction") or ""),
            guard_cache.get("moves") or {},
        )
        signal["pre_send_market_guard"] = guard

        if guard.get("blocked"):
            print(
                "PRE-SEND MARKET GUARD:",
                signal.get("symbol"),
                signal.get("direction"),
                "engellendi |",
                guard.get("reason"),
                "| majors=",
                guard.get("major_moves"),
                "| weighted=",
                guard.get("weighted_move_percent"),
            )
            if runner._is_live_run():
                try:
                    register_shadow_rejection(signal, guard, now)
                except Exception as exc:
                    print("Market guard shadow kayıt hatası:", type(exc).__name__, exc)
            return False

        return original_send_trade(exchange, signal, ml_store)

    runner._load_universe = guarded_load_universe
    runner._send_trade = guarded_send_trade


def main() -> None:
    install_guards()
    runner.run()


if __name__ == "__main__":
    main()
