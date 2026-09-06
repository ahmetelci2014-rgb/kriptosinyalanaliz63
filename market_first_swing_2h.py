"""Zero-extra-call 2H swing preparation for the single Market First system.

The layer aggregates the 1H candles already fetched for deep-scan candidates.
It is observational only: it can send an early swing preparation, but it never
creates a trade or bypasses the normal Market First entry/risk guards.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import pandas as pd

import market_first_entry_plan as entry_plan
import market_first_strategy as strategy

VERSION = "MARKET_FIRST_SWING_2H_V1_2026_09_06"
STATE_FILE = "market_first_swing_2h_state.json"
LEDGER_FILE = "market_first_swing_2h_ledger.json"
SUMMARY_FILE = "market_first_swing_2h_summary.json"
MIN_QUOTE_VOLUME_24H = 750_000.0
MIN_SWING_SCORE = 74
MAX_ZONE_DISTANCE_PERCENT = 1.80
MAX_EXTENSION_2H_ATR = 1.55
MAX_EXTENSION_1H_ATR = 1.65
REPEAT_SECONDS = 6 * 60 * 60
ACTIVE_TRACK_SECONDS = 36 * 60 * 60
MAX_ACTIVE_PRIORITY = 16
MAX_LEDGER_EPISODES = 1200


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def aggregate_1h_to_2h(df1h: Any) -> Optional[pd.DataFrame]:
    if df1h is None or not hasattr(df1h, "copy"):
        return None
    needed = ["open", "high", "low", "close", "volume"]
    if not all(column in df1h.columns for column in needed):
        return None
    frame = df1h.copy()
    for column in needed:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=needed).reset_index(drop=True)
    if len(frame) < 90:
        return None

    timestamp_column = next((name for name in ("timestamp", "datetime", "time") if name in frame.columns), None)
    if timestamp_column is not None:
        raw = frame[timestamp_column]
        if pd.api.types.is_numeric_dtype(raw):
            clean = raw.dropna()
            last = _sf(clean.iloc[-1]) if len(clean) else 0.0
            unit = "ms" if last > 10_000_000_000 else "s"
            dt = pd.to_datetime(raw, unit=unit, utc=True, errors="coerce")
        else:
            dt = pd.to_datetime(raw, utc=True, errors="coerce")
        usable = frame.assign(_dt=dt).dropna(subset=["_dt"]).set_index("_dt")
        result = usable.resample("2h", label="right", closed="right").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        ).dropna().reset_index(drop=True)
        if len(result) >= 45:
            return result

    even_len = (len(frame) // 2) * 2
    work = frame.iloc[-even_len:].copy().reset_index(drop=True)
    work["_pair"] = work.index // 2
    result = work.groupby("_pair", sort=True).agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum")
    ).reset_index(drop=True)
    return result if len(result) >= 45 else None


def _two_hour_structure(df2h: Any, current_price: float) -> Optional[Dict[str, Any]]:
    if df2h is None or len(df2h) < 42:
        return None
    frame = df2h.copy()
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna().reset_index(drop=True)
    if len(frame) < 42:
        return None
    closed = frame.iloc[:-1].copy().reset_index(drop=True)
    if len(closed) < 40:
        return None
    closed["ema9"] = closed["close"].ewm(span=9, adjust=False).mean()
    closed["ema20"] = closed["close"].ewm(span=20, adjust=False).mean()
    closed["ema50"] = closed["close"].ewm(span=50, adjust=False).mean()
    prev_close = closed["close"].shift(1)
    tr = pd.concat([closed["high"]-closed["low"], (closed["high"]-prev_close).abs(), (closed["low"]-prev_close).abs()], axis=1).max(axis=1)
    closed["atr14"] = tr.rolling(14).mean()
    closed = closed.dropna().reset_index(drop=True)
    if len(closed) < 8:
        return None
    last, past = closed.iloc[-1], closed.iloc[-4]
    close, ema9, ema20, ema50 = _sf(last.get("close")), _sf(last.get("ema9")), _sf(last.get("ema20")), _sf(last.get("ema50"))
    ema20_past, atr = _sf(past.get("ema20")), _sf(last.get("atr14"))
    price = current_price if current_price > 0 else close
    if close > ema9 > ema20 and ema20 >= ema20_past:
        direction = "LONG"
    elif close < ema9 < ema20 and ema20 <= ema20_past:
        direction = "SHORT"
    elif close > ema20 > ema50 and ema20 >= ema20_past:
        direction = "LONG"
    elif close < ema20 < ema50 and ema20 <= ema20_past:
        direction = "SHORT"
    else:
        direction = "NEUTRAL"
    return {"direction": direction, "ema20": ema20, "ema50": ema50, "atr": atr, "extension_atr": round(abs(price-ema20)/atr, 4) if atr > 0 else 0.0}


def _score(direction: str, s2h: Mapping[str, Any], s1h: Mapping[str, Any], s15: Mapping[str, Any], s5: Mapping[str, Any], distance: float, context: strategy.MarketContext) -> int:
    score = 58
    d15, d5 = str(s15.get("direction") or "").upper(), str(s5.get("direction") or "").upper()
    score += 10 if d15 == direction else 6 if d15 == "NEUTRAL" else 1
    score += 5 if d5 == direction else 4 if d5 == "NEUTRAL" else 2
    v15, v5 = _sf(s15.get("volume_ratio")), _sf(s5.get("volume_ratio"))
    score += 5 if v15 >= 1.0 else 3 if v15 >= 0.65 else 0
    score += 3 if v5 >= 0.8 else 2 if v5 >= 0.5 else 0
    score += 5 if distance == 0 else 4 if distance <= 0.45 else 2 if distance <= 0.90 else 0
    ext2, ext1 = _sf(s2h.get("extension_atr")), _sf(s1h.get("extension_atr"))
    score += 4 if ext2 <= 0.8 else 2 if ext2 <= 1.2 else 0
    score += 3 if ext1 <= 0.9 else 0
    preferred = str(context.preferred_direction or "").upper()
    score += 4 if preferred == direction else 2 if preferred not in {"LONG", "SHORT"} else 0
    return int(max(0, min(100, round(score))))


def evaluate_swing_preparation(*, symbol: str, df5m: Any, df15m: Any, df1h: Any, current_price: float, quote_volume_24h: float, context: strategy.MarketContext) -> Tuple[Optional[Dict[str, Any]], str]:
    if current_price <= 0: return None, "SWING_NO_PRICE"
    if quote_volume_24h < MIN_QUOTE_VOLUME_24H: return None, "SWING_LOW_VOLUME"
    df2h = aggregate_1h_to_2h(df1h)
    if df2h is None: return None, "SWING_2H_DATA"
    s2h = _two_hour_structure(df2h, current_price)
    s1h, s15, s5 = strategy._structure(df1h, current_price), strategy._structure(df15m, current_price), strategy._structure(df5m, current_price)
    if not all(isinstance(item, Mapping) for item in (s2h, s1h, s15, s5)): return None, "SWING_STRUCTURE_DATA"
    d2h, d1h = str(s2h.get("direction") or "").upper(), str(s1h.get("direction") or "").upper()
    if d2h not in {"LONG", "SHORT"} or d2h != d1h: return None, "SWING_2H_1H_NOT_ALIGNED"
    direction = d2h
    _, market_allowed = strategy._market_component(direction, context)
    if not market_allowed: return None, "SWING_MARKET_OPPOSED"
    if _sf(s2h.get("extension_atr")) > MAX_EXTENSION_2H_ATR: return None, "SWING_2H_EXTENDED"
    if _sf(s1h.get("extension_atr")) > MAX_EXTENSION_1H_ATR: return None, "SWING_1H_EXTENDED"
    atr15 = _sf(s15.get("atr"))
    anchor = entry_plan._choose_anchor(direction, current_price, s15, s1h)
    zone = entry_plan._zone(direction, current_price, anchor, atr15)
    if not zone: return None, "SWING_ZONE_DATA"
    low, high = _sf(zone.get("low")), _sf(zone.get("high"))
    distance = entry_plan._distance_to_zone_percent(current_price, low, high)
    if distance > MAX_ZONE_DISTANCE_PERCENT: return None, "SWING_TOO_FAR"
    geometry_entry = current_price if low <= current_price <= high else _sf(zone.get("anchor"))
    risk, risk_reason = entry_plan._risk_geometry(direction, geometry_entry, s15, s1h)
    if risk is None: return None, f"SWING_{risk_reason}"
    score = _score(direction, s2h, s1h, s15, s5, distance, context)
    if score < MIN_SWING_SCORE: return None, "SWING_LOW_SCORE"
    plan: Dict[str, Any] = {
        "version": VERSION, "symbol": str(symbol), "direction": direction, "status": "SWING_PREP", "score": score,
        "current_price": round(current_price,10), "quote_volume_24h": round(_sf(quote_volume_24h),2), "zone_low": low, "zone_high": high,
        "ideal_entry": _sf(zone.get("anchor")), "zone_distance_percent": round(distance,4), "structure_2h": d2h, "structure_1h": d1h,
        "structure_15m": str(s15.get("direction") or ""), "structure_5m": str(s5.get("direction") or ""),
        "extension_atr_2h": round(_sf(s2h.get("extension_atr")),3), "extension_atr_1h": round(_sf(s1h.get("extension_atr")),3),
        "volume_ratio_15m": round(_sf(s15.get("volume_ratio")),3), "volume_ratio_5m": round(_sf(s5.get("volume_ratio")),3),
        "market_regime": context.regime, "market_label": strategy.market_label(context), "market_preferred_direction": context.preferred_direction,
        "market_breadth_5m": context.breadth_5m, "shadow_only": True,
    }
    plan.update(risk)
    return plan, "OK"


def format_preparation(plan: Mapping[str, Any]) -> str:
    direction = str(plan.get("direction") or ""); icon = "🟢" if direction == "LONG" else "🔴"
    return (f"🧭 2H SWING HAZIRLIĞI | {plan.get('symbol')}\n{icon} {direction}\n🌍 Piyasa: {plan.get('market_label','KARIŞIK')}\n"
            f"📍 Swing izleme bölgesi: {_sf(plan.get('zone_low')):.10g} - {_sf(plan.get('zone_high')):.10g}\n💵 Mevcut: {_sf(plan.get('current_price')):.10g}\n"
            f"🛑 Yapısal geçersizlik: {_sf(plan.get('sl')):.10g}\n🎯 Referans TP1: {_sf(plan.get('tp1')):.10g} | TP2: {_sf(plan.get('tp2')):.10g} | TP3: {_sf(plan.get('tp3')):.10g}\n"
            f"📊 2H/1H: {plan.get('structure_2h')}/{plan.get('structure_1h')} | 15M: {plan.get('structure_15m')} | 5M: {plan.get('structure_5m')}\n⭐ Swing skoru: {int(_sf(plan.get('score')))}\n"
            "⏳ Erken swing farkındalığıdır; gerçek işlem için mevcut 15M/5M + canlı akış teyidi beklenecek.")


def load_state(bot: Any) -> Dict[str, Any]:
    state = bot.load_json_file(STATE_FILE,{"plans":{}})
    if not isinstance(state,dict): state={"plans":{}}
    if not isinstance(state.get("plans"),dict): state["plans"]={}
    return state


def should_emit(state: Dict[str,Any], plan: Mapping[str,Any], now:int) -> bool:
    key=f"{plan.get('symbol')}:{plan.get('direction')}"; return now-int(((state.get("plans") or {}).get(key) or {}).get("last_alert_at") or 0)>=REPEAT_SECONDS


def mark_emitted(state: Dict[str,Any], plan: Mapping[str,Any], now:int) -> None:
    key=f"{plan.get('symbol')}:{plan.get('direction')}"; state.setdefault("plans",{})[key]={"last_alert_at":int(now),"updated_at":int(now),"direction":plan.get("direction"),"score":int(_sf(plan.get("score"))),"zone_low":_sf(plan.get("zone_low")),"zone_high":_sf(plan.get("zone_high")),"status":"SWING_PREP"}


def save_state(bot:Any,state:Dict[str,Any])->None: bot.save_json_file(STATE_FILE,state)


def load_ledger(bot:Any)->Dict[str,Any]:
    ledger=bot.load_json_file(LEDGER_FILE,{"version":VERSION,"episodes":{}})
    if not isinstance(ledger,dict): ledger={"version":VERSION,"episodes":{}}
    if not isinstance(ledger.get("episodes"),dict): ledger["episodes"]={}
    ledger["version"]=VERSION; return ledger


def _open_episode(ledger:Mapping[str,Any],symbol:str,direction:str)->Optional[Dict[str,Any]]:
    items=[e for e in (ledger.get("episodes") or {}).values() if isinstance(e,dict) and not e.get("resolved") and str(e.get("symbol"))==symbol and str(e.get("direction"))==direction]
    return max(items,key=lambda e:int(e.get("first_at") or 0)) if items else None


def register_plan(ledger:Dict[str,Any],plan:Mapping[str,Any],now:int,alerted:bool)->Dict[str,Any]:
    symbol,direction=str(plan.get("symbol") or ""),str(plan.get("direction") or ""); existing=_open_episode(ledger,symbol,direction)
    if existing is not None:
        existing.update({"updated_at":int(now),"latest_score":int(_sf(plan.get("score"))),"telegram_alert_sent":bool(existing.get("telegram_alert_sent") or alerted)}); return existing
    key=f"{symbol}:{direction}:{int(now)}"; e={"episode_id":key,"symbol":symbol,"direction":direction,"first_at":int(now),"updated_at":int(now),"alert_price":_sf(plan.get("current_price")),"telegram_alert_sent":bool(alerted),"score":int(_sf(plan.get("score"))),"zone_low":_sf(plan.get("zone_low")),"zone_high":_sf(plan.get("zone_high")),"sl":_sf(plan.get("sl")),"tp1":_sf(plan.get("tp1")),"tp2":_sf(plan.get("tp2")),"tp3":_sf(plan.get("tp3")),"structure_2h":plan.get("structure_2h"),"structure_1h":plan.get("structure_1h"),"structure_15m":plan.get("structure_15m"),"structure_5m":plan.get("structure_5m"),"best_favorable_percent":0.0,"worst_adverse_percent":0.0,"tp1_at":0,"tp2_at":0,"tp3_at":0,"sl_at":0,"first_decisive_event":None,"resolved":False,"outcome":None,"shadow_only":True}; ledger.setdefault("episodes",{})[key]=e; return e


def update_symbol_market(ledger:Dict[str,Any],symbol:str,current_price:float,df5m:Any,now:int)->int:
    if current_price<=0:return 0
    high,low=current_price,current_price
    try:
        if df5m is not None and len(df5m)>0:
            row=df5m.iloc[-1]; high=max(high,_sf(row.get("high"),current_price)); low=min(low,_sf(row.get("low"),current_price))
    except Exception: pass
    changed=0
    for e in (ledger.get("episodes") or {}).values():
        if not isinstance(e,dict) or e.get("resolved") or str(e.get("symbol"))!=symbol or now-int(e.get("first_at") or 0)>ACTIVE_TRACK_SECONDS:continue
        entry,direction=_sf(e.get("alert_price")),str(e.get("direction") or "")
        if entry<=0:continue
        if direction=="LONG":
            favorable,adverse=max(0.0,(high-entry)/entry*100),max(0.0,(entry-low)/entry*100); hits={"tp1":_sf(e.get("tp1"))>0 and high>=_sf(e.get("tp1")),"tp2":_sf(e.get("tp2"))>0 and high>=_sf(e.get("tp2")),"tp3":_sf(e.get("tp3"))>0 and high>=_sf(e.get("tp3")),"sl":_sf(e.get("sl"))>0 and low<=_sf(e.get("sl"))}
        else:
            favorable,adverse=max(0.0,(entry-low)/entry*100),max(0.0,(high-entry)/entry*100); hits={"tp1":_sf(e.get("tp1"))>0 and low<=_sf(e.get("tp1")),"tp2":_sf(e.get("tp2"))>0 and low<=_sf(e.get("tp2")),"tp3":_sf(e.get("tp3"))>0 and low<=_sf(e.get("tp3")),"sl":_sf(e.get("sl"))>0 and high>=_sf(e.get("sl"))}
        e["best_favorable_percent"]=round(max(_sf(e.get("best_favorable_percent")),favorable),4); e["worst_adverse_percent"]=round(max(_sf(e.get("worst_adverse_percent")),adverse),4)
        for name in ("tp1","tp2","tp3","sl"):
            if hits[name] and not int(e.get(f"{name}_at") or 0): e[f"{name}_at"]=int(now)
        if e.get("first_decisive_event") is None:
            if hits["tp1"] and hits["sl"]: e["first_decisive_event"]="AMBIGUOUS_SAME_BAR"
            elif hits["tp1"]: e["first_decisive_event"]="TP1_FIRST"
            elif hits["sl"]: e["first_decisive_event"]="SL_FIRST"
        e["updated_at"]=int(now); changed+=1
    return changed


def finalize_expired(ledger:Dict[str,Any],now:int)->int:
    count=0
    for e in (ledger.get("episodes") or {}).values():
        if not isinstance(e,dict) or e.get("resolved") or now-int(e.get("first_at") or 0)<ACTIVE_TRACK_SECONDS:continue
        e["outcome"]="TP3_REACHED" if int(e.get("tp3_at") or 0) else "TP2_REACHED" if int(e.get("tp2_at") or 0) else "TP1_REACHED" if int(e.get("tp1_at") or 0) else "SL_FIRST_NO_TP" if int(e.get("sl_at") or 0) else "TIMEOUT"; e["resolved"]=True; e["resolved_at"]=int(now); count+=1
    return count


def active_symbols(ledger:Mapping[str,Any],now:int)->list[str]:
    result=[]
    for e in (ledger.get("episodes") or {}).values():
        if not isinstance(e,Mapping) or e.get("resolved") or now-int(e.get("first_at") or 0)>ACTIVE_TRACK_SECONDS:continue
        symbol=str(e.get("symbol") or "")
        if symbol and symbol not in result:result.append(symbol)
        if len(result)>=MAX_ACTIVE_PRIORITY:break
    return result


def prioritize_active_symbols(selected:Sequence[str],rows:Sequence[Mapping[str,Any]],ledger:Mapping[str,Any],now:int,max_total:int)->list[str]:
    available={str(row.get("symbol") or "") for row in rows if isinstance(row,Mapping)}; merged=[]; seen=set()
    for symbol in active_symbols(ledger,now)+[str(item) for item in selected]:
        if not symbol or symbol not in available or symbol in seen:continue
        seen.add(symbol); merged.append(symbol)
        if len(merged)>=max_total:break
    return merged


def summary(ledger:Mapping[str,Any],now:int)->Dict[str,Any]:
    episodes=[e for e in (ledger.get("episodes") or {}).values() if isinstance(e,Mapping)]; resolved=[e for e in episodes if e.get("resolved")]
    return {"version":VERSION,"generated_at":int(now),"total":len(episodes),"open":len(episodes)-len(resolved),"resolved":len(resolved),"long":sum(1 for e in episodes if e.get("direction")=="LONG"),"short":sum(1 for e in episodes if e.get("direction")=="SHORT"),"telegram_alert_sent":sum(1 for e in episodes if e.get("telegram_alert_sent")),"tp1_first":sum(1 for e in episodes if e.get("first_decisive_event")=="TP1_FIRST"),"sl_first":sum(1 for e in episodes if e.get("first_decisive_event")=="SL_FIRST"),"tp3_reached":sum(1 for e in episodes if int(e.get("tp3_at") or 0)),"avg_best_favorable_percent_resolved":round(sum(_sf(e.get("best_favorable_percent")) for e in resolved)/len(resolved),4) if resolved else 0.0,"note":"2H swing preparation is hypothetical opportunity tracking, never realised P&L."}


def save_ledger(bot:Any,ledger:Dict[str,Any],now:int)->None:
    ledger["version"],ledger["updated_at"]=VERSION,int(now); episodes=ledger.get("episodes") or {}
    if len(episodes)>MAX_LEDGER_EPISODES:
        ordered=sorted(episodes.items(),key=lambda p:int((p[1] or {}).get("first_at") or 0),reverse=True); ledger["episodes"]=dict(ordered[:MAX_LEDGER_EPISODES])
    bot.save_json_file(LEDGER_FILE,ledger); bot.save_json_file(SUMMARY_FILE,summary(ledger,now))
