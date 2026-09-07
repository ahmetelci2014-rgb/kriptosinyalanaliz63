#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Autonomous Trade & Reverse Shadow V1.

Public OKX data only. No API keys, no Telegram, no real orders.
Uses strategy.analyze_mtf_trade for autonomous virtual entries and measures
whether profit-time reverse decisions beat a no-reverse benchmark after costs.
"""
from __future__ import annotations

import json, math, os, tempfile, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

VERSION = "AUTONOMOUS_REVERSE_SHADOW_V1_2026_09_08"
MODE = "SHADOW_ONLY_NO_ORDERS_NO_TELEGRAM"
STATE_FILE = Path("autonomous_reverse_shadow_state.json")
LEDGER_FILE = Path("autonomous_reverse_shadow_ledger.json")
TR_TZ = timezone(timedelta(hours=3))

MAX_UNIVERSE_COINS = int(os.getenv("AR_MAX_UNIVERSE_COINS", "120"))
DEEP_SCAN_PER_RUN = int(os.getenv("AR_DEEP_SCAN_PER_RUN", "20"))
MAX_OPEN_POSITIONS = int(os.getenv("AR_MAX_OPEN_POSITIONS", "6"))
MAX_NEW_POSITIONS_PER_RUN = int(os.getenv("AR_MAX_NEW_POSITIONS_PER_RUN", "2"))
MIN_ENTRY_SCORE = int(os.getenv("AR_MIN_ENTRY_SCORE", "80"))
MIN_24H_NOTIONAL = float(os.getenv("AR_MIN_24H_NOTIONAL", "200000"))
MAX_POSITION_HOURS = float(os.getenv("AR_MAX_POSITION_HOURS", "18"))
ENTRY_COOLDOWN_HOURS = float(os.getenv("AR_ENTRY_COOLDOWN_HOURS", "4"))
MIN_REVERSE_PROFIT_R = float(os.getenv("AR_MIN_REVERSE_PROFIT_R", "0.55"))
REVERSE_SCORE_THRESHOLD = int(os.getenv("AR_REVERSE_SCORE_THRESHOLD", "90"))
MAX_REVERSES_PER_CHAIN = int(os.getenv("AR_MAX_REVERSES_PER_CHAIN", "1"))
TAKER_FEE_PCT_PER_SIDE = float(os.getenv("AR_TAKER_FEE_PCT_PER_SIDE", "0.05"))
SLIPPAGE_PCT_PER_SIDE = float(os.getenv("AR_SLIPPAGE_PCT_PER_SIDE", "0.02"))
DAILY_RISK_STOP_R = float(os.getenv("AR_DAILY_RISK_STOP_R", "-3.0"))
ANALYSIS_LIMIT = 240
STABLE_BASES = {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDP", "USD"}


def now_ts() -> int: return int(time.time())


def tr_text(ts: Optional[int] = None) -> str:
    return datetime.fromtimestamp(int(ts if ts is not None else now_ts()), tz=TR_TZ).strftime("%Y-%m-%d %H:%M:%S")


def day_key(ts: Optional[int] = None) -> str:
    return datetime.fromtimestamp(int(ts if ts is not None else now_ts()), tz=TR_TZ).strftime("%Y-%m-%d")


def safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value in (None, "", "-"): return default
        value = float(value)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def load_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists(): return dict(default)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else dict(default)
    except Exception as exc:
        print(path, "okuma hatasi:", exc)
        return dict(default)


def save_json_atomic(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as f:
            tmp = f.name
            json.dump(data, f, ensure_ascii=False, indent=2); f.write("\n"); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path); tmp = None
    finally:
        if tmp and os.path.exists(tmp): os.remove(tmp)


def bot_symbol(okx: str) -> str: return f"{str(okx).split('/')[0].upper()}USDT"


def okx_symbol(symbol: str) -> str:
    base = str(symbol).upper()
    if base.endswith("USDT"): base = base[:-4]
    return f"{base}/USDT:USDT"


def get_exchange():
    import ccxt
    return ccxt.okx({"enableRateLimit": True, "options": {"defaultType": "swap"}})


def fetch_df(exchange, symbol: str, timeframe: str, limit: int = ANALYSIS_LIMIT):
    import pandas as pd
    try:
        rows = exchange.fetch_ohlcv(okx_symbol(symbol), timeframe=timeframe, limit=int(limit))
        if not rows or len(rows) < 40: return None
        return pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
    except Exception as exc:
        print(symbol, timeframe, "veri hatasi:", exc); return None


def ticker_notional(ticker: Dict[str, Any]) -> float:
    ticker = ticker if isinstance(ticker, dict) else {}
    info = ticker.get("info") if isinstance(ticker.get("info"), dict) else {}
    last = safe_float(ticker.get("last"), safe_float(info.get("last"), None))
    quote = safe_float(ticker.get("quoteVolume"), None)
    if quote and quote > 0: return quote
    base = safe_float(info.get("volCcy24h"), safe_float(ticker.get("baseVolume"), None))
    return base * last if base and last and last > 0 else 0.0


def build_universe(exchange) -> List[Dict[str, Any]]:
    markets, tickers = exchange.load_markets(), exchange.fetch_tickers()
    rows, seen = [], set()
    for m in markets.values():
        if not isinstance(m, dict) or not m.get("active", True) or not m.get("swap", False): continue
        if str(m.get("quote") or "").upper() != "USDT" or str(m.get("settle") or "").upper() != "USDT": continue
        raw, base = str(m.get("symbol") or ""), str(m.get("base") or "").upper()
        if "/USDT:USDT" not in raw or not base or base in STABLE_BASES: continue
        symbol = bot_symbol(raw)
        if symbol in seen: continue
        seen.add(symbol)
        t = tickers.get(raw, {}); notional = ticker_notional(t)
        last = safe_float(t.get("last"), None) if isinstance(t, dict) else None
        if notional >= MIN_24H_NOTIONAL and last and last > 0:
            rows.append({"symbol": symbol, "notional_24h": round(notional, 2), "last": last})
    rows.sort(key=lambda x: x["notional_24h"], reverse=True)
    return rows[:MAX_UNIVERSE_COINS]


def empty_state() -> Dict[str, Any]:
    return {"version": VERSION, "mode": MODE, "rotation_cursor": 0, "last_run": 0}


def empty_ledger() -> Dict[str, Any]:
    return {"version": VERSION, "mode": MODE, "positions": {}, "benchmarks": {}, "chains": {}, "summary": {}}


def ensure_ledger(data: Dict[str, Any]) -> Dict[str, Any]:
    data = data if isinstance(data, dict) else {}
    for k, v in empty_ledger().items(): data.setdefault(k, v)
    for k in ("positions", "benchmarks", "chains", "summary"):
        if not isinstance(data.get(k), dict): data[k] = {}
    return data


def open_positions(ledger): return [p for p in ledger["positions"].values() if isinstance(p, dict) and p.get("status") == "OPEN"]


def symbol_is_open(ledger, symbol): return any(p.get("symbol") == symbol for p in open_positions(ledger))


def recent_symbol_close(ledger, symbol, ts):
    cd = ENTRY_COOLDOWN_HOURS * 3600
    return any(
        isinstance(p, dict) and p.get("symbol") == symbol and int(p.get("closed_at") or 0) and ts - int(p["closed_at"]) < cd
        for p in ledger["positions"].values()
    )


def r_multiple(direction, entry, exit_price, risk_abs):
    if entry <= 0 or risk_abs <= 0: return 0.0
    return (exit_price - entry) / risk_abs if direction == "LONG" else (entry - exit_price) / risk_abs


def risk_percent(entry, risk_abs): return risk_abs / entry * 100 if entry > 0 else 0.0


def estimate_round_trip_cost_r(entry, risk_abs):
    rp = risk_percent(entry, risk_abs)
    return (2 * (TAKER_FEE_PCT_PER_SIDE + SLIPPAGE_PCT_PER_SIDE) / rp) if rp > 0 else 0.0


def size_factor_for_score(score):
    return 0.75 if score >= 99 else 0.60 if score >= 96 else 0.40 if score >= 93 else 0.25


def daily_closed_net_r(ledger, ts):
    today = day_key(ts)
    return round(sum(float(p.get("weighted_net_r") or 0) for p in ledger["positions"].values() if isinstance(p, dict) and p.get("closed_day") == today), 4)


def select_rotation(universe: Sequence[Dict[str, Any]], cursor: int):
    rows = list(universe)
    if not rows: return [], 0
    count, start = min(DEEP_SCAN_PER_RUN, len(rows)), cursor % len(rows)
    return [rows[(start+i) % len(rows)] for i in range(count)], (start+count) % len(rows)


def latest_price(exchange, symbol):
    try: return safe_float(exchange.fetch_ticker(okx_symbol(symbol)).get("last"), None)
    except Exception: return None


def indicator_frames(df5, df15, df1h):
    from strategy import add_indicators
    return add_indicators(df5), add_indicators(df15), add_indicators(df1h)


def calculate_reverse_score(current_direction: str, df5, df15, df1h) -> Dict[str, Any]:
    f5, f15, f1 = indicator_frames(df5, df15, df1h)
    if f5 is None or f15 is None or f1 is None: return {"score": 0, "hard_gate": False, "reasons": ["VERI_YETERSIZ"]}
    a5, p5, a15, a1 = f5.iloc[-2], f5.iloc[-3], f15.iloc[-2], f1.iloc[-2]
    d, reasons, score = current_direction.upper(), [], 0
    if d == "LONG":
        o5, o15 = float(a5.close) < float(a5.ema20), float(a15.close) < float(a15.ema20)
        checks = [(o5,15,"5M_EMA20_ALTINDA"),(a5.ema20_slope<0,10,"5M_EGIM_ASAGI"),(a5.macd_hist<0,10,"5M_MACD_NEG"),
                  (a5.macd_hist<p5.macd_hist,10,"5M_MACD_ZAYIF"),(a5.rsi<45,10,"5M_RSI_ZAYIF"),(o15,15,"15M_EMA20_ALTINDA"),
                  (a15.macd_hist<0,10,"15M_MACD_NEG"),(a15.rsi<47,10,"15M_RSI_ZAYIF"),(a15.volume_ratio>=1.2,5,"15M_HACIM"),
                  (a5.close<a5.open,5,"5M_KIRMIZI"),(a1.close<a1.ema20,10,"1H_EMA20_ALTINDA")]
        reverse_to = "SHORT"
    else:
        o5, o15 = float(a5.close) > float(a5.ema20), float(a15.close) > float(a15.ema20)
        checks = [(o5,15,"5M_EMA20_USTUNDE"),(a5.ema20_slope>0,10,"5M_EGIM_YUKARI"),(a5.macd_hist>0,10,"5M_MACD_POZ"),
                  (a5.macd_hist>p5.macd_hist,10,"5M_MACD_GUCLU"),(a5.rsi>55,10,"5M_RSI_GUCLU"),(o15,15,"15M_EMA20_USTUNDE"),
                  (a15.macd_hist>0,10,"15M_MACD_POZ"),(a15.rsi>53,10,"15M_RSI_GUCLU"),(a15.volume_ratio>=1.2,5,"15M_HACIM"),
                  (a5.close>a5.open,5,"5M_YESIL"),(a1.close>a1.ema20,10,"1H_EMA20_USTUNDE")]
        reverse_to = "LONG"
    for passed, pts, reason in checks:
        if bool(passed): score += pts; reasons.append(reason)
    return {"score": min(100, int(score)), "hard_gate": bool(o5 and o15), "reverse_to": reverse_to, "reasons": reasons}


def market_regime(exchange):
    from strategy import add_indicators
    lv = sv = 0; details = []
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        df = fetch_df(exchange, symbol, "15m")
        frame = add_indicators(df) if df is not None else None
        if frame is None: continue
        a = frame.iloc[-2]
        lo, sh = a.close >= a.ema20 and a.macd_hist >= 0, a.close <= a.ema20 and a.macd_hist <= 0
        lv += int(lo); sv += int(sh); details.append({"symbol":symbol,"side":"LONG" if lo else "SHORT" if sh else "NEUTRAL"})
    mixed = lv < 2 and sv < 2
    return {"long_ok": lv >= 2 or mixed, "short_ok": sv >= 2 or mixed, "long_votes": lv, "short_votes": sv, "details": details}


def close_position(p, price, ts, reason):
    gross = r_multiple(p["direction"], float(p["entry"]), price, float(p["risk_abs"]))
    cost = estimate_round_trip_cost_r(float(p["entry"]), float(p["risk_abs"]))
    unit = gross - cost; factor = float(p.get("size_factor") or 1)
    p.update({"status":"CLOSED","exit_price":round(price,12),"closed_at":ts,"closed_at_tr":tr_text(ts),"closed_day":day_key(ts),
              "close_reason":reason,"gross_r":round(gross,4),"cost_r":round(cost,4),"unit_net_r":round(unit,4),"weighted_net_r":round(unit*factor,4)})


def open_entry_position(ledger, signal, ts, regime):
    symbol, direction = str(signal["symbol"]), str(signal["direction"]).upper()
    entry, sl = float(signal["entry"]), float(signal["sl"]); risk = abs(entry-sl)
    pid, cid = f"{symbol}_{direction}_ENTRY_{ts}", f"CHAIN_{symbol}_{ts}"
    ledger["positions"][pid] = {"position_id":pid,"chain_id":cid,"parent_position_id":None,"symbol":symbol,"direction":direction,
        "source":signal.get("source"),"score":int(signal.get("score") or 0),"quality":signal.get("quality"),"entry":entry,"sl":sl,
        "tp1":float(signal["tp1"]),"tp2":float(signal["tp2"]),"tp3":float(signal["tp3"]),"risk_abs":risk,
        "risk_percent":risk_percent(entry,risk),"size_factor":1.0,"reverse_count":0,"reverse_origin":False,"opened_at":ts,
        "opened_at_tr":tr_text(ts),"opened_day":day_key(ts),"status":"OPEN","tp1_hit":False,"tp2_hit":False,
        "max_favorable_r":0.0,"max_adverse_r":0.0,"last_checked_at":ts,"regime_at_open":regime}
    ledger["chains"][cid] = {"chain_id":cid,"symbol":symbol,"opened_at":ts,"root_position_id":pid,"reverse_used":False,"status":"OPEN"}
    return pid


def create_benchmark(ledger, p, ts, price):
    bid = f"BENCH_{p['position_id']}_{ts}"
    ledger["benchmarks"][bid] = {"benchmark_id":bid,"chain_id":p["chain_id"],"symbol":p["symbol"],"direction":p["direction"],
        "entry":p["entry"],"sl":p["sl"],"tp3":p["tp3"],"risk_abs":p["risk_abs"],"original_opened_at":p["opened_at"],
        "reverse_at":ts,"reverse_price":price,"status":"OPEN"}
    return bid


def reverse_targets(direction, entry, df15):
    from strategy import add_indicators
    f = add_indicators(df15); atr = safe_float(f.iloc[-2].get("atr"), None) if f is not None else None
    risk = min(max((atr or 0)*1.25, entry*0.004), entry*0.02)
    s = 1 if direction == "LONG" else -1
    return {"sl":entry-s*risk,"tp1":entry+s*risk,"tp2":entry+s*2*risk,"tp3":entry+s*3*risk,"risk_abs":risk}


def open_reverse_position(ledger, parent, info, price, df15, ts):
    d, score = info["reverse_to"], int(info["score"]); t = reverse_targets(d, price, df15)
    pid = f"{parent['symbol']}_{d}_REVERSE_{ts}"
    ledger["positions"][pid] = {"position_id":pid,"chain_id":parent["chain_id"],"parent_position_id":parent["position_id"],
        "symbol":parent["symbol"],"direction":d,"source":"AUTONOMOUS_REVERSE_ENGINE","score":score,"entry":price,"sl":t["sl"],
        "tp1":t["tp1"],"tp2":t["tp2"],"tp3":t["tp3"],"risk_abs":t["risk_abs"],"risk_percent":risk_percent(price,t["risk_abs"]),
        "size_factor":size_factor_for_score(score),"reverse_count":1,"reverse_origin":True,"reverse_reasons":info["reasons"],
        "opened_at":ts,"opened_at_tr":tr_text(ts),"opened_day":day_key(ts),"status":"OPEN","tp1_hit":False,"tp2_hit":False,
        "max_favorable_r":0.0,"max_adverse_r":0.0,"last_checked_at":ts}
    c = ledger["chains"][parent["chain_id"]]; c.update({"reverse_used":True,"reverse_position_id":pid,"reverse_score":score,"reverse_at":ts})
    return pid


def process_position(p, df5, ts):
    if df5 is None or p.get("status") != "OPEN": return False
    rows = df5[df5.time > int(p.get("last_checked_at") or 0)*1000]
    if rows.empty: rows = df5.tail(2)
    e, sl, t1, t2, t3, risk = map(float,[p["entry"],p["sl"],p["tp1"],p["tp2"],p["tp3"],p["risk_abs"]]); d = p["direction"]
    for _, r in rows.iterrows():
        ct, hi, lo = int(float(r.time)/1000), float(r.high), float(r.low)
        fav = (hi-e)/risk if d=="LONG" else (e-lo)/risk; adv = (e-lo)/risk if d=="LONG" else (hi-e)/risk
        p["max_favorable_r"], p["max_adverse_r"] = round(max(float(p.get("max_favorable_r") or 0),fav),4), round(max(float(p.get("max_adverse_r") or 0),adv),4)
        if d=="LONG":
            if lo<=sl: close_position(p,sl,ct,"SL"); return True
            p["tp1_hit"] |= hi>=t1; p["tp2_hit"] |= hi>=t2
            if hi>=t3: close_position(p,t3,ct,"TP3"); return True
        else:
            if hi>=sl: close_position(p,sl,ct,"SL"); return True
            p["tp1_hit"] |= lo<=t1; p["tp2_hit"] |= lo<=t2
            if lo<=t3: close_position(p,t3,ct,"TP3"); return True
        p["last_checked_at"] = max(int(p.get("last_checked_at") or 0),ct)
    if ts-int(p["opened_at"]) >= MAX_POSITION_HOURS*3600:
        close_position(p,float(rows.iloc[-1].close),ts,"EXPIRED"); return True
    return False


def process_benchmark(b, df5, ts):
    if b.get("status")!="OPEN" or df5 is None: return
    rows = df5[df5.time >= int(b["reverse_at"])*1000]; d=b["direction"]; sl,tp3=float(b["sl"]),float(b["tp3"])
    exit_price=reason=closed_at=None
    for _,r in rows.iterrows():
        hi,lo,ct=float(r.high),float(r.low),int(float(r.time)/1000)
        if d=="LONG" and lo<=sl: exit_price,reason,closed_at=sl,"NO_REVERSE_SL",ct; break
        if d=="LONG" and hi>=tp3: exit_price,reason,closed_at=tp3,"NO_REVERSE_TP3",ct; break
        if d=="SHORT" and hi>=sl: exit_price,reason,closed_at=sl,"NO_REVERSE_SL",ct; break
        if d=="SHORT" and lo<=tp3: exit_price,reason,closed_at=tp3,"NO_REVERSE_TP3",ct; break
    if exit_price is None and ts >= int(b["original_opened_at"]+MAX_POSITION_HOURS*3600):
        exit_price=float(rows.iloc[-1].close) if not rows.empty else float(b["entry"]); reason="NO_REVERSE_EXPIRED"; closed_at=ts
    if exit_price is not None:
        gross=r_multiple(d,float(b["entry"]),exit_price,float(b["risk_abs"])); cost=estimate_round_trip_cost_r(float(b["entry"]),float(b["risk_abs"]))
        b.update({"status":"CLOSED","exit_price":round(exit_price,12),"closed_at":closed_at,"close_reason":reason,
                  "gross_r":round(gross,4),"cost_r":round(cost,4),"net_r":round(gross-cost,4)})


def maybe_reverse(ledger, p, exchange, df5, df15, df1h, ts):
    if p.get("status")!="OPEN" or p.get("reverse_origin") or int(p.get("reverse_count") or 0)>=MAX_REVERSES_PER_CHAIN: return False
    price=latest_price(exchange,p["symbol"])
    if price is None: return False
    cur=r_multiple(p["direction"],float(p["entry"]),price,float(p["risk_abs"])); p["last_current_r"]=round(cur,4)
    if cur<MIN_REVERSE_PROFIT_R: return False
    info=calculate_reverse_score(p["direction"],df5,df15,df1h)
    p.update({"last_reverse_score":info["score"],"last_reverse_hard_gate":info["hard_gate"],"last_reverse_reasons":info["reasons"]})
    if not info["hard_gate"] or info["score"]<REVERSE_SCORE_THRESHOLD: return False
    create_benchmark(ledger,p,ts,price); close_position(p,price,ts,"REVERSE"); open_reverse_position(ledger,p,info,price,df15,ts)
    print(f"REVERSE {p['symbol']} {p['direction']} -> {info['reverse_to']} score={info['score']}"); return True


def monitor(ledger, exchange, ts):
    reverses=0
    for p in list(open_positions(ledger)):
        df5,df15,df1=fetch_df(exchange,p["symbol"],"5m"),fetch_df(exchange,p["symbol"],"15m"),fetch_df(exchange,p["symbol"],"1h")
        if df5 is None or process_position(p,df5,ts): continue
        if df15 is not None and df1 is not None and maybe_reverse(ledger,p,exchange,df5,df15,df1,ts): reverses+=1
    for b in ledger["benchmarks"].values():
        if isinstance(b,dict) and b.get("status")=="OPEN": process_benchmark(b,fetch_df(exchange,b["symbol"],"5m"),ts)
    return reverses


def scan_entries(ledger,state,exchange,universe,regime,ts):
    from strategy import analyze_mtf_trade
    if len(open_positions(ledger))>=MAX_OPEN_POSITIONS or daily_closed_net_r(ledger,ts)<=DAILY_RISK_STOP_R: return 0
    rows,nxt=select_rotation(universe,int(state.get("rotation_cursor") or 0)); state["rotation_cursor"]=nxt; found=[]
    for row in rows:
        s=row["symbol"]
        if symbol_is_open(ledger,s) or recent_symbol_close(ledger,s,ts): continue
        d15,d1,d4=fetch_df(exchange,s,"15m"),fetch_df(exchange,s,"1h"),fetch_df(exchange,s,"4h")
        if d15 is None or d1 is None or d4 is None: continue
        sig=analyze_mtf_trade(s,d15,d1,d4,current_price=float(row["last"]))
        if not sig or sig.get("signal_class")!="TRADE" or int(sig.get("score") or 0)<MIN_ENTRY_SCORE: continue
        d=str(sig.get("direction") or "").upper()
        if (d=="LONG" and not regime["long_ok"]) or (d=="SHORT" and not regime["short_ok"]): continue
        found.append((int(sig["score"]),sig))
    found.sort(key=lambda x:x[0],reverse=True); opened=0
    for _,sig in found:
        if opened>=MAX_NEW_POSITIONS_PER_RUN or len(open_positions(ledger))>=MAX_OPEN_POSITIONS: break
        if not symbol_is_open(ledger,sig["symbol"]): open_entry_position(ledger,sig,ts,regime); opened+=1
    return opened


def update_chains(ledger):
    ps=list(ledger["positions"].values()); bs=list(ledger["benchmarks"].values())
    for cid,c in ledger["chains"].items():
        legs=[p for p in ps if isinstance(p,dict) and p.get("chain_id")==cid]
        if not legs: continue
        c["strategy_weighted_net_r"]=round(sum(float(p.get("weighted_net_r") or 0) for p in legs if p.get("status")=="CLOSED"),4)
        b=next((x for x in bs if isinstance(x,dict) and x.get("chain_id")==cid),None)
        if b and b.get("status")=="CLOSED":
            c["benchmark_net_r"]=b["net_r"]; c["reverse_contribution_r"]=round(c["strategy_weighted_net_r"]-float(b["net_r"]),4)
        if not any(p.get("status")=="OPEN" for p in legs) and (not b or b.get("status")=="CLOSED"): c["status"]="CLOSED"


def summary(ledger,ts):
    ps=[p for p in ledger["positions"].values() if isinstance(p,dict)]; cs=[c for c in ledger["chains"].values() if isinstance(c,dict)]
    closed=[p for p in ps if p.get("status")=="CLOSED"]; compared=[c for c in cs if c.get("reverse_contribution_r") is not None]
    better=[c for c in compared if float(c["reverse_contribution_r"])>0]
    return {"updated_at":ts,"updated_at_tr":tr_text(ts),"open_positions":sum(p.get("status")=="OPEN" for p in ps),
        "closed_legs":len(closed),"closed_weighted_net_r":round(sum(float(p.get("weighted_net_r") or 0) for p in closed),4),
        "today_closed_net_r":daily_closed_net_r(ledger,ts),"reverse_chains_compared":len(compared),"reverse_better_count":len(better),
        "reverse_better_rate_percent":round(len(better)/len(compared)*100,2) if compared else None,
        "reverse_contribution_total_r":round(sum(float(c.get("reverse_contribution_r") or 0) for c in compared),4)}


def run():
    ts=now_ts(); state=load_json(STATE_FILE,empty_state()); ledger=ensure_ledger(load_json(LEDGER_FILE,empty_ledger())); ex=get_exchange()
    reverses=monitor(ledger,ex,ts); regime=market_regime(ex); universe=build_universe(ex); opened=scan_entries(ledger,state,ex,universe,regime,ts)
    update_chains(ledger); ledger["summary"]=summary(ledger,ts); ledger["last_update"]=ts; ledger["last_update_tr"]=tr_text(ts)
    state.update({"version":VERSION,"mode":MODE,"last_run":ts,"last_run_tr":tr_text(ts),"last_universe_count":len(universe),"last_regime":regime})
    save_json_atomic(LEDGER_FILE,ledger); save_json_atomic(STATE_FILE,state)
    result={"version":VERSION,"mode":MODE,"opened":opened,"reverses":reverses,"summary":ledger["summary"]}
    print(json.dumps(result,ensure_ascii=False,indent=2)); return result


if __name__ == "__main__": run()
