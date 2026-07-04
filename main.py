import os
import time
import requests
import pandas as pd
import ccxt

from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD, ADXIndicator
from ta.volatility import AverageTrueRange


TOKEN = os.getenv("8619346423:AAHyaf5nk3IQYvMzEcNAYQFQH8eALdz6220")
CHAT_ID = os.getenv("8439391876")

# Ayarlar
TIMEFRAME = "30m"
LIMIT = 200
SCAN_LIMIT = 50          # Kaç parite taransın
MIN_SCORE = 45           # Sinyal eşiği
MAX_SIGNALS = 5          # En fazla kaç sinyal göndersin


def send_telegram(message):
    if not TOKEN or not CHAT_ID:
        print("TOKEN veya CHAT_ID eksik.")
        return

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }

    try:
        r = requests.post(url, data=data, timeout=20)
        print("Telegram cevap:", r.status_code, r.text)
    except Exception as e:
        print("Telegram gönderim hatası:", e)


def get_exchange():
    return ccxt.okx({
        "enableRateLimit": True,
        "options": {
            "defaultType": "swap"
        }
    })


def get_top_symbols(exchange):
    markets = exchange.load_markets()
    tickers = exchange.fetch_tickers()

    symbols = []

    for symbol, market in markets.items():
        try:
            if not market.get("swap"):
                continue

            if market.get("quote") != "USDT":
                continue

            if not symbol.endswith(":USDT"):
                continue

            ticker = tickers.get(symbol, {})
            quote_volume = ticker.get("quoteVolume") or 0
            last_price = ticker.get("last") or 0

            if last_price <= 0:
                continue

            symbols.append({
                "symbol": symbol,
                "volume": quote_volume
            })

        except Exception:
            continue

    symbols = sorted(symbols, key=lambda x: x["volume"], reverse=True)
    return [x["symbol"] for x in symbols[:SCAN_LIMIT]]


def fetch_df(exchange, symbol):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=LIMIT)
        if not ohlcv or len(ohlcv) < 150:
            return None

        df = pd.DataFrame(
            ohlcv,
            columns=["time", "open", "high", "low", "close", "volume"]
        )

        return df

    except Exception as e:
        print(symbol, "veri hatası:", e)
        return None


def analyze_symbol(symbol, df):
    try:
        df["rsi"] = RSIIndicator(df["close"], window=14).rsi()
        df["ema20"] = EMAIndicator(df["close"], window=20).ema_indicator()
        df["ema50"] = EMAIndicator(df["close"], window=50).ema_indicator()
        df["ema200"] = EMAIndicator(df["close"], window=200).ema_indicator()

        macd = MACD(df["close"])
        df["macd"] = macd.macd()
        df["macd_signal"] = macd.macd_signal()

        adx = ADXIndicator(df["high"], df["low"], df["close"], window=14)
        df["adx"] = adx.adx()

        atr = AverageTrueRange(df["high"], df["low"], df["close"], window=14)
        df["atr"] = atr.average_true_range()

        df["volume_ma"] = df["volume"].rolling(20).mean()

        last = df.iloc[-1]
        prev = df.iloc[-2]

        close = float(last["close"])
        rsi = float(last["rsi"])
        ema20 = float(last["ema20"])
        ema50 = float(last["ema50"])
        ema200 = float(last["ema200"])
        macd_now = float(last["macd"])
        macd_signal = float(last["macd_signal"])
        macd_prev = float(prev["macd"])
        macd_signal_prev = float(prev["macd_signal"])
        adx_val = float(last["adx"])
        atr_val = float(last["atr"])
        volume = float(last["volume"])
        volume_ma = float(last["volume_ma"]) if last["volume_ma"] > 0 else 1

        if pd.isna(rsi) or pd.isna(ema200) or pd.isna(adx_val) or pd.isna(atr_val):
            return None

        volume_ratio = volume / volume_ma
        atr_percent = (atr_val / close) * 100

        long_score = 0
        short_score = 0
        reasons_long = []
        reasons_short = []

        # Trend puanı
        if close > ema20:
            long_score += 8
            reasons_long.append("Fiyat EMA20 üstünde")
        else:
            short_score += 8
            reasons_short.append("Fiyat EMA20 altında")

        if ema20 > ema50:
            long_score += 10
            reasons_long.append("EMA20 EMA50 üstünde")
        else:
            short_score += 10
            reasons_short.append("EMA20 EMA50 altında")

        if close > ema200:
            long_score += 10
            reasons_long.append("Ana trend yukarı")
        else:
            short_score += 10
            reasons_short.append("Ana trend aşağı")

        # RSI puanı
        if 45 <= rsi <= 68:
            long_score += 10
            reasons_long.append("RSI long için uygun")
        elif 32 <= rsi <= 55:
            short_score += 10
            reasons_short.append("RSI short için uygun")

        if rsi > 70:
            short_score += 6
            reasons_short.append("RSI aşırı alım bölgesi")

        if rsi < 30:
            long_score += 6
            reasons_long.append("RSI aşırı satım bölgesi")

        # MACD puanı
        if macd_now > macd_signal:
            long_score += 10
            reasons_long.append("MACD pozitif")
        else:
            short_score += 10
            reasons_short.append("MACD negatif")

        if macd_prev < macd_signal_prev and macd_now > macd_signal:
            long_score += 8
            reasons_long.append("MACD yukarı kesişim")
        elif macd_prev > macd_signal_prev and macd_now < macd_signal:
            short_score += 8
            reasons_short.append("MACD aşağı kesişim")

        # ADX trend gücü
        if adx_val >= 15:
            long_score += 7
            short_score += 7

        if adx_val >= 25:
            long_score += 5
            short_score += 5

        # Hacim
        if volume_ratio >= 1.10:
            long_score += 8
            short_score += 8

        if volume_ratio >= 1.50:
            long_score += 5
            short_score += 5

        # Volatilite
        if atr_percent >= 0.40:
            long_score += 5
            short_score += 5

        if atr_percent >= 0.80:
            long_score += 5
            short_score += 5

        # Çok cansız piyasayı ele
        if adx_val < 12 and volume_ratio < 0.80:
            return None

        if long_score >= short_score:
            side = "LONG"
            score = long_score
            reasons = reasons_long
            entry = close
            sl = entry - (atr_val * 1.5)
            tp1 = entry + (atr_val * 1.5)
            tp2 = entry + (atr_val * 2.5)
            tp3 = entry + (atr_val * 3.5)
        else:
            side = "SHORT"
            score = short_score
            reasons = reasons_short
            entry = close
            sl = entry + (atr_val * 1.5)
            tp1 = entry - (atr_val * 1.5)
            tp2 = entry - (atr_val * 2.5)
            tp3 = entry - (atr_val * 3.5)

        clean_symbol = symbol.replace("/USDT:USDT", "USDT")

        return {
            "symbol": clean_symbol,
            "side": side,
            "score": round(score, 1),
            "entry": entry,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "rsi": round(rsi, 2),
            "adx": round(adx_val, 2),
            "volume_ratio": round(volume_ratio, 2),
            "atr_percent": round(atr_percent, 2),
            "reasons": reasons[:4]
        }

    except Exception as e:
        print(symbol, "analiz hatası:", e)
        return None


def format_signal(signal):
    reasons_text = "\n".join([f"• {r}" for r in signal["reasons"]])

    return f"""
🚨 <b>KRİPTO FUTURES SİNYAL</b>

<b>Parite:</b> {signal["symbol"]}
<b>Yön:</b> {signal["side"]}
<b>Skor:</b> {signal["score"]}/100

<b>Giriş:</b> {signal["entry"]:.6f}

🎯 <b>TP1:</b> {signal["tp1"]:.6f}
🎯 <b>TP2:</b> {signal["tp2"]:.6f}
🎯 <b>TP3:</b> {signal["tp3"]:.6f}

🛑 <b>Stop:</b> {signal["sl"]:.6f}

📊 <b>RSI:</b> {signal["rsi"]}
📊 <b>ADX:</b> {signal["adx"]}
📊 <b>Hacim:</b> {signal["volume_ratio"]}x
📊 <b>Volatilite:</b> %{signal["atr_percent"]}

<b>Sinyal Nedenleri:</b>
{reasons_text}

⚠️ Kaldıraç düşük tutulmalı. İşleme girmeden önce grafikte kontrol et.
"""


def format_watchlist(candidates):
    text = "📡 <b>Bot çalıştı ama güçlü sinyal eşiği geçilmedi.</b>\n\n"
    text += "En yakın adaylar:\n\n"

    for i, s in enumerate(candidates[:5], 1):
        text += f"{i}) <b>{s['symbol']}</b> | {s['side']} | Skor: {s['score']} | RSI: {s['rsi']} | ADX: {s['adx']}\n"

    text += "\nBu mesaj botun çalıştığını gösterir. Güçlü sinyal çıkınca ayrıca sinyal gönderir."
    return text


def main():
    print("Bot başladı...")

    exchange = get_exchange()

    try:
        symbols = get_top_symbols(exchange)
    except Exception as e:
        print("Parite listesi alınamadı:", e)
        send_telegram("❌ Bot hata verdi: Parite listesi alınamadı.")
        return

    print("Toplam taranan parite:", len(symbols))

    all_candidates = []

    for symbol in symbols:
        df = fetch_df(exchange, symbol)
        if df is None:
            continue

        signal = analyze_symbol(symbol, df)
        if signal:
            all_candidates.append(signal)

        time.sleep(0.2)

    all_candidates = sorted(all_candidates, key=lambda x: x["score"], reverse=True)

    strong_signals = [s for s in all_candidates if s["score"] >= MIN_SCORE]

    print("Aday sayısı:", len(all_candidates))
    print("Güçlü sinyal sayısı:", len(strong_signals))

    if strong_signals:
        message = f"✅ <b>Bot çalıştı.</b>\nToplam taranan parite: {len(symbols)}\nGüçlü sinyal sayısı: {len(strong_signals)}\n"
        send_telegram(message)

        for signal in strong_signals[:MAX_SIGNALS]:
            send_telegram(format_signal(signal))
            time.sleep(1)

    else:
        print("Şu an güçlü sinyal yok.")

        if all_candidates:
            send_telegram(format_watchlist(all_candidates))
        else:
            send_telegram(
                f"📡 Bot çalıştı.\n\nToplam taranan parite: {len(symbols)}\nŞu an uygun aday bulunamadı."
            )


if __name__ == "__main__":
    main()