"""Scalp shadow data mode.

Premium MTF is the only live Telegram trade channel. Scalp keeps scanning,
creating virtual/shadow entries and updating its performance ledger, but every
Telegram message is suppressed. No orders.
"""
from __future__ import annotations
from typing import Any, Callable

import market_impulse_guard as impulse
import profitability_engine as profit
import opportunity_capture as capture
import scalp_radar as radar

REACTION_MIN_1M_REVERSAL_PERCENT=0.05


def _n(v:Any,d=0.0):
    try:return float(v)
    except:return d


def _attack_off(*args:Any,**kwargs:Any):
    return None,{"reason":"PREMIUM_ONLY_SCALP_ATTACK_SHADOW_DISABLED","live":False}


def _reaction_confirm(original:Callable[...,tuple[Any,Any]]):
    def wrapped(*args:Any,**kwargs:Any):
        signal,debug=original(*args,**kwargs)
        if not isinstance(signal,dict):return signal,debug
        direction=str(signal.get("direction") or "").upper()
        move1=_n(signal.get("move1"))
        if direction=="SHORT" and move1>-REACTION_MIN_1M_REVERSAL_PERCENT:return None,debug
        if direction=="LONG" and move1<REACTION_MIN_1M_REVERSAL_PERCENT:return None,debug
        opposing=impulse.recent_opposing_strong_impulse(str(signal.get("symbol") or ""),direction)
        if opposing:return None,debug
        return signal,debug
    return wrapped


def _silent_sender(message:Any,*args:Any,**kwargs:Any):
    text=str(message or "")
    if text:
        print("PREMIUM-ONLY: SCALP Telegram suppressed")
    return True


def _mark_shadow_open():
    state=radar.load_state(); changed=False
    for signal in state.get("open_scalp_signals",{}).values():
        if isinstance(signal,dict) and not signal.get("profit_mode_v2_shadow"):
            signal["profit_mode_v2_shadow"]=True
            signal["profit_mode_v2_version"]=profit.VERSION
            signal["live_channel"]="PREMIUM_ONLY"
            changed=True
    if changed:radar.save_state(state)


def run()->None:
    profile=profit.scalp_profile()
    profile_live_eligible=bool(profile.get("live_allowed"))

    radar.SEND_EARLY_ALERTS_TO_TELEGRAM=False
    radar.SEND_PREWATCH_ALERTS_TO_TELEGRAM=False

    # Shadow entries continue so the setup can build evidence. They never
    # reach Telegram because the sender is forcibly replaced below.
    radar.MAX_NEW_SIGNALS_PER_RUN=1
    radar.MAX_OPEN_SCALP_SIGNALS=1
    radar.has_open_same_symbol=lambda state,symbol: False
    radar.evaluate_portfolio_risk=capture.make_opposite_direction_evaluator(radar.evaluate_portfolio_risk)
    radar.analyze_attack_side=_attack_off
    radar.analyze_reaction_side=_reaction_confirm(radar.analyze_reaction_side)
    radar.send_telegram=_silent_sender

    print(
        "PREMIUM-ONLY / SCALP SHADOW DATA | "
        "profile_live_eligible=",
        profile_live_eligible,
        "| cost-adjusted evidence:",
        profile,
    )

    radar.main()
    _mark_shadow_open()


if __name__=="__main__":
    run()
