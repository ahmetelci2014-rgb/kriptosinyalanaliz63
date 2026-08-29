"""Opportunity-learning overlay for Market First historical ML replay.

V1/V2 only labelled candidates that already passed the live trade gate. That
created a circular problem: if the rule gate is too strict, the ML layer receives
no examples and can never measure which rejected opportunities were actually good.

V3 keeps live trading rules unchanged. Research-only differences:
- minute-level no-lookahead capture from V2 remains active;
- historical candidate floor is relaxed from 66 to 55 *only in this research
  process* so near-miss opportunities can be labelled;
- EARLY candidates and READY candidates rejected only by risk/room are given a
  counterfactual risk plan using the same swing/ATR stop geometry;
- the 1.60R room rule is recorded as a feature instead of used as a historical
  hard reject, allowing the Random Forest to learn whether NO_ROOM is too strict;
- future candles are still used only after all features and the hypothetical
  entry/SL/TP1 are frozen.

No Telegram message and no exchange order is ever created here.
"""
from __future__ import annotations

from collections import Counter
import math
from typing import Any, Dict, Mapping, Optional

import market_first_historical_ml_seed as seed
import market_first_historical_replay_v2 as capture_v2
import market_first_strategy as strategy

CAPTURE_VERSION = "MARKET_FIRST_HISTORICAL_OPPORTUNITY_V3_2026_08_29"
RESEARCH_MIN_SCORE = 55


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def research_risk_from_structures(
    direction: str,
    entry: float,
    s5: Mapping[str, Any],
    s15: Mapping[str, Any],
) -> tuple[Optional[Dict[str, float]], str]:
    """Build the live stop geometry but do not hard-reject low room in research."""
    direction = str(direction or "").upper()
    atr5 = _sf(s5.get("atr"))
    if entry <= 0 or atr5 <= 0:
        return None, "RISK_DATA"

    if direction == "LONG":
        raw_sl = _sf(s5.get("swing_low_12")) - atr5 * 0.10
        risk = entry - raw_sl
    else:
        raw_sl = _sf(s5.get("swing_high_12")) + atr5 * 0.10
        risk = raw_sl - entry

    if risk <= 0:
        return None, "RISK_GEOMETRY"

    risk_percent = risk / entry * 100.0
    if risk_percent < strategy.MIN_RISK_PERCENT:
        risk = entry * strategy.MIN_RISK_PERCENT / 100.0
        raw_sl = entry - risk if direction == "LONG" else entry + risk
        risk_percent = strategy.MIN_RISK_PERCENT

    if risk_percent > strategy.MAX_RISK_PERCENT:
        return None, "RISK_WIDE"

    if direction == "LONG":
        opposing = _sf(s15.get("range_high_72"))
        room = opposing - entry if opposing > entry else 0.0
    else:
        opposing = _sf(s15.get("range_low_72"))
        room = entry - opposing if 0 < opposing < entry else 0.0

    room_r = room / risk if risk > 0 and room > 0 else 99.0

    if direction == "LONG":
        tp1 = entry + risk * strategy.TP1_R
        tp2 = entry + risk * strategy.TP2_R
        tp3 = entry + risk * strategy.TP3_R
    else:
        tp1 = entry - risk * strategy.TP1_R
        tp2 = entry - risk * strategy.TP2_R
        tp3 = entry - risk * strategy.TP3_R

    if min(raw_sl, tp1, tp2, tp3) <= 0:
        return None, "RISK_GEOMETRY"

    return {
        "sl": round(raw_sl, 10),
        "tp1": round(tp1, 10),
        "tp2": round(tp2, 10),
        "tp3": round(tp3, 10),
        "risk_percent": round(risk_percent, 3),
        "room_r": round(room_r, 2),
    }, "OK"


def _research_plan(
    decision: Mapping[str, Any],
    f5: Any,
    f15: Any,
    current: float,
) -> tuple[Optional[Dict[str, float]], str]:
    if decision.get("trade_eligible"):
        required = ("sl", "tp1", "risk_percent")
        if all(_sf(decision.get(key)) > 0 for key in required):
            return {
                "sl": _sf(decision.get("sl")),
                "tp1": _sf(decision.get("tp1")),
                "tp2": _sf(decision.get("tp2")),
                "tp3": _sf(decision.get("tp3")),
                "risk_percent": _sf(decision.get("risk_percent")),
                "room_r": _sf(decision.get("room_r"), 99.0),
            }, "LIVE_PLAN"

    s5 = strategy._structure(f5, current)
    s15 = strategy._structure(f15, current)
    if s5 is None or s15 is None:
        return None, "RISK_STRUCTURE_DATA"
    return research_risk_from_structures(
        str(decision.get("direction") or ""),
        current,
        s5,
        s15,
    )


def process_symbol_v3(
    exchange: Any,
    row: Mapping[str, Any],
    major_cache: Mapping[str, Mapping[str, Any]],
    start_ms: int,
    end_eval_ms: int,
    end_data_ms: int,
    store: Dict[str, Any],
) -> Dict[str, Any]:
    """Label EARLY/near-miss candidates instead of waiting for live eligibility."""
    symbol = str(row["symbol"])
    market_symbol = str(row["market_symbol"])
    contract_size = _sf(row.get("contract_size"), 1.0)
    stats: Counter = Counter()

    warm5 = 16 * 60 * 60_000
    warm15 = 4 * 24 * 60 * 60_000
    warm1h = 10 * 24 * 60 * 60_000
    df5 = seed._fetch_range(
        exchange,
        market_symbol,
        "5m",
        start_ms - warm5,
        end_data_ms,
        max_bars=int((end_data_ms - (start_ms - warm5)) / seed.TF_MS["5m"]) + 40,
    )
    df15 = seed._fetch_range(
        exchange,
        market_symbol,
        "15m",
        start_ms - warm15,
        end_eval_ms,
        max_bars=int((end_eval_ms - (start_ms - warm15)) / seed.TF_MS["15m"]) + 40,
    )
    df1h = seed._fetch_range(
        exchange,
        market_symbol,
        "1h",
        start_ms - warm1h,
        end_eval_ms,
        max_bars=int((end_eval_ms - (start_ms - warm1h)) / seed.TF_MS["1h"]) + 40,
    )
    if min(len(df5), len(df15), len(df1h)) < 80:
        return {"symbol": symbol, "status": "DATA_MISSING", "stats": {}}

    eval_times = seed.select_eval_times(df5, start_ms, end_eval_ms)
    stats["eval_times"] = len(eval_times)
    samples = store.setdefault("samples", {})

    for eval_ms in eval_times:
        sample_id = f"HIST:{symbol}:{int(eval_ms // 1000)}"
        if sample_id in samples:
            stats["duplicate"] += 1
            continue

        quote_volume = seed._historical_quote_volume(df5, eval_ms, contract_size)
        if quote_volume < seed.MIN_HISTORICAL_QUOTE_VOLUME:
            stats["LOW_HIST_VOLUME"] += 1
            continue

        context, major_moves = seed._context_at(major_cache, eval_ms)
        if context is None:
            stats["MARKET_DATA"] += 1
            continue

        df1m, f5, f15, f1h, current = seed._candidate_frames(
            exchange,
            market_symbol,
            df5,
            df15,
            df1h,
            eval_ms,
        )
        if df1m is None or f5 is None or f15 is None or f1h is None:
            stats["CANDIDATE_DATA"] += 1
            continue

        decision, reason = strategy.analyze_candidate(
            symbol=symbol,
            df1m=df1m,
            df5m=f5,
            df15m=f15,
            df1h=f1h,
            current_price=current,
            quote_volume_24h=quote_volume,
            context=context,
        )
        decision, reason = seed.audit.revise_late_decision(decision, reason)
        if decision is None:
            stats[str(reason or "REJECTED")] += 1
            continue

        stage = str(decision.get("stage") or "")
        stats[f"stage_{stage}"] += 1
        if stage == "LATE":
            stats["RESEARCH_LATE_SKIP"] += 1
            continue

        original_trade_eligible = bool(decision.get("trade_eligible"))
        original_risk_reject = str(decision.get("risk_reject_reason") or "")
        plan, plan_reason = _research_plan(decision, f5, f15, current)
        if plan is None:
            stats[f"RESEARCH_{plan_reason}"] += 1
            continue

        research_decision = dict(decision)
        research_decision.update(plan)
        research_decision["research_original_trade_eligible"] = original_trade_eligible
        research_decision["research_original_risk_reject_reason"] = original_risk_reject
        research_decision["research_counterfactual"] = not original_trade_eligible
        research_decision["research_min_score"] = RESEARCH_MIN_SCORE

        guard = seed.evaluate_pre_send_market(
            str(research_decision.get("direction") or ""),
            major_moves,
        )
        if guard.get("blocked"):
            stats[f"GUARD_{guard.get('reason')}"] += 1
            continue

        features = seed.extract_features(research_decision, context)
        future = seed._future_5m(df5, eval_ms)
        outcome = seed.resolve_historical_outcome(
            str(research_decision.get("direction") or ""),
            _sf(research_decision.get("current_price")),
            _sf(research_decision.get("sl")),
            _sf(research_decision.get("tp1")),
            _sf(research_decision.get("risk_percent")),
            future,
        )
        if outcome.get("label") not in (0, 1):
            stats[str(outcome.get("result") or "OUTCOME_UNRESOLVED")] += 1
            continue

        sample = seed._historical_sample(
            symbol,
            eval_ms,
            research_decision,
            features,
            outcome,
        )
        sample["research_capture_version"] = CAPTURE_VERSION
        sample["research_candidate_stage"] = stage
        sample["research_candidate_score"] = int(research_decision.get("score") or 0)
        sample["research_original_trade_eligible"] = original_trade_eligible
        sample["research_original_risk_reject_reason"] = original_risk_reject or None
        sample["research_counterfactual"] = not original_trade_eligible
        sample["research_room_r"] = _sf(research_decision.get("room_r"), 99.0)
        sample["research_min_score"] = RESEARCH_MIN_SCORE
        samples[sample_id] = sample

        stats["seeded"] += 1
        stats["seeded_counterfactual" if not original_trade_eligible else "seeded_live_gate"] += 1
        stats["positive" if int(outcome["label"]) == 1 else "negative"] += 1
        if original_risk_reject:
            stats[f"seeded_from_{original_risk_reject}"] += 1

    return {"symbol": symbol, "status": "OK", "stats": dict(stats)}


def _reset_cursor_once_for_v3() -> None:
    state = seed._load_json(
        seed.STATE_FILE,
        {"version": seed.VERSION, "cursor": 0, "runs": 0, "processed_symbols": []},
    )
    if str(state.get("opportunity_capture_version") or "") == CAPTURE_VERSION:
        return
    state["cursor"] = 0
    state["opportunity_capture_version"] = CAPTURE_VERSION
    state["opportunity_capture_reason"] = (
        "Learn from EARLY and near-miss candidates because trade-only replay produced zero labels"
    )
    seed._atomic_json(seed.STATE_FILE, state)


def run():
    _reset_cursor_once_for_v3()

    # Research-only relaxation. This Python process never runs the live bot.
    old_min_alert = strategy.MIN_ALERT_SCORE
    strategy.MIN_ALERT_SCORE = RESEARCH_MIN_SCORE
    try:
        seed.select_eval_times = capture_v2.select_eval_times_v2
        seed._market_symbol_map = capture_v2.market_symbol_map_v2
        seed._process_symbol = process_symbol_v3
        return seed.run()
    finally:
        strategy.MIN_ALERT_SCORE = old_min_alert


if __name__ == "__main__":
    run()
