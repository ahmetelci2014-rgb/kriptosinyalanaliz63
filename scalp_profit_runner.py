"""Scalp Profit Mode V2.

TEPKI_SCALP is live only after enough cost-adjusted virtual/real evidence.
Until then it continues as silent shadow so the sample can grow. No orders.
"""
from __future__ import annotations
from typing import Any, Callable

import live_entry_safety as safety
import market_impulse_guard as impulse
import profitability_engine as profit
import opportunity_capture as capture
import scalp_radar as radar

REACTION_MIN_1M_REVERSAL_PERCENT=0.05


def _n(v:Any,d=0.0):
    try:return float(v)
    except:return d


def _attack_off(*args:Any,**kwargs:Any):
    return None,{"reason":"PROFIT_V2_ATAK_DISABLED","live":False}


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


def _clear_sender(original:Callable[...,Any]):
    def wrapped(message:Any,*args:Any,**kwargs:Any):
        text=str(message or "")
        if text.startswith("🚀 SCALP SİNYALİ"):
            text=("✅ İŞLEM GİRİŞİ — SCALP V2\n"
                  "Maliyet-sonrası setup kanıtı geçti.\n\n"+text)
        return original(text,*args,**kwargs)
    return wrapped


def _silent_sender(message:Any,*args:Any,**kwargs:Any):
    text=str(message or "")
    if "SCALP SİNYALİ" in text:
        print("PROFIT V2 SHADOW SCALP ENTRY suppressed")
    return True


def _mark_shadow_open():
    state=radar.load_state(); changed=False
    for signal in state.get("open_scalp_signals",{}).values():
        if isinstance(signal,dict) and not signal.get("profit_mode_v2_shadow"):
            signal["profit_mode_v2_shadow"]=True
            signal["profit_mode_v2_version"]=profit.VERSION
            changed=True
    if changed:radar.save_state(state)


def run()->None:
    profile=profit.scalp_profile()
    state=radar.load_state()
    shadow_open=any(isinstance(s,dict) and s.get("profit_mode_v2_shadow") for s in state.get("open_scalp_signals",{}).values())
    live=bool(profile.get("live_allowed")) and not shadow_open

    radar.SEND_EARLY_ALERTS_TO_TELEGRAM=False
    radar.SEND_PREWATCH_ALERTS_TO_TELEGRAM=False
    radar.MAX_NEW_SIGNALS_PER_RUN=1
    radar.MAX_OPEN_SCALP_SIGNALS=1
    radar.has_open_same_symbol=lambda state,symbol: False
    radar.evaluate_portfolio_risk=capture.make_opposite_direction_evaluator(radar.evaluate_portfolio_risk)
    radar.analyze_attack_side=_attack_off
    radar.analyze_reaction_side=_reaction_confirm(radar.analyze_reaction_side)

    if not live:
        radar.send_telegram=_silent_sender
        print("PROFIT V2 / SCALP SHADOW | cost-adjusted evidence:",profile)
    else:
        radar.send_telegram=safety.make_entry_safety_sender(radar.send_telegram)
        radar.send_telegram=_clear_sender(radar.send_telegram)
        print("PROFIT V2 / SCALP LIVE | cost-adjusted evidence:",profile)

    radar.main()
    if not live:_mark_shadow_open()


if __name__=="__main__":
    run()
