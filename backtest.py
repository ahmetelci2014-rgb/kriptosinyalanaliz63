import time
import json
from datetime import datetime, timezone

import ccxt
import pandas as pd

from config import COINS, INTERVAL
from strategy import analyze_signal


# =========================
# BACKTEST AYARLARI
# =========================
AUTO_SCAN_ALL_OKX_USDT = True

# İlk deneme için 120 iyi. Sorunsuz çalışırsa None yapıp tüm OKX USDT swap paritelerini test edebilirsin.
# Tüm coinler için: MAX_COINS_BACKTEST = None
MAX_COINS_BACKTEST = 120

BACKTEST_DAYS = 7
TIMEFRAME = INTERVAL
TIMEFRAME_MINUTES = 15

# 7 gün + indikatör ısınması için yeterli mum
FIFTEEN_M_LIMIT = 1000
FOUR_H_LIMIT = 350

HIGHER_TIMEFRAME = "4h"
MIN_SCORE_FOR_REPORT = 0
ONLY_A_QUALITY = True
USE_4H_TREND_FILTER = True

# Aynı coin/yön için çok sık sinyal açılmasını engeller
DUPLICATE_BLOCK_MINUTES = 45

# İşlem en fazla kaç mum takip edilsin? 96 mum = 24 saat
MAX_HOLD_CANDLES = 96

STABLE_BASES = {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDP", "USD"}


# =========================
# OKX / VERİ FONKSİYONLARI
# =========================
def get_exchange():
    return ccxt.okx({
        "enableRateLimit": True,
        "options": {
            "defaultType": "swap"
        }
    })


def to_okx_symbol(symbol):
    base = symbol.replace("USDT", "")
    return f"{base}/USDT:USDT"


def okx_symbol_to_bot_symbol(okx_symbol):
    base = okx_symbol.split("/")[0]
    return f"{base}USDT".upper()


def get_scan_coins(exchange):
    if not AUTO_SCAN_ALL_OKX_USDT:
        return COINS

    try:
        markets = exchange.load_markets()
        auto_coins = []

        for market in markets.values():
            try:
                if not market.get("active", True):
                    continue
                if not market.get("swap", False):
                    continue
                if market.get("quote") != "USDT":
                    continue
                if market.get("settle") != "USDT":
                    continue

                okx_symbol = market.get("symbol")
                if not okx_symbol or "/USDT:USDT" not in okx_symbol:
                    continue

                base = str(market.get("base", "")).upper()
                if not base or base in STABLE_BASES:
                    continue

                coin = okx_symbol_to_bot_symbol(okx_symbol)
                if coin not in auto_coins:
                    auto_coins.append(coin)

            except Exception as market_error:
                print("Market filtreleme hatası:", market_error)

        if not auto_coins:
            print("Otomatik coin bulunamadı, config COINS kullanılacak.")
            return COINS

        priority_coins = [coin for coin in COINS if coin in auto_coins]
        other_coins = sorted([coin for coin in auto_coins if coin not in priority_coins])
        scan_coins = priority_coins + other_coins

        if MAX_COINS_BACKTEST is not None:
            scan_coins = scan_coins[:MAX_COINS_BACKTEST]

        return scan_coins

    except Exception as e:
        print("Otomatik coin çekme hatası:", e)
        return COINS


def fetch_df(exchange, okx_symbol, timeframe, limit):
    try:
        ohlcv = exchange.fetch_ohlcv(okx_symbol, timeframe=timeframe, limit=limit)
        if not ohlcv or len(ohlcv) < 250:
            return None

        df = pd.DataFrame(
            ohlcv,
            columns=["time", "open", "high", "low", "close", "volume"]
        )

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)

        df["time"] = df["time"].astype(int)
        return df

    except Exception as e:
        print(okx_symbol, timeframe, "veri hatası:", e)
        return None


# =========================
# 4H TREND FİLTRESİ
# =========================
def prepare_4h_df(df4):
    df4 = df4.copy()
    df4["ema20"] = df4["close"].ewm(span=20, adjust=False).mean()
    df4["ema50"] = df4["close"].ewm(span=50, adjust=False).mean()
    df4["ema200"] = df4["close"].ewm(span=200, adjust=False).mean()
    df4["ema20_slope"] = df4["ema20"] - df4["ema20"].shift(3)
    df4 = df4.dropna()
    return df4


def get_4h_trend_at_time(df4, signal_time_ms):
    try:
        # Sinyal anında kapanmış son 4H mumu kullanılsın
        four_h_ms = 4 * 60 * 60 * 1000
        usable = df4[df4["time"] <= signal_time_ms - four_h_ms]

        if usable.empty:
            return "NEUTRAL"

        last = usable.iloc[-1]
        close = float(last["close"])
        ema20 = float(last["ema20"])
        ema50 = float(last["ema50"])
        ema200 = float(last["ema200"])
        slope = float(last["ema20_slope"])

        if close > ema200 and ema20 > ema50 and slope > 0:
            return "LONG"

        if close < ema200 and ema20 < ema50 and slope < 0:
            return "SHORT"

        return "NEUTRAL"

    except Exception:
        return "NEUTRAL"


# =========================
# BACKTEST MANTIĞI
# =========================
def is_same_direction_duplicate(symbol, direction, last_signal_times, signal_time_ms):
    key = f"{symbol}_{direction}"
    last_time = last_signal_times.get(key)

    if last_time is None:
        return False

    diff_minutes = (signal_time_ms - last_time) / 1000 / 60
    return diff_minutes < DUPLICATE_BLOCK_MINUTES


def find_trade_result(df15, start_index, direction, entry, tp1, tp2, tp3, sl):
    """
    Sinyal geldikten sonraki mumlarda önce TP1 mi SL mi geldi bakar.
    Aynı mumda hem TP1 hem SL varsa güvenli tarafta kalıp SL sayar.
    """
    max_end = min(len(df15), start_index + MAX_HOLD_CANDLES)

    tp1_hit = False
    tp2_hit = False
    tp3_hit = False

    for i in range(start_index, max_end):
        row = df15.iloc[i]
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        result_time = int(row["time"])

        if direction == "LONG":
            if not tp1_hit:
                # Aynı mumda ikisi de görülürse kötümser sayıyoruz: SL
                if low <= sl:
                    return {
                        "result": "SL",
                        "result_time": result_time,
                        "exit_price": sl,
                        "tp1_hit": False,
                        "tp2_hit": False,
                        "tp3_hit": False,
                    }
                if high >= tp1:
                    tp1_hit = True
            else:
                # TP1 sonrası kalan işlemde SL giriş fiyatıdır
                if high >= tp2:
                    tp2_hit = True
                if high >= tp3:
                    tp3_hit = True
                    return {
                        "result": "TP3",
                        "result_time": result_time,
                        "exit_price": tp3,
                        "tp1_hit": True,
                        "tp2_hit": tp2_hit,
                        "tp3_hit": True,
                    }
                if low <= entry:
                    return {
                        "result": "BE",
                        "result_time": result_time,
                        "exit_price": entry,
                        "tp1_hit": True,
                        "tp2_hit": tp2_hit,
                        "tp3_hit": False,
                    }

        elif direction == "SHORT":
            if not tp1_hit:
                # Aynı mumda ikisi de görülürse kötümser sayıyoruz: SL
                if high >= sl:
                    return {
                        "result": "SL",
                        "result_time": result_time,
                        "exit_price": sl,
                        "tp1_hit": False,
                        "tp2_hit": False,
                        "tp3_hit": False,
                    }
                if low <= tp1:
                    tp1_hit = True
            else:
                # TP1 sonrası kalan işlemde SL giriş fiyatıdır
                if low <= tp2:
                    tp2_hit = True
                if low <= tp3:
                    tp3_hit = True
                    return {
                        "result": "TP3",
                        "result_time": result_time,
                        "exit_price": tp3,
                        "tp1_hit": True,
                        "tp2_hit": tp2_hit,
                        "tp3_hit": True,
                    }
                if high >= entry:
                    return {
                        "result": "BE",
                        "result_time": result_time,
                        "exit_price": entry,
                        "tp1_hit": True,
                        "tp2_hit": tp2_hit,
                        "tp3_hit": False,
                    }

    last_close = float(df15.iloc[max_end - 1]["close"])
    result_time = int(df15.iloc[max_end - 1]["time"])

    return {
        "result": "OPEN_OR_TIMEOUT",
        "result_time": result_time,
        "exit_price": last_close,
        "tp1_hit": tp1_hit,
        "tp2_hit": tp2_hit,
        "tp3_hit": tp3_hit,
    }


def estimate_r_multiple(direction, entry, tp1, sl, result):
    """
    Basit R hesabı:
    - SL = -1R
    - TP1 sonrası girişten kapanırsa yarım pozisyon TP1 aldığı için +0.5R kabul edilir
    - TP2/TP3 sonuçlarında kaba kâr hesabı yapılır
    """
    risk = abs(entry - sl)
    if risk <= 0:
        return 0

    if result["result"] == "SL":
        return -1.0

    if not result.get("tp1_hit"):
        return 0.0

    tp1_r = abs(tp1 - entry) / risk

    if result["result"] == "BE":
        return round(tp1_r * 0.50, 3)

    exit_price = float(result.get("exit_price", entry))
    second_half_r = abs(exit_price - entry) / risk * 0.50
    total_r = (tp1_r * 0.50) + second_half_r
    return round(total_r, 3)


def backtest_coin(exchange, symbol):
    okx_symbol = to_okx_symbol(symbol)
    df15 = fetch_df(exchange, okx_symbol, TIMEFRAME, FIFTEEN_M_LIMIT)
    df4 = fetch_df(exchange, okx_symbol, HIGHER_TIMEFRAME, FOUR_H_LIMIT)

    if df15 is None or df4 is None:
        return []

    df4 = prepare_4h_df(df4)
    if df4.empty:
        return []

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - BACKTEST_DAYS * 24 * 60 * 60 * 1000

    trades = []
    last_signal_times = {}

    # 220 mum ısınma için bırakılır. Sinyal kapanmış mumdan, takip sonraki mumdan başlar.
    for i in range(220, len(df15) - 2):
        signal_time_ms = int(df15.iloc[i - 1]["time"])

        if signal_time_ms < start_ms:
            continue

        test_df = df15.iloc[: i + 1].copy()

        try:
            signal = analyze_signal(symbol, test_df)
        except Exception as e:
            print(symbol, "analyze_signal hatası:", e)
            continue

        if not signal:
            continue

        if ONLY_A_QUALITY and signal.get("quality") != "A":
            continue

        if int(signal.get("score", 0)) < MIN_SCORE_FOR_REPORT:
            continue

        direction = signal["direction"]

        if is_same_direction_duplicate(symbol, direction, last_signal_times, signal_time_ms):
            continue

        if USE_4H_TREND_FILTER:
            trend_4h = get_4h_trend_at_time(df4, signal_time_ms)
            if trend_4h != direction:
                continue
        else:
            trend_4h = "OFF"

        entry = float(signal["entry"])
        tp1 = float(signal["tp1"])
        tp2 = float(signal.get("tp2") or tp1)
        tp3 = float(signal.get("tp3") or tp2)
        sl = float(signal["sl"])

        result = find_trade_result(
            df15=df15,
            start_index=i,
            direction=direction,
            entry=entry,
            tp1=tp1,
            tp2=tp2,
            tp3=tp3,
            sl=sl,
        )

        r_multiple = estimate_r_multiple(direction, entry, tp1, sl, result)

        trades.append({
            "symbol": symbol,
            "direction": direction,
            "quality": signal.get("quality"),
            "score": int(signal.get("score", 0)),
            "trend_4h": trend_4h,
            "entry": entry,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "sl": sl,
            "signal_time": signal_time_ms,
            "result": result["result"],
            "result_time": result["result_time"],
            "tp1_hit": bool(result.get("tp1_hit")),
            "tp2_hit": bool(result.get("tp2_hit")),
            "tp3_hit": bool(result.get("tp3_hit")),
            "r": r_multiple,
        })

        last_signal_times[f"{symbol}_{direction}"] = signal_time_ms

    return trades


# =========================
# RAPOR
# =========================
def ms_to_text(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def build_report(all_trades):
    total = len(all_trades)
    tp1 = sum(1 for t in all_trades if t["tp1_hit"])
    tp2 = sum(1 for t in all_trades if t["tp2_hit"])
    tp3 = sum(1 for t in all_trades if t["tp3_hit"])
    sl = sum(1 for t in all_trades if t["result"] == "SL")
    be = sum(1 for t in all_trades if t["result"] == "BE")
    open_timeout = sum(1 for t in all_trades if t["result"] == "OPEN_OR_TIMEOUT")
    closed = tp1 + sl
    success_rate = round((tp1 / closed) * 100, 2) if closed else 0
    total_r = round(sum(float(t.get("r", 0)) for t in all_trades), 3)
    avg_r = round(total_r / total, 3) if total else 0

    long_trades = [t for t in all_trades if t["direction"] == "LONG"]
    short_trades = [t for t in all_trades if t["direction"] == "SHORT"]

    def side_stats(trades):
        side_total = len(trades)
        side_tp1 = sum(1 for t in trades if t["tp1_hit"])
        side_sl = sum(1 for t in trades if t["result"] == "SL")
        side_closed = side_tp1 + side_sl
        side_rate = round((side_tp1 / side_closed) * 100, 2) if side_closed else 0
        side_r = round(sum(float(t.get("r", 0)) for t in trades), 3)
        return side_total, side_tp1, side_sl, side_rate, side_r

    long_total, long_tp1, long_sl, long_rate, long_r = side_stats(long_trades)
    short_total, short_tp1, short_sl, short_rate, short_r = side_stats(short_trades)

    coin_rows = []
    by_coin = {}
    for trade in all_trades:
        by_coin.setdefault(trade["symbol"], []).append(trade)

    for coin, trades in by_coin.items():
        c_total = len(trades)
        c_tp1 = sum(1 for t in trades if t["tp1_hit"])
        c_sl = sum(1 for t in trades if t["result"] == "SL")
        c_closed = c_tp1 + c_sl
        c_rate = round((c_tp1 / c_closed) * 100, 2) if c_closed else 0
        c_r = round(sum(float(t.get("r", 0)) for t in trades), 3)
        coin_rows.append((coin, c_total, c_tp1, c_sl, c_rate, c_r))

    coin_rows = sorted(coin_rows, key=lambda x: x[5], reverse=True)

    report = []
    report.append("📊 BACKTEST RAPORU")
    report.append(f"Tarih: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report.append(f"Gün: {BACKTEST_DAYS}")
    report.append(f"Timeframe: {TIMEFRAME}")
    report.append(f"4H Trend Filtresi: {USE_4H_TREND_FILTER}")
    report.append(f"Sadece A Kalite: {ONLY_A_QUALITY}")
    report.append(f"Test edilen maksimum coin: {MAX_COINS_BACKTEST}")
    report.append("")
    report.append(f"Toplam işlem: {total}")
    report.append(f"TP1 gelen: {tp1}")
    report.append(f"TP2 gelen: {tp2}")
    report.append(f"TP3 gelen: {tp3}")
    report.append(f"BE / girişten kapanan: {be}")
    report.append(f"SL olan: {sl}")
    report.append(f"Açık/zaman aşımı: {open_timeout}")
    report.append(f"TP1 başarı oranı: %{success_rate}")
    report.append(f"Toplam R: {total_r}")
    report.append(f"İşlem başı ortalama R: {avg_r}")
    report.append("")
    report.append("LONG / SHORT")
    report.append(f"LONG: işlem {long_total}, TP1 {long_tp1}, SL {long_sl}, başarı %{long_rate}, R {long_r}")
    report.append(f"SHORT: işlem {short_total}, TP1 {short_tp1}, SL {short_sl}, başarı %{short_rate}, R {short_r}")
    report.append("")
    report.append("En iyi coinler:")
    for row in coin_rows[:10]:
        report.append(f"{row[0]} | işlem {row[1]} | TP1 {row[2]} | SL {row[3]} | başarı %{row[4]} | R {row[5]}")

    report.append("")
    report.append("En zayıf coinler:")
    for row in sorted(coin_rows, key=lambda x: x[5])[:10]:
        report.append(f"{row[0]} | işlem {row[1]} | TP1 {row[2]} | SL {row[3]} | başarı %{row[4]} | R {row[5]}")

    report.append("")
    report.append("Son 20 işlem:")
    for trade in all_trades[-20:]:
        report.append(
            f"{ms_to_text(trade['signal_time'])} | {trade['symbol']} {trade['direction']} "
            f"| score {trade['score']} | 4H {trade['trend_4h']} | sonuç {trade['result']} | R {trade['r']}"
        )

    return "\n".join(report)


def main():
    print("Backtest başladı...")
    exchange = get_exchange()
    scan_coins = get_scan_coins(exchange)

    print("Test edilecek coin sayısı:", len(scan_coins))
    print("Not: Bu ilk backtest, ana stratejiyi ölçer. Telegram'daki max 3 sıralamasını birebir simüle etmez.")

    all_trades = []

    for index, coin in enumerate(scan_coins, start=1):
        print(f"[{index}/{len(scan_coins)}] {coin} test ediliyor...")
        try:
            trades = backtest_coin(exchange, coin)
            all_trades.extend(trades)
            print(coin, "işlem sayısı:", len(trades))
            time.sleep(0.2)
        except Exception as e:
            print(coin, "backtest hatası:", e)

    all_trades = sorted(all_trades, key=lambda x: x["signal_time"])

    report = build_report(all_trades)
    print("\n" + report)

    with open("backtest_report.txt", "w", encoding="utf-8") as f:
        f.write(report)

    with open("backtest_trades.json", "w", encoding="utf-8") as f:
        json.dump(all_trades, f, indent=2, ensure_ascii=False)

    print("\nDosyalar oluşturuldu:")
    print("- backtest_report.txt")
    print("- backtest_trades.json")
    print("Backtest tamamlandı.")


if __name__ == "__main__":
    main()
