# main.py
import os, time, json, requests, ccxt
import pandas as pd
from datetime import datetime, timezone, timedelta
from config import *
from strategy import analyze_normal_signal, analyze_radar_signal, format_price

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
OPEN_SIGNALS_FILE = "open_signals.json"
PERFORMANCE_FILE = "performance.json"
LAST_SIGNALS_FILE = "last_signals.json"
TR_TIMEZONE = timezone(timedelta(hours=3))


def send_telegram(message):
    if not TOKEN or not CHAT_ID:
        print("TOKEN veya CHAT_ID eksik.")
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={"chat_id": CHAT_ID, "text": message}, timeout=20)
        print("Telegram cevap:", r.status_code, r.text)
        return r.status_code == 200
    except Exception as e:
        print("Telegram gönderim hatası:", e)
        return False


def load_json_file(filename):
    try:
        if not os.path.exists(filename):
            return {}
        text = open(filename, "r", encoding="utf-8").read().strip()
        if not text:
            return {}
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(filename, "okuma hatası:", e)
        return {}


def save_json_file(filename, data):
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data if isinstance(data, dict) else {}, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print(filename, "kaydetme hatası:", e)
        return False


def load_open_signals(): return load_json_file(OPEN_SIGNALS_FILE)
def save_open_signals(data): return save_json_file(OPEN_SIGNALS_FILE, data)
def load_performance(): return load_json_file(PERFORMANCE_FILE)
def save_performance(data): return save_json_file(PERFORMANCE_FILE, data)
def load_last_signals(): return load_json_file(LAST_SIGNALS_FILE)
def save_last_signals(data): return save_json_file(LAST_SIGNALS_FILE, data)
def now_ts(): return int(time.time())
def today_key(): return datetime.now(TR_TIMEZONE).strftime("%Y-%m-%d")


def ensure_perf_day(performance):
    today = today_key()
    performance.setdefault("days", {})
    performance["days"].setdefault(today, {"opened":0,"watch":0,"tp1":0,"tp2":0,"tp3":0,"sl":0,"be":0,"expired":0,"coins":{},"long":0,"short":0,"normal":0,"radar":0,"direction_stops":{},"stop_times":{}})
    return performance


def update_performance(symbol, result, direction=None, source=None):
    p = ensure_perf_day(load_performance())
    day = p["days"][today_key()]
    if result == "OPENED":
        day["opened"] += 1
        if direction == "LONG": day["long"] += 1
        if direction == "SHORT": day["short"] += 1
        if source == "RADAR": day["radar"] += 1
        else: day["normal"] += 1
    elif result == "WATCH":
        day["watch"] += 1
    elif result in ["TP1","TP2","TP3","SL","BE","EXPIRED"]:
        day[result.lower()] = int(day.get(result.lower(),0)) + 1
        if result == "SL" and direction in ["LONG","SHORT"]:
            day.setdefault("direction_stops", {})
            day["direction_stops"][direction] = int(day["direction_stops"].get(direction,0)) + 1
            day.setdefault("stop_times", {})
            day["stop_times"][symbol] = now_ts()
    day.setdefault("coins", {})
    day["coins"].setdefault(symbol, {"opened":0,"watch":0,"tp1":0,"tp2":0,"tp3":0,"sl":0,"be":0,"expired":0})
    coin = day["coins"][symbol]
    if result == "OPENED": coin["opened"] += 1
    elif result == "WATCH": coin["watch"] += 1
    elif result in ["TP1","TP2","TP3","SL","BE","EXPIRED"]: coin[result.lower()] = int(coin.get(result.lower(),0)) + 1
    p["last_update"] = now_ts()
    save_performance(p)


def get_today_sl_count(): return int(load_performance().get("days",{}).get(today_key(),{}).get("sl",0))
def risk_mode_active(): return get_today_sl_count() >= RISK_MODE_STOP_COUNT


def has_recent_stop(symbol):
    day = load_performance().get("days",{}).get(today_key(),{})
    t = int(day.get("stop_times",{}).get(symbol,0))
    return t > 0 and now_ts() - t < STOPPED_COIN_COOLDOWN_HOURS * 3600


def get_exchange(): return ccxt.okx({"enableRateLimit": True, "options": {"defaultType": "swap"}})
def to_okx_symbol(symbol): return f"{symbol.replace('USDT','')}/USDT:USDT"
def okx_symbol_to_bot_symbol(okx_symbol): return f"{okx_symbol.split('/')[0]}USDT".upper()


def safe_quote_volume(ticker):
    try:
        if ticker.get("quoteVolume") is not None: return float(ticker.get("quoteVolume"))
        info = ticker.get("info", {})
        for k in ["volCcy24h", "volUsd24h", "vol24h"]:
            if info.get(k) is not None: return float(info.get(k))
    except Exception: pass
    return 0.0


def get_scan_coins(exchange):
    if not AUTO_TOP_VOLUME_SCAN: return COINS
    try:
        markets = exchange.load_markets()
        okx_symbols = []
        stable = {"USDT","USDC","DAI","FDUSD","TUSD","USDP","USD"}
        for m in markets.values():
            if not m.get("active", True) or not m.get("swap", False): continue
            if m.get("quote") != "USDT" or m.get("settle") != "USDT": continue
            s = m.get("symbol")
            if not s or "/USDT:USDT" not in s: continue
            if str(m.get("base","")).upper() in stable: continue
            okx_symbols.append(s)
        tickers = exchange.fetch_tickers(okx_symbols)
        rows = []
        for s in okx_symbols:
            vol = safe_quote_volume(tickers.get(s, {}))
            if vol >= MIN_24H_QUOTE_VOLUME: rows.append((okx_symbol_to_bot_symbol(s), vol))
        if not rows: return COINS
        rows.sort(key=lambda x: x[1], reverse=True)
        volume_coins = [c for c,_ in rows]
        priority = [c for c in COINS if c in volume_coins]
        others = [c for c in volume_coins if c not in priority]
        scan = (priority + others)[:MAX_SCAN_COINS]
        print("Taranacak coin:", len(scan), scan[:10])
        return scan
    except Exception as e:
        print("Top volume tarama hatası:", e)
        return COINS


def fetch_df(exchange, symbol, timeframe, limit, min_len=30):
    try:
        ohlcv = exchange.fetch_ohlcv(to_okx_symbol(symbol), timeframe=timeframe, limit=limit)
        if not ohlcv or len(ohlcv) < min_len: return None
        return pd.DataFrame(ohlcv, columns=["time","open","high","low","close","volume"])
    except Exception as e:
        print(symbol, timeframe, "veri hatası:", e)
        return None


def simple_ema(series, span): return series.ewm(span=span, adjust=False).mean()


def get_market_direction_status(exchange):
    if not MARKET_GUARD_ENABLED: return {"LONG": True, "SHORT": True, "reason": "kapalı"}
    long_ok = short_ok = hard_red = hard_green = 0
    details = []
    for ref in MARKET_REFERENCE_COINS:
        try:
            df15 = fetch_df(exchange, ref, ENTRY_TIMEFRAME, 80, 40)
            df5 = fetch_df(exchange, ref, RADAR_TIMEFRAME, 40, 20)
            if df15 is None or df5 is None: continue
            df15 = df15.copy(); df15["ema20"] = simple_ema(df15["close"],20)
            last15 = df15.iloc[-2]; close15 = float(last15["close"]); ema20 = float(last15["ema20"])
            last5 = df5.iloc[-2]; move5 = ((float(last5["close"])-float(last5["open"]))/float(last5["open"]))*100
            if close15 >= ema20 and move5 > -MARKET_MAX_COUNTER_5M_MOVE_PERCENT: long_ok += 1
            if close15 <= ema20 and move5 < MARKET_MAX_COUNTER_5M_MOVE_PERCENT: short_ok += 1
            if move5 <= -MARKET_MAX_COUNTER_5M_MOVE_PERCENT: hard_red += 1
            if move5 >= MARKET_MAX_COUNTER_5M_MOVE_PERCENT: hard_green += 1
            details.append(f"{ref}: 5M %{round(move5,2)}")
        except Exception as e: print(ref, "market hatası:", e)
    return {"LONG": long_ok >= MARKET_LONG_MIN_OK_COUNT and hard_red < 2, "SHORT": short_ok >= MARKET_SHORT_MIN_OK_COUNT and hard_green < 2, "reason": " | ".join(details)}


def fetch_candles_since(exchange, symbol, timeframe, since_seconds, limit=150):
    try:
        ohlcv = exchange.fetch_ohlcv(to_okx_symbol(symbol), timeframe=timeframe, since=max(0,int(since_seconds))*1000, limit=limit)
        return [{"time":int(i[0]/1000),"open":float(i[1]),"high":float(i[2]),"low":float(i[3]),"close":float(i[4])} for i in ohlcv]
    except Exception as e:
        print(symbol, "takip mum hatası:", e); return []


def get_current_price(exchange, symbol):
    try:
        price = exchange.fetch_ticker(to_okx_symbol(symbol)).get("last")
        return float(price) if price is not None else None
    except Exception as e:
        print(symbol, "güncel fiyat hatası:", e); return None


def is_entry_still_valid(signal, current_price):
    try:
        entry, tp1, sl = float(signal["entry"]), float(signal["tp1"]), float(signal["sl"])
        d = signal["direction"]
        if current_price is None or entry <= 0: return False, "güncel fiyat yok"
        if abs((current_price-entry)/entry)*100 > MAX_ENTRY_DISTANCE_PERCENT: return False, "girişten uzak"
        if d == "LONG":
            total = tp1-entry; progressed = current_price-entry
            if total <= 0: return False, "TP1 hatalı"
            if (progressed/total)*100 >= MAX_TP1_PROGRESS_PERCENT: return False, "TP1'e yaklaşmış"
            if current_price >= tp1: return False, "TP1 zaten gelmiş"
            if current_price <= sl: return False, "SL tarafında"
        else:
            total = entry-tp1; progressed = entry-current_price
            if total <= 0: return False, "TP1 hatalı"
            if (progressed/total)*100 >= MAX_TP1_PROGRESS_PERCENT: return False, "TP1'e yaklaşmış"
            if current_price <= tp1: return False, "TP1 zaten gelmiş"
            if current_price >= sl: return False, "SL tarafında"
        return True, "uygun"
    except Exception as e: return False, f"giriş kontrol hatası: {e}"


def is_duplicate(signal, watch=False):
    data = load_last_signals(); prefix = "WATCH" if watch else "TRADE"
    key = f"{prefix}_{signal['symbol']}_{signal['direction']}"
    wait = WATCH_DUPLICATE_BLOCK_SECONDS if watch else DUPLICATE_BLOCK_SECONDS
    return now_ts() - int(data.get(key, 0)) < wait


def mark_sent(signal, watch=False):
    data = load_last_signals(); prefix = "WATCH" if watch else "TRADE"
    data[f"{prefix}_{signal['symbol']}_{signal['direction']}"] = now_ts()
    save_last_signals(data)


def has_open_same_symbol(symbol): return any(s.get("symbol") == symbol for s in load_open_signals().values())


def check_open_signals(exchange):
    open_signals = load_open_signals()
    if not open_signals: print("Açık sinyal yok."); return
    updated = {}; max_age = MAX_OPEN_SIGNAL_HOURS * 3600
    for key, sig in open_signals.items():
        try:
            symbol, d = sig["symbol"], sig["direction"]
            entry, tp1, tp2, tp3, sl = float(sig["entry"]), float(sig["tp1"]), float(sig["tp2"]), float(sig["tp3"]), float(sig["sl"])
            opened_at = int(sig.get("opened_at", now_ts())); last_checked = int(sig.get("last_checked_at", opened_at))
            if now_ts() - opened_at > max_age:
                send_telegram(f"⏳ SİNYAL SÜRESİ DOLDU\n\nCoin: {symbol}\nYön: {d}\nGiriş: {format_price(entry)}")
                update_performance(symbol, "EXPIRED"); continue
            candles = fetch_candles_since(exchange, symbol, ENTRY_TIMEFRAME, max(opened_at, last_checked-1200), 120)
            if not candles: updated[key] = sig; continue
            high, low = max(c["high"] for c in candles), min(c["low"] for c in candles)
            current = get_current_price(exchange, symbol)
            tp1_hit, tp2_hit = bool(sig.get("tp1_hit", False)), bool(sig.get("tp2_hit", False))
            if d == "LONG":
                if not tp1_hit and low <= sl:
                    send_telegram(f"❌ STOP OLDU\n\nCoin: {symbol}\nYön: LONG 🟢\nGiriş: {format_price(entry)}\nSL: {format_price(sl)}\nGüncel: {format_price(current or sl)}")
                    update_performance(symbol, "SL", direction=d, source=sig.get("source")); continue
                if not tp1_hit and high >= tp1:
                    send_telegram(f"✅ TP1 GELDİ\n\nCoin: {symbol}\nYön: LONG 🟢\nGiriş: {format_price(entry)}\nTP1: {format_price(tp1)}\nÖneri: %50 kâr al, kalan işlem için SL girişe çek.")
                    sig["tp1_hit"] = True; tp1_hit = True; update_performance(symbol, "TP1")
                if tp1_hit and not tp2_hit and high >= tp2:
                    send_telegram(f"✅ TP2 GELDİ\n\nCoin: {symbol}\nYön: LONG 🟢\nTP2: {format_price(tp2)}")
                    sig["tp2_hit"] = True; tp2_hit = True; update_performance(symbol, "TP2")
                if tp1_hit and high >= tp3:
                    send_telegram(f"🏁 TP3 GELDİ\n\nCoin: {symbol}\nYön: LONG 🟢\nTP3: {format_price(tp3)}")
                    update_performance(symbol, "TP3"); continue
                if tp1_hit and low <= entry:
                    send_telegram(f"🟡 KALAN İŞLEM GİRİŞTEN KAPANDI\n\nCoin: {symbol}\nYön: LONG 🟢\nGiriş: {format_price(entry)}")
                    update_performance(symbol, "BE"); continue
            else:
                if not tp1_hit and high >= sl:
                    send_telegram(f"❌ STOP OLDU\n\nCoin: {symbol}\nYön: SHORT 🔴\nGiriş: {format_price(entry)}\nSL: {format_price(sl)}\nGüncel: {format_price(current or sl)}")
                    update_performance(symbol, "SL", direction=d, source=sig.get("source")); continue
                if not tp1_hit and low <= tp1:
                    send_telegram(f"✅ TP1 GELDİ\n\nCoin: {symbol}\nYön: SHORT 🔴\nGiriş: {format_price(entry)}\nTP1: {format_price(tp1)}\nÖneri: %50 kâr al, kalan işlem için SL girişe çek.")
                    sig["tp1_hit"] = True; tp1_hit = True; update_performance(symbol, "TP1")
                if tp1_hit and not tp2_hit and low <= tp2:
                    send_telegram(f"✅ TP2 GELDİ\n\nCoin: {symbol}\nYön: SHORT 🔴\nTP2: {format_price(tp2)}")
                    sig["tp2_hit"] = True; tp2_hit = True; update_performance(symbol, "TP2")
                if tp1_hit and low <= tp3:
                    send_telegram(f"🏁 TP3 GELDİ\n\nCoin: {symbol}\nYön: SHORT 🔴\nTP3: {format_price(tp3)}")
                    update_performance(symbol, "TP3"); continue
                if tp1_hit and high >= entry:
                    send_telegram(f"🟡 KALAN İŞLEM GİRİŞTEN KAPANDI\n\nCoin: {symbol}\nYön: SHORT 🔴\nGiriş: {format_price(entry)}")
                    update_performance(symbol, "BE"); continue
            sig["last_checked_at"] = now_ts(); updated[key] = sig
        except Exception as e:
            print(key, "açık sinyal takip hatası:", e); updated[key] = sig
    save_open_signals(updated)


def should_send_status(): return now_ts() - int(load_performance().get("last_status_message",0)) >= SEND_STATUS_EVERY_MINUTES*60

def mark_status_sent():
    p = load_performance(); p["last_status_message"] = now_ts(); save_performance(p)


def maybe_send_open_summary(exchange):
    p = load_performance(); last = int(p.get("last_open_summary",0))
    if now_ts() - last < OPEN_SUMMARY_EVERY_MINUTES*60: return
    open_signals = load_open_signals()
    if not open_signals: return
    lines = ["📌 AÇIK SİNYAL ÖZETİ\n"]
    for sig in list(open_signals.values())[:10]:
        try:
            symbol, d = sig["symbol"], sig["direction"]; entry, tp1, sl = float(sig["entry"]), float(sig["tp1"]), float(sig["sl"])
            cur = get_current_price(exchange, symbol)
            if cur is None: continue
            if d == "LONG": profit = ((cur-entry)/entry)*100; tpdist = ((tp1-cur)/cur)*100; icon="🟢"
            else: profit = ((entry-cur)/entry)*100; tpdist = ((cur-tp1)/cur)*100; icon="🔴"
            lines.append(f"{icon} {symbol} {d}\nGiriş: {format_price(entry)} | Güncel: {format_price(cur)}\nTP1: {format_price(tp1)} | SL: {format_price(sl)}\nDurum: %{round(profit,2)} | TP1 uzaklık: %{round(tpdist,2)}\n")
        except Exception as e: print("Özet hatası:", e)
    send_telegram("\n".join(lines)); p["last_open_summary"] = now_ts(); save_performance(p)


def build_watch_message(signal):
    icon = "🟢" if signal["direction"] == "LONG" else "🔴"
    return f"""
🟡 TAKİP RADARI - İŞLEM AÇMA

{icon} {signal["direction"]}
Coin: {signal["symbol"]}

Coin hareketleniyor ama A kalite şartı tam oluşmadı.

Giriş adayı: {format_price(signal["entry"])}
TP1 adayı: {format_price(signal["tp1"])}
SL adayı: {format_price(signal["sl"])}

Skor: %{signal["score"]}
Kaynak: {signal["source"]}
Hacim: {signal["volume_ratio"]}x
RSI 15M: {signal["rsi_15m"]}
ADX 15M: {signal["adx_15m"]}

A kalite giriş sinyali gelmeden işlem açma.
"""


def build_daily_report():
    day = load_performance().get("days",{}).get(today_key(),{})
    opened, watch, tp1, tp2, tp3, sl, be, expired = [int(day.get(k,0)) for k in ["opened","watch","tp1","tp2","tp3","sl","be","expired"]]
    longc, shortc, normal, radar = [int(day.get(k,0)) for k in ["long","short","normal","radar"]]
    success = round((tp1/(tp1+sl))*100,2) if tp1+sl > 0 else 0
    return f"""
📊 GÜNLÜK PERFORMANS RAPORU

📅 Tarih: {today_key()}

📈 Açılan A Kalite Sinyal: {opened}
🟡 Takip Radarı: {watch}
🟢 LONG: {longc}
🔴 SHORT: {shortc}
✅ Normal: {normal}
⚡ Radar: {radar}

✅ TP1 Gelen: {tp1}
✅ TP2 Gelen: {tp2}
✅ TP3 Gelen: {tp3}
🟡 Girişten Kapanan: {be}
❌ Stop Olan: {sl}
⏳ Süresi Dolan: {expired}
📌 Açık Sinyal: {len(load_open_signals())}

📊 TP1 Başarı Oranı: %{success}

📌 Not:
Takip radarları işlem sinyali sayılmaz.
TP1 sonrası kalan işlem için SL giriş fiyatı kabul edilir.
Bu bot emir açmaz, sadece sinyal gönderir.
"""


def maybe_send_daily_report():
    now = datetime.now(TR_TIMEZONE); today = today_key()
    if now.hour != DAILY_REPORT_HOUR or now.minute < DAILY_REPORT_MINUTE: return
    p = load_performance()
    if p.get("last_daily_report") == today: return
    send_telegram(build_daily_report()); p["last_daily_report"] = today; save_performance(p)


def save_open_signal(signal):
    data = load_open_signals(); key = f"{signal['symbol']}_{signal['direction']}_{signal.get('source','NORMAL')}"
    data[key] = {"symbol":signal["symbol"],"direction":signal["direction"],"source":signal.get("source","NORMAL"),"entry":signal["entry"],"tp1":signal["tp1"],"tp2":signal["tp2"],"tp3":signal["tp3"],"sl":signal["sl"],"score":signal["score"],"risk_percent":signal.get("risk_percent"),"opened_at":now_ts(),"last_checked_at":now_ts(),"tp1_hit":False,"tp2_hit":False,"tp3_hit":False}
    save_open_signals(data)


def main():
    print(BOT_NAME, "başladı.")
    exchange = get_exchange()
    check_open_signals(exchange); maybe_send_open_summary(exchange)
    risk_mode = risk_mode_active()
    if risk_mode and should_send_status():
        send_telegram(f"🟡 {BOT_NAME} çalıştı.\n\nRiskli Piyasa Modu aktif.\nSistem durmadı; daha seçici çalışıyor.\nBugünkü stop: {get_today_sl_count()}"); mark_status_sent()
    scan_coins = get_scan_coins(exchange); market_status = get_market_direction_status(exchange)
    print("Taranan coin:", len(scan_coins), "Açık:", len(load_open_signals()), "Risk:", risk_mode)
    trade_candidates, watch_candidates = [], []
    for symbol in scan_coins:
        try:
            if len(load_open_signals()) >= MAX_OPEN_SIGNALS:
                print("Maksimum açık sinyal sınırı doldu."); break
            if has_open_same_symbol(symbol) or has_recent_stop(symbol): continue
            current = get_current_price(exchange, symbol)
            df15 = fetch_df(exchange, symbol, ENTRY_TIMEFRAME, ENTRY_LIMIT, 120)
            df1h = fetch_df(exchange, symbol, CONFIRM_TIMEFRAME, CONFIRM_LIMIT, 120)
            df4h = fetch_df(exchange, symbol, TREND_TIMEFRAME, TREND_LIMIT, 120)
            signals = []
            normal = analyze_normal_signal(symbol, df15, df1h, df4h, current)
            if normal: signals.append(normal)
            if RADAR_ENABLED:
                df5 = fetch_df(exchange, symbol, RADAR_TIMEFRAME, RADAR_LIMIT, 50)
                radar = analyze_radar_signal(symbol, df5, df15, df1h, df4h, current)
                if radar: signals.append(radar)
            for sig in signals:
                if sig["direction"] == "LONG" and not ALLOW_LONG: continue
                if sig["direction"] == "SHORT" and not ALLOW_SHORT: continue
                if sig["signal_class"] == "TRADE" and not market_status.get(sig["direction"], True): sig["signal_class"] = "WATCH"
                if risk_mode and sig.get("source") == "RADAR" and sig["signal_class"] == "TRADE" and not RISK_MODE_ALLOW_RADAR_TRADE: sig["signal_class"] = "WATCH"
                valid, reason = is_entry_still_valid(sig, current)
                if not valid:
                    print(symbol, "giriş elendi ->", reason); continue
                if sig["signal_class"] == "TRADE" and not is_duplicate(sig, False): trade_candidates.append(sig)
                elif sig["signal_class"] == "WATCH" and not is_duplicate(sig, True): watch_candidates.append(sig)
            time.sleep(0.10)
        except Exception as e: print(symbol, "analiz hatası:", e)
    trade_candidates.sort(key=lambda s: s["score"], reverse=True); watch_candidates.sort(key=lambda s: s["score"], reverse=True)
    selected_trade = trade_candidates[:(RISK_MODE_MAX_TRADE_SIGNALS if risk_mode else MAX_TRADE_SIGNALS_PER_RUN)]
    selected_watch = watch_candidates[:(RISK_MODE_MAX_WATCH_ALERTS if risk_mode else MAX_WATCH_ALERTS_PER_RUN)]
    if selected_trade:
        send_telegram(f"✅ {BOT_NAME} çalıştı.\nTaranan coin: {len(scan_coins)}\nA kalite aday: {len(trade_candidates)}\nGönderilen işlem sinyali: {len(selected_trade)}\nRiskli Piyasa Modu: {'AKTİF' if risk_mode else 'Kapalı'}\nSistem: Dengeli V5.1: 4H trend + 1H onay + 15M pullback + hacim + radar.")
        for sig in selected_trade:
            cur = get_current_price(exchange, sig["symbol"]); valid, reason = is_entry_still_valid(sig, cur)
            if not valid: continue
            if send_telegram(sig["message"] + f"\n💰 Güncel Fiyat: {format_price(cur)}\n📌 Son Kontrol: Girişe yakın ✅"):
                save_open_signal(sig); mark_sent(sig, False); update_performance(sig["symbol"], "OPENED", direction=sig["direction"], source=sig.get("source"))
            time.sleep(1)
    if selected_watch:
        send_telegram(f"🟡 {BOT_NAME} takip radarı çalıştı.\nTakip uyarısı: {len(selected_watch)}\nBu mesajlar işlem sinyali değildir.")
        for sig in selected_watch:
            if send_telegram(build_watch_message(sig)):
                mark_sent(sig, True); update_performance(sig["symbol"], "WATCH", direction=sig["direction"], source=sig.get("source"))
            time.sleep(1)
    if not selected_trade and not selected_watch and should_send_status():
        send_telegram(f"📡 {BOT_NAME} çalıştı.\n\nTaranan coin: {len(scan_coins)}\nUygun trend momentum sinyali yok.\nSistem durmadı, taramaya devam ediyor."); mark_status_sent()
    maybe_send_daily_report(); print(BOT_NAME, "tamamlandı.")

if __name__ == "__main__": main()
