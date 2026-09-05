"""Live entry point for the single Market First V5 system.

Market First remains the only strategy. This entry point installs safety/audit
hooks without creating a second signal engine:
- strict crypto-only OKX universe,
- final contract-level crypto purity gate for stock/ETF/commodity derivatives,
- fresh moderate-mover deep-scan priority,
- newly listed contract deep-scan priority,
- rolling full-universe deep-analysis coverage,
- strongest first-observation EARLY moves can become immediate fast trades,
- active EARLY alerts forced back into every scan until resolved,
- EARLY -> trade follow-through remains as a fallback, not the primary fast path,
- corrected late/stale classification,
- profit-after-cost ML labelling,
- no-lookahead historical replay rows added only to the in-memory ML training pool,
- short-term breadth conflict removes stale normal-regime directional preference,
- combined live taker/CVD or taker/opposing-wall veto before Telegram,
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
import market_first_early_trade_bridge as early_trade_bridge
import market_first_fast_entry as fast_entry
import market_first_live_direction_guard as direction_guard
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
    original_decision_to_signal = runner.decision_to_signal
    original_send_trade = runner._send_trade
    original_train_quality_model = runner.train_quality_model
    original_load_ml_store = runner.load_ml_store
    original_extract_features = runner.extract_features
    original_format_trade_message = runner._format_trade_message

    guard_cache: Dict[str, Any] = {"at": 0, "moves": {}}
    followthrough_cache: Dict[str, Any] = {"alerts": {}}
    runtime_cache: Dict[str, Any] = {
        "exchange": None,
        "ml_store": None,
        "ml_bundle": None,
        "features": {},
        "fast_sent": 0,
    }

    def tracked_load_ml_store():
        store = original_load_ml_store()
        runtime_cache["ml_store"] = store
        return store

    def tracked_extract_features(decision, context):
        features = original_extract_features(decision, context)
        symbol = str((decision or {}).get("symbol") or "") if isinstance(decision, dict) else ""
        if symbol:
            runtime_cache.setdefault("features", {})[symbol] = features
        return features

    def guarded_load_universe(exchange: Any):
        runtime_cache["exchange"] = exchange
        rows, universe = original_load_universe(exchange)
        rows, universe, strict_summary = audit.strict_crypto_universe(
            exchange,
            rows,
            universe,
        )
        print("MARKET FIRST STRICT CRYPTO:", strict_summary)

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
        selected, active_alerts, follow_summary = early_trade_bridge.prioritize_active_alerts(
            selected,
            rows,
            state,
            max_total=full_coverage.MAX_DEEP_SCAN_PER_RUN,
            now=runner.bot.now_ts(),
        )
        followthrough_cache["alerts"] = active_alerts
        print(
            "MARKET FIRST EARLY CAPTURE | deep=",
            len(selected),
            "| fresh-band + full-universe rotation + active-alert follow-up",
        )
        print("MARKET FIRST NEW LISTINGS:", listing_summary)
        print("MARKET FIRST FULL COVERAGE:", coverage_summary)
        print("MARKET FIRST EARLY->TRADE:", follow_summary)
        return selected

    def audited_analyze_candidate(*args, **kwargs):
        def arg(call_args, call_kwargs, name: str, index: int, default=None):
            if name in call_kwargs:
                return call_kwargs.get(name)
            return call_args[index] if len(call_args) > index else default

        raw_context = arg(args, kwargs, "context", 7)
        context, breadth_guard = direction_guard.neutralize_breadth_conflict(raw_context)

        # Feed the underlying strategy the effective context so normal BULL/BEAR
        # preference cannot stay sticky when short-term breadth strongly disagrees.
        call_args = list(args)
        call_kwargs = dict(kwargs)
        if "context" in call_kwargs:
            call_kwargs["context"] = context
        elif len(call_args) > 7:
            call_args[7] = context
        else:
            call_kwargs["context"] = context

        decision, reason = original_analyze_candidate(*tuple(call_args), **call_kwargs)
        revised, revised_reason = audit.revise_late_decision(decision, reason)

        symbol = str(arg(args, kwargs, "symbol", 0, "") or "")
        current_price = float(arg(args, kwargs, "current_price", 5, 0.0) or 0.0)

        if revised is not None and breadth_guard.get("active"):
            revised["breadth_direction_guard"] = breadth_guard

        if revised is not None and revised.get("late_rescued"):
            print(
                "LATE RESCUE -> EARLY:",
                revised.get("symbol"),
                revised.get("direction"),
                "| 3m=", revised.get("move_3m_percent"),
                "| 5m=", revised.get("move_5m_percent"),
                "| extATR=", revised.get("extension_atr_5m"),
            )

        fast_promoted, fast_reason, fast_diag = fast_entry.promote_initial_early(
            revised,
            revised_reason,
            df5m=arg(args, kwargs, "df5m", 2),
            df15m=arg(args, kwargs, "df15m", 3),
            df1h=arg(args, kwargs, "df1h", 4),
            current_price=current_price,
            context=context,
        )
        if fast_diag.get("promoted"):
            print(
                "İLK ERKEN -> HIZLI İŞLEM:",
                symbol,
                fast_promoted.get("direction"),
                "| skor=", fast_promoted.get("score"),
                "| 3m=", fast_promoted.get("move_3m_percent"),
                "| 5m=", fast_promoted.get("move_5m_percent"),
                "| risk=", fast_promoted.get("risk_percent"),
                "| roomR=", fast_promoted.get("room_r"),
            )
        revised, revised_reason = fast_promoted, fast_reason

        alert = (followthrough_cache.get("alerts") or {}).get(symbol)
        if alert and not bool((revised or {}).get("fast_entry")):
            promoted, promoted_reason, follow_diag = early_trade_bridge.promote_active_alert(
                revised,
                revised_reason,
                alert,
                symbol=symbol,
                df1m=arg(args, kwargs, "df1m", 1),
                df5m=arg(args, kwargs, "df5m", 2),
                df15m=arg(args, kwargs, "df15m", 3),
                df1h=arg(args, kwargs, "df1h", 4),
                current_price=current_price,
                quote_volume_24h=float(arg(args, kwargs, "quote_volume_24h", 6, 0.0) or 0.0),
                context=context,
                now=runner.bot.now_ts(),
            )
            if follow_diag.get("promoted"):
                print(
                    "ERKEN -> İŞLEM TEYİDİ:",
                    symbol,
                    promoted.get("direction"),
                    "| ilk uyarıdan=", promoted.get("followthrough_favorable_percent"),
                    "| skor=", promoted.get("score"),
                    "| risk=", promoted.get("risk_percent"),
                    "| roomR=", promoted.get("room_r"),
                )
            revised, revised_reason = promoted, promoted_reason
        return revised, revised_reason

    def decision_to_signal_with_fast_path(decision):
        signal = original_decision_to_signal(decision)
        signal = fast_entry.decorate_signal(signal, decision)
        signal = early_trade_bridge.decorate_followthrough_signal(signal, decision)
        if signal is None:
            return signal

        # Runner's legacy derivative copy list intentionally predates CVD/book.
        # Carry these live fields to the final pre-send guard without changing
        # their ML/shadow use elsewhere.
        for key in direction_guard.LIVE_FLOW_SIGNAL_FIELDS:
            if key in decision:
                signal[key] = decision.get(key)

        if not bool(decision.get("fast_entry")):
            return signal

        symbol = str(signal.get("symbol") or "")
        runner._copy_derivatives_to_signal(signal, decision)
        signal["ml_features"] = (runtime_cache.get("features") or {}).get(symbol, {})
        signal["ml_mode"] = decision.get("ml_mode")
        signal["ml_quality_probability"] = decision.get("ml_quality_probability")

        # A fast entry is useful only if it is sent during the candidate scan,
        # not after the other 100+ deep scans have finished. Preserve the normal
        # ML, cooldown, portfolio, major-market, liquidity and open-slot guards.
        if not runner._is_live_run():
            return signal
        bundle = runtime_cache.get("ml_bundle")
        probability = decision.get("ml_quality_probability")
        if bundle is not None and runner.should_block_live(probability, bundle):
            return signal
        if runner.bot.has_open_same_symbol(symbol) or runner.bot.has_recent_stop(symbol):
            return signal
        risky_open, _, _ = runner.bot.count_open_signal_risk()
        if risky_open >= runner.bot.MAX_OPEN_SIGNALS:
            return signal

        exchange = runtime_cache.get("exchange")
        ml_store = runtime_cache.get("ml_store")
        if exchange is None or not isinstance(ml_store, dict):
            return signal

        sent = runner._send_trade(exchange, signal, ml_store)
        if not sent:
            return signal

        runtime_cache["fast_sent"] = int(runtime_cache.get("fast_sent") or 0) + 1
        # The ordinary end-of-run queue still runs. Reduce its remaining per-run
        # allowance so immediate entries do not weaken portfolio discipline.
        runner.bot.MAX_TRADE_SIGNALS_PER_RUN = max(
            0,
            int(runner.bot.MAX_TRADE_SIGNALS_PER_RUN) - 1,
        )
        runner.bot.RISK_MODE_MAX_TRADE_SIGNALS = max(
            0,
            int(runner.bot.RISK_MODE_MAX_TRADE_SIGNALS) - 1,
        )
        print("HIZLI İŞLEM AYNI TURDA GÖNDERİLDİ:", symbol, signal.get("direction"))
        return None

    def train_quality_with_history(live_store):
        training_store = ml_training_pool.combine_training_store(live_store)
        historical_added = int(training_store.get("historical_seed_rows_added") or 0)
        if historical_added:
            print("ML HISTORICAL TRAINING POOL:", historical_added, "etiketli replay örneği eklendi")
        bundle = original_train_quality_model(training_store)
        runtime_cache["ml_bundle"] = bundle
        return bundle

    def format_trade_message_with_fast_path(signal):
        text = original_format_trade_message(signal)
        if bool((signal or {}).get("fast_entry")):
            text = text.replace("✅ İŞLEM FIRSATI", "⚡ ERKEN İŞLEM FIRSATI", 1)
        return text

    def guarded_send_trade(exchange: Any, signal: Dict[str, Any], ml_store: Dict[str, Any]) -> bool:
        now = runner.bot.now_ts()

        flow_guard = direction_guard.evaluate_live_flow_veto(signal)
        signal["pre_send_live_flow_guard"] = flow_guard
        if flow_guard.get("blocked"):
            print(
                "PRE-SEND LIVE FLOW GUARD:",
                signal.get("symbol"),
                signal.get("direction"),
                "engellendi |",
                flow_guard.get("reason"),
                "| taker=", flow_guard.get("taker_alignment"),
                "| cvd=", flow_guard.get("cvd_ratio_alignment"),
                "| wall=", flow_guard.get("opposing_wall_ratio"),
            )
            if runner._is_live_run():
                try:
                    audit_guard = dict(flow_guard)
                    audit_guard["guard_type"] = "LIVE_FLOW"
                    register_shadow_rejection(signal, audit_guard, now)
                except Exception as exc:
                    print("Live flow shadow kayıt hatası:", type(exc).__name__, exc)
            return False

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

    runner.load_ml_store = tracked_load_ml_store
    runner.extract_features = tracked_extract_features
    runner._load_universe = guarded_load_universe
    runner._select_deep_scan = audited_select_deep_scan
    runner.analyze_candidate = audited_analyze_candidate
    runner.decision_to_signal = decision_to_signal_with_fast_path
    runner.reconcile_samples = audit.reconcile_samples_net_r
    runner.train_quality_model = train_quality_with_history
    runner._format_trade_message = format_trade_message_with_fast_path
    runner._send_trade = guarded_send_trade


def main() -> None:
    install_guards()
    runner.run()


if __name__ == "__main__":
    main()
