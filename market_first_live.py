"""Live entry point for the single Market First V5 system.

Market First remains the only strategy. This entry point installs safety/audit
hooks without creating a second signal engine:
- strict crypto-only OKX universe,
- final contract-level crypto purity gate for stock/ETF/commodity derivatives,
- fresh moderate-mover deep-scan priority,
- newly listed contract deep-scan priority,
- rolling full-universe deep-analysis coverage,
- corrected late/stale classification,
- profit-after-cost ML labelling,
- no-lookahead historical replay rows added only to the in-memory ML training pool,
- fresh BTC/ETH/SOL recheck before Telegram,
- final spread/depth liquidity check,
- shadow audit for signals withheld by the last safety guards.
"""
from __future__ import annotations

from typing import Any, Dict

import market_first_runner as runner
import market_first_audit_layer as audit
import market_first_crypto_purity as purity
import market_first_new_listings as new_listings
import market_first_full_coverage as full_coverage
import market_first_ml_training_pool as ml_training_pool
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
    original_select_deep_scan = runner._select_deep_scan
    original_analyze_candidate = runner.analyze_candidate
    original_send_trade = runner._send_trade
    original_train_quality_model = runner.train_quality_model
    guard_cache: Dict[str, Any] = {"at": 0, "moves": {}}

    def guarded_load_universe(exchange: Any):
        rows, universe = original_load_universe(exchange)
        rows, universe, strict_summary = audit.strict_crypto_universe(
            exchange,
            rows,
            universe,
        )
        print("MARKET FIRST STRICT CRYPTO:", strict_summary)

        # CCXT can classify OKX stock/ETF/commodity products as generic
        # contract/future rows instead of swap=True. Re-check every surviving
        # derivative using OKX instCategory/groupId and fail closed when crypto
        # evidence is missing. This prevents XAU/AAPL/DELL/etc. from polluting
        # market breadth, rotation and deep-scan selection.
        rows, universe, purity_summary = purity.filter_market_first_universe(
            exchange,
            rows,
            universe,
        )
        print("MARKET FIRST CRYPTO PURITY:", purity_summary)

        if runner._is_live_run():
            try:
                summary = update_shadow_results(universe, runner.bot.now_ts())
                print("MARKET GUARD SHADOW:", summary)
            except Exception as exc:
                print("Market guard shadow güncelleme hatası:", type(exc).__name__, exc)
        return rows, universe

    def audited_select_deep_scan(rows, sample_moves, state):
        selected = audit.select_deep_scan(
            rows,
            sample_moves,
            state,
            original_select_deep_scan,
        )
        selected, listing_summary = new_listings.prioritize_new_listings(
            rows,
            state,
            selected,
            max_total=audit.MAX_AUDITED_DEEP_SCAN,
            now=runner.bot.now_ts(),
        )
        selected, coverage_summary = full_coverage.expand_full_universe_coverage(
            rows,
            state,
            selected,
            max_total=full_coverage.MAX_DEEP_SCAN_PER_RUN,
            priority_slots=full_coverage.PRIORITY_SLOTS,
        )
        print(
            "MARKET FIRST EARLY CAPTURE | deep=",
            len(selected),
            "| fresh-band + full-universe rotation",
        )
        print("MARKET FIRST NEW LISTINGS:", listing_summary)
        print("MARKET FIRST FULL COVERAGE:", coverage_summary)
        return selected

    def audited_analyze_candidate(*args, **kwargs):
        decision, reason = original_analyze_candidate(*args, **kwargs)
        revised, revised_reason = audit.revise_late_decision(decision, reason)
        if revised is not None and revised.get("late_rescued"):
            print(
                "LATE RESCUE -> EARLY:",
                revised.get("symbol"),
                revised.get("direction"),
                "| 3m=", revised.get("move_3m_percent"),
                "| 5m=", revised.get("move_5m_percent"),
                "| extATR=", revised.get("extension_atr_5m"),
            )
        return revised, revised_reason

    def train_quality_with_history(live_store):
        training_store = ml_training_pool.combine_training_store(live_store)
        historical_added = int(training_store.get("historical_seed_rows_added") or 0)
        if historical_added:
            print("ML HISTORICAL TRAINING POOL:", historical_added, "etiketli replay örneği eklendi")
        return original_train_quality_model(training_store)

    def guarded_send_trade(exchange: Any, signal: Dict[str, Any], ml_store: Dict[str, Any]) -> bool:
        now = runner.bot.now_ts()

        # Reuse the same fresh-major snapshot for multiple signals selected in the
        # same run. This avoids unnecessary OKX calls without weakening the guard.
        if now - int(guard_cache.get("at") or 0) > 20:
            guard_cache["moves"] = fetch_fresh_major_moves(exchange)
            guard_cache["at"] = now

        major_guard = evaluate_pre_send_market(
            str(signal.get("direction") or ""),
            guard_cache.get("moves") or {},
        )
        signal["pre_send_market_guard"] = major_guard

        if major_guard.get("blocked"):
            print(
                "PRE-SEND MARKET GUARD:",
                signal.get("symbol"),
                signal.get("direction"),
                "engellendi |",
                major_guard.get("reason"),
                "| majors=",
                major_guard.get("major_moves"),
                "| weighted=",
                major_guard.get("weighted_move_percent"),
            )
            if runner._is_live_run():
                try:
                    audit_guard = dict(major_guard)
                    audit_guard["guard_type"] = "MAJOR_RECHECK"
                    register_shadow_rejection(signal, audit_guard, now)
                except Exception as exc:
                    print("Market guard shadow kayıt hatası:", type(exc).__name__, exc)
            return False

        liquidity = audit.evaluate_liquidity(exchange, str(signal.get("symbol") or ""))
        signal["pre_send_liquidity_guard"] = liquidity
        if liquidity.get("blocked"):
            print(
                "PRE-SEND LIQUIDITY GUARD:",
                signal.get("symbol"),
                signal.get("direction"),
                "engellendi |",
                liquidity.get("reason"),
                "| spread_bps=", liquidity.get("spread_bps"),
                "| min_depth=", liquidity.get("min_side_depth_quote"),
            )
            if runner._is_live_run():
                try:
                    audit_guard = dict(liquidity)
                    audit_guard["guard_type"] = "LIQUIDITY"
                    register_shadow_rejection(signal, audit_guard, now)
                except Exception as exc:
                    print("Liquidity shadow kayıt hatası:", type(exc).__name__, exc)
            return False

        return original_send_trade(exchange, signal, ml_store)

    runner._load_universe = guarded_load_universe
    runner._select_deep_scan = audited_select_deep_scan
    runner.analyze_candidate = audited_analyze_candidate
    runner.reconcile_samples = audit.reconcile_samples_net_r
    runner.train_quality_model = train_quality_with_history
    runner._send_trade = guarded_send_trade


def main() -> None:
    install_guards()
    runner.run()


if __name__ == "__main__":
    main()
