# position_trend_shadow.py
# Ana Trend Pozisyon Radari V1
#
# Amaç:
# - 1D + 4H ana trend yönünü bulmak
# - 1H geri çekilme / kırılım-retest ile giriş zamanlamak
# - 1-7 gün arası sanal pozisyon taşımak
# - TP1/TP2/TP3 + yapısal stop + 4H trend bozulması ile yönetmek
# - funding / tahmini işlem maliyetini Net R hesabına eklemek
# - open interest verisini teşhis için kaydetmek
#
# GÜVENLİK:
# - Telegram mesajı göndermez
# - Emir açmaz
# - Canlı Premium / Swing / Scalp / Pump filtrelerini değiştirmez
# - Sadece kendi state ve ledger JSON dosyalarını yazar

from __future__ import annotations

import json
import math
import os
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


VERSION = "POSITION_TREND_SHADOW_V1_2026_08_11"
MODE = "SHADOW_ONLY_NO_TELEGRAM_NO_ORDERS_NO_LIVE_FILTER_CHANGE"

STATE_FILE = "position_trend_shadow_state.json"
LEDGER_FILE = "position_trend_shadow_ledger.json"

PRIORITY_COINS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "DOGEUSDT", "LINKUSDT", "AVAXUSDT", "SUIUSDT", "ADAUSDT",
    "LTCUSDT", "DOTUSDT", "APTUSDT", "ARBUSDT", "OPUSDT",
    "NEARUSDT", "INJUSDT", "WLDUSDT", "FILUSDT", "ATOMUSDT",
    "UNIUSDT", "AAVEUSDT", "TRXUSDT", "ETCUSDT", "ICPUSDT",
    "SEIUSDT", "TIAUSDT", "ORDIUSDT", "JUPUSDT", "BCHUSDT",
]

MARKET_REFERENCES = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

MAX_UNIVERSE = 90
MIN_CORRECTED_NOTIONAL_USDT = 5_000_000.0
MAX_NEW_TRADES_PER_RUN = 1
MAX_OPEN_TRADES = 4
SAME_SYMBOL_COOLDOWN_HOURS = 48

MIN_SCORE = 88
MIN_RISK_PERCENT = 1.20
MAX_RISK_PERCENT = 5.50
MAX_HOLD_HOURS = 168

TP1_R = 1.50
TP2_R = 3.00
TP3_R = 5.00

TP1_FRACTION = 0.25
TP2_FRACTION = 0.25
RUNNER_FRACTION = 0.50

# TP1 sonrası kalan pozisyonun stopu -0.25R'a;
# TP2 sonrası +0.50R'a taşınır.
AFTER_TP1_STOP_R = -0.25
AFTER_TP2_STOP_R = 0.50

# Funding, aşırı ters taraftaysa yeni sanal giriş engellenir.
FUNDING_PENALTY_ABS = 0.0005
FUNDING_BLOCK_ABS = 0.0015

# Gerçek kullanıcı fee tier'ı bilinmediği için gölge hesapta
# konservatif tahmini toplam giriş+çıkış maliyeti.
ESTIMATED_ROUND_TRIP_FEE_RATE = 0.0010

D1_LIMIT = 260
H4_LIMIT = 280
H1_LIMIT = 320


def now_ts() -> int:
    return int(time.time())


def utc_text(ts: Optional[int] = None) -> str:
    value = ts if ts is not None else now_ts()
    return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        if not math.isfinite(result):
            return default
        return result
    except Exception:
        return default


def normalize_bot_symbol(symbol: str) -> str:
    value = str(symbol or "").upper().strip()
    value = value.replace("/USDT:USDT", "USDT")
    value = value.replace(":USDT", "")
    value = value.replace("/", "")
    if value and not value.endswith("USDT"):
        value += "USDT"
    return value


def to_okx_symbol(bot_symbol: str) -> str:
    value = normalize_bot_symbol(bot_symbol)
    if not value.endswith("USDT"):
        return value
    base = value[:-4]
    return f"{base}/USDT:USDT"


def corrected_quote_notional_24h(ticker: Dict[str, Any]) -> float:
    """
    OKX linear USDT swap için yaklaşık 24s USDT notional.

    Öncelik:
    raw info.volCcy24h (base coin) * last fiyat.
    Fallback: baseVolume * last.
    Son fallback: unified quoteVolume.
    """
    info = ticker.get("info") or {}
    last = safe_float(ticker.get("last") or info.get("last"))

    raw_base = safe_float(info.get("volCcy24h"))
    if raw_base > 0 and last > 0:
        return raw_base * last

    base_volume = safe_float(ticker.get("baseVolume"))
    if base_volume > 0 and last > 0:
        return base_volume * last

    return safe_float(ticker.get("quoteVolume"))


def atomic_save_json(filename: str, data: Dict[str, Any]) -> None:
    target = os.path.abspath(filename)
    directory = os.path.dirname(target) or "."
    os.makedirs(directory, exist_ok=True)

    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        with open(tmp, "r", encoding="utf-8") as verify:
            loaded = json.load(verify)
            if not isinstance(loaded, dict):
                raise ValueError("JSON doğrulaması başarısız.")

        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass


def empty_state() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "mode": MODE,
        "open_trades": {},
        "last_closed_by_symbol": {},
        "oi_snapshots": {},
        "last_run": None,
        "last_universe": {},
        "run_stats": {},
    }


def empty_ledger() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "mode": MODE,
        "closed_trades": [],
        "summary": {},
        "last_update": None,
    }


def load_json(filename: str, default: Dict[str, Any]) -> Dict[str, Any]:
    try:
        if not os.path.exists(filename):
            return default
        raw = PathLikeRead(filename)
        if not raw.strip():
            return default
        data = json.loads(raw)
        return data if isinstance(data, dict) else default
    except Exception:
        return default


def PathLikeRead(filename: str) -> str:
    with open(filename, "r", encoding="utf-8") as handle:
        return handle.read()


def load_state() -> Dict[str, Any]:
    state = load_json(STATE_FILE, empty_state())
    state["version"] = VERSION
    state["mode"] = MODE
    state.setdefault("open_trades", {})
    state.setdefault("last_closed_by_symbol", {})
    state.setdefault("oi_snapshots", {})
    state.setdefault("last_universe", {})
    state.setdefault("run_stats", {})
    return state


def load_ledger() -> Dict[str, Any]:
    ledger = load_json(LEDGER_FILE, empty_ledger())
    ledger["version"] = VERSION
    ledger["mode"] = MODE
    ledger.setdefault("closed_trades", [])
    ledger.setdefault("summary", {})
    return ledger


def eligible_markets(exchange) -> Tuple[List[str], Dict[str, str]]:
    markets = exchange.load_markets()
    okx_symbols: List[str] = []
    bot_to_okx: Dict[str, str] = {}

    stable_bases = {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDP", "USD"}

    for market in markets.values():
        if not market.get("active", True):
            continue
        if not market.get("swap", False):
            continue
        if str(market.get("quote", "")).upper() != "USDT":
            continue
        if str(market.get("settle", "")).upper() != "USDT":
            continue

        okx_symbol = str(market.get("symbol", ""))
        if "/USDT:USDT" not in okx_symbol:
            continue

        base = str(market.get("base", "")).upper()
        if not base or base in stable_bases:
            continue

        bot = normalize_bot_symbol(okx_symbol)
        if bot in bot_to_okx:
            continue

        bot_to_okx[bot] = okx_symbol
        okx_symbols.append(okx_symbol)

    return okx_symbols, bot_to_okx


def build_universe(exchange) -> Tuple[List[str], Dict[str, Any]]:
    okx_symbols, bot_to_okx = eligible_markets(exchange)
    tickers = exchange.fetch_tickers(okx_symbols)

    rows = []
    for bot, okx_symbol in bot_to_okx.items():
        ticker = tickers.get(okx_symbol, {}) or {}
        notional = corrected_quote_notional_24h(ticker)
        rows.append({
            "symbol": bot,
            "okx_symbol": okx_symbol,
            "notional_usdt": notional,
        })

    rows.sort(key=lambda x: x["notional_usdt"], reverse=True)

    active_set = set(bot_to_okx)
    priority = [s for s in PRIORITY_COINS if s in active_set]
    priority_set = set(priority)

    liquid_others = [
        row["symbol"]
        for row in rows
        if row["symbol"] not in priority_set
        and row["notional_usdt"] >= MIN_CORRECTED_NOTIONAL_USDT
    ]

    universe = (priority + liquid_others)[:MAX_UNIVERSE]
    metadata = {
        "eligible_total": len(rows),
        "priority_active": len(priority),
        "liquid_above_min": sum(
            1 for row in rows
            if row["notional_usdt"] >= MIN_CORRECTED_NOTIONAL_USDT
        ),
        "selected": len(universe),
        "top10": universe[:10],
        "min_notional_usdt": MIN_CORRECTED_NOTIONAL_USDT,
    }
    return universe, metadata


def fetch_df(exchange, bot_symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    rows = exchange.fetch_ohlcv(
        to_okx_symbol(bot_symbol),
        timeframe=timeframe,
        limit=limit,
    )
    if not rows or len(rows) < 60:
        return pd.DataFrame()

    df = pd.DataFrame(
        rows,
        columns=["ts", "open", "high", "low", "close", "volume"],
    )

    # Son mum oluşuyor olabilir; yalnız kapanmış mumlarla karar ver.
    if len(df) > 1:
        df = df.iloc[:-1].copy()

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna().reset_index(drop=True)
    return add_indicators(df)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    out = df.copy()
    close = out["close"]
    high = out["high"]
    low = out["low"]

    out["ema20"] = close.ewm(span=20, adjust=False).mean()
    out["ema50"] = close.ewm(span=50, adjust=False).mean()
    out["ema200"] = close.ewm(span=200, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    out["rsi14"] = (100 - (100 / (1 + rs))).fillna(50.0)

    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    out["atr14"] = tr.ewm(alpha=1 / 14, adjust=False).mean()

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    atr = out["atr14"].replace(0, float("nan"))
    plus_di = 100 * plus_dm.ewm(alpha=1 / 14, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / 14, adjust=False).mean() / atr
    denom = (plus_di + minus_di).replace(0, float("nan"))
    dx = ((plus_di - minus_di).abs() / denom) * 100
    out["adx14"] = dx.ewm(alpha=1 / 14, adjust=False).mean().fillna(0.0)

    out["volume_avg20"] = out["volume"].rolling(20).mean()
    out["volume_ratio"] = (
        out["volume"] / out["volume_avg20"].replace(0, float("nan"))
    ).fillna(0.0)

    return out


def direction_points(df: pd.DataFrame, direction: str) -> int:
    if len(df) < 205:
        return 0

    row = df.iloc[-1]
    slope_now = safe_float(row["ema50"])
    slope_old = safe_float(df.iloc[-6]["ema50"])

    points = 0
    if direction == "LONG":
        points += int(row["close"] > row["ema20"])
        points += int(row["ema20"] > row["ema50"])
        points += int(row["ema50"] > row["ema200"])
        points += int(slope_now > slope_old)
        points += int(50 <= row["rsi14"] <= 72)
    else:
        points += int(row["close"] < row["ema20"])
        points += int(row["ema20"] < row["ema50"])
        points += int(row["ema50"] < row["ema200"])
        points += int(slope_now < slope_old)
        points += int(28 <= row["rsi14"] <= 50)

    return points


def qualified_direction(d1: pd.DataFrame, h4: pd.DataFrame) -> Optional[str]:
    if d1.empty or h4.empty:
        return None

    for direction in ("LONG", "SHORT"):
        d1_points = direction_points(d1, direction)
        h4_points = direction_points(h4, direction)
        h4_adx = safe_float(h4.iloc[-1]["adx14"])

        if d1_points >= 4 and h4_points >= 4 and h4_adx >= 18:
            return direction

    return None


def detect_h1_setup(h1: pd.DataFrame, direction: str) -> Optional[str]:
    if len(h1) < 80:
        return None

    row = h1.iloc[-1]
    atr = max(safe_float(row["atr14"]), 1e-12)
    ema20 = safe_float(row["ema20"])
    ema50 = safe_float(row["ema50"])
    close = safe_float(row["close"])
    open_ = safe_float(row["open"])
    high = safe_float(row["high"])
    low = safe_float(row["low"])
    rsi = safe_float(row["rsi14"])

    if direction == "LONG":
        not_overextended = (close - ema20) <= 1.20 * atr
        pullback = (
            low <= ema20 + 0.35 * atr
            and low >= ema50 - 0.80 * atr
            and close > ema20
            and close > open_
            and 45 <= rsi <= 68
            and not_overextended
        )

        base_res = safe_float(h1.iloc[-30:-6]["high"].max())
        recent_breakout = bool((h1.iloc[-6:-1]["close"] > base_res).any())
        retest = (
            recent_breakout
            and low <= base_res + 0.50 * atr
            and close > base_res
            and close > open_
            and not_overextended
        )
    else:
        not_overextended = (ema20 - close) <= 1.20 * atr
        pullback = (
            high >= ema20 - 0.35 * atr
            and high <= ema50 + 0.80 * atr
            and close < ema20
            and close < open_
            and 32 <= rsi <= 55
            and not_overextended
        )

        base_sup = safe_float(h1.iloc[-30:-6]["low"].min())
        recent_breakout = bool((h1.iloc[-6:-1]["close"] < base_sup).any())
        retest = (
            recent_breakout
            and high >= base_sup - 0.50 * atr
            and close < base_sup
            and close < open_
            and not_overextended
        )

    if pullback and retest:
        return "PULLBACK_PLUS_RETEST"
    if pullback:
        return "PULLBACK_RECLAIM"
    if retest:
        return "BREAKOUT_RETEST"
    return None


def market_vote_from_frames(d1: pd.DataFrame, h4: pd.DataFrame) -> str:
    direction = qualified_direction(d1, h4)
    return direction or "NEUTRAL"


def get_market_regime(exchange) -> Dict[str, Any]:
    votes = []
    detail = {}

    for symbol in MARKET_REFERENCES:
        try:
            d1 = fetch_df(exchange, symbol, "1d", D1_LIMIT)
            h4 = fetch_df(exchange, symbol, "4h", H4_LIMIT)
            vote = market_vote_from_frames(d1, h4)
        except Exception:
            vote = "NEUTRAL"

        votes.append(vote)
        detail[symbol] = vote

    long_votes = votes.count("LONG")
    short_votes = votes.count("SHORT")

    if long_votes >= 2:
        regime = "LONG"
    elif short_votes >= 2:
        regime = "SHORT"
    else:
        regime = "NEUTRAL"

    return {
        "regime": regime,
        "long_votes": long_votes,
        "short_votes": short_votes,
        "detail": detail,
    }


def fetch_funding(exchange, bot_symbol: str) -> Dict[str, Any]:
    try:
        data = exchange.fetch_funding_rate(to_okx_symbol(bot_symbol)) or {}
        return {
            "rate": safe_float(data.get("fundingRate")),
            "timestamp": int(data.get("fundingTimestamp") or 0),
            "next_timestamp": int(data.get("nextFundingTimestamp") or 0),
        }
    except Exception:
        return {"rate": 0.0, "timestamp": 0, "next_timestamp": 0}


def fetch_open_interest(exchange, bot_symbol: str) -> Dict[str, Any]:
    try:
        data = exchange.fetch_open_interest(to_okx_symbol(bot_symbol)) or {}
        return {
            "oi_usd": safe_float(
                data.get("openInterestValue")
                or (data.get("info") or {}).get("oiUsd")
            ),
            "timestamp": int(data.get("timestamp") or 0),
        }
    except Exception:
        return {"oi_usd": 0.0, "timestamp": 0}


def funding_effect(direction: str, rate: float) -> Tuple[bool, int]:
    """
    Returns: (blocked, score_points 0..5)
    Long için pozitif funding ters maliyet; short için negatif funding ters maliyet.
    """
    adverse = rate if direction == "LONG" else -rate

    if adverse >= FUNDING_BLOCK_ABS:
        return True, 0
    if adverse >= FUNDING_PENALTY_ABS:
        return False, 1
    if adverse > 0:
        return False, 3
    return False, 5


def structural_levels(h1: pd.DataFrame, direction: str) -> Optional[Dict[str, float]]:
    row = h1.iloc[-1]
    entry = safe_float(row["close"])
    atr = max(safe_float(row["atr14"]), 1e-12)

    if direction == "LONG":
        swing = safe_float(h1.iloc[-14:]["low"].min())
        stop = swing - 0.25 * atr
        risk = entry - stop
    else:
        swing = safe_float(h1.iloc[-14:]["high"].max())
        stop = swing + 0.25 * atr
        risk = stop - entry

    if entry <= 0 or risk <= 0:
        return None

    risk_percent = (risk / entry) * 100
    if not (MIN_RISK_PERCENT <= risk_percent <= MAX_RISK_PERCENT):
        return None

    if direction == "LONG":
        tp1 = entry + TP1_R * risk
        tp2 = entry + TP2_R * risk
        tp3 = entry + TP3_R * risk
    else:
        tp1 = entry - TP1_R * risk
        tp2 = entry - TP2_R * risk
        tp3 = entry - TP3_R * risk

    return {
        "entry": entry,
        "stop": stop,
        "risk": risk,
        "risk_percent": risk_percent,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
    }


def score_candidate(
    d1: pd.DataFrame,
    h4: pd.DataFrame,
    h1: pd.DataFrame,
    direction: str,
    setup_type: str,
    market_regime: str,
    funding_points: int,
) -> int:
    score = 0
    score += direction_points(d1, direction) * 5       # max 25
    score += direction_points(h4, direction) * 5       # max 25

    h4_adx = safe_float(h4.iloc[-1]["adx14"])
    if h4_adx >= 28:
        score += 10
    elif h4_adx >= 23:
        score += 8
    elif h4_adx >= 18:
        score += 5

    score += 20 if setup_type == "PULLBACK_PLUS_RETEST" else 16

    vol_ratio = safe_float(h1.iloc[-1]["volume_ratio"])
    if vol_ratio >= 1.20:
        score += 8
    elif vol_ratio >= 0.90:
        score += 5
    elif vol_ratio >= 0.70:
        score += 2

    h1_adx = safe_float(h1.iloc[-1]["adx14"])
    if h1_adx >= 22:
        score += 5
    elif h1_adx >= 18:
        score += 3

    if market_regime == direction:
        score += 7
    elif market_regime == "NEUTRAL":
        score += 3

    score += int(funding_points)
    return min(score, 100)


def analyze_symbol(exchange, bot_symbol: str, market_regime: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
    try:
        d1 = fetch_df(exchange, bot_symbol, "1d", D1_LIMIT)
        if d1.empty:
            return None, "NO_D1_HISTORY"

        # 1D tek başına yön üretmiyorsa 4H/1H isteklerini boşa harcama.
        d1_long = direction_points(d1, "LONG")
        d1_short = direction_points(d1, "SHORT")
        if max(d1_long, d1_short) < 4:
            return None, "NO_D1_TREND"

        h4 = fetch_df(exchange, bot_symbol, "4h", H4_LIMIT)
        if h4.empty:
            return None, "NO_H4_HISTORY"

        direction = qualified_direction(d1, h4)
        if not direction:
            return None, "NO_D1_H4_ALIGNMENT"

        regime = str(market_regime.get("regime", "NEUTRAL"))
        if regime not in ("NEUTRAL", direction):
            return None, "MARKET_REGIME_OPPOSITE"

        h1 = fetch_df(exchange, bot_symbol, "1h", H1_LIMIT)
        if h1.empty:
            return None, "NO_H1_HISTORY"

        setup_type = detect_h1_setup(h1, direction)
        if not setup_type:
            return None, "NO_H1_ENTRY_SETUP"

        funding = fetch_funding(exchange, bot_symbol)
        funding_block, funding_points = funding_effect(
            direction,
            safe_float(funding.get("rate")),
        )
        if funding_block:
            return None, "EXTREME_ADVERSE_FUNDING"

        levels = structural_levels(h1, direction)
        if not levels:
            return None, "STRUCTURAL_RISK_OUT_OF_RANGE"

        score = score_candidate(
            d1, h4, h1, direction, setup_type, regime, funding_points
        )
        if score < MIN_SCORE:
            return None, "SCORE_BELOW_MIN"

        oi = fetch_open_interest(exchange, bot_symbol)

        signal = {
            "version": VERSION,
            "symbol": bot_symbol,
            "direction": direction,
            "setup_type": setup_type,
            "score": score,
            **levels,
            "market_regime": regime,
            "market_detail": market_regime.get("detail", {}),
            "funding_rate_entry": safe_float(funding.get("rate")),
            "funding_next_timestamp": int(funding.get("next_timestamp") or 0),
            "oi_usd_entry": safe_float(oi.get("oi_usd")),
            "d1_rsi": safe_float(d1.iloc[-1]["rsi14"]),
            "d1_adx": safe_float(d1.iloc[-1]["adx14"]),
            "h4_rsi": safe_float(h4.iloc[-1]["rsi14"]),
            "h4_adx": safe_float(h4.iloc[-1]["adx14"]),
            "h1_rsi": safe_float(h1.iloc[-1]["rsi14"]),
            "h1_adx": safe_float(h1.iloc[-1]["adx14"]),
            "h1_volume_ratio": safe_float(h1.iloc[-1]["volume_ratio"]),
            "entry_candle_ts": int(h1.iloc[-1]["ts"]),
            "created_at": now_ts(),
            "created_at_text": utc_text(),
        }
        return signal, "TRADE_SETUP"

    except Exception as exc:
        return None, f"ERROR:{type(exc).__name__}"


def make_trade_id(signal: Dict[str, Any]) -> str:
    return (
        f"{signal['symbol']}_{signal['direction']}_"
        f"{int(signal['created_at'])}"
    )


def open_shadow_trade(state: Dict[str, Any], signal: Dict[str, Any]) -> str:
    trade_id = make_trade_id(signal)
    trade = dict(signal)
    trade.update({
        "trade_id": trade_id,
        "opened_at": int(signal["created_at"]),
        # İşlemden önceki geçmiş 1H mumları sonradan TP/SL sayılmasın.
        "last_checked_candle_ts": int(
            signal.get("entry_candle_ts")
            or (int(signal["created_at"]) * 1000)
        ),
        "active_stop": safe_float(signal["stop"]),
        "remaining_fraction": 1.0,
        "realized_price_r": 0.0,
        "tp1_hit": False,
        "tp2_hit": False,
        "tp3_hit": False,
        "best_r": 0.0,
        "worst_r": 0.0,
        "closed": False,
    })
    state.setdefault("open_trades", {})[trade_id] = trade
    return trade_id


def price_to_r(trade: Dict[str, Any], price: float) -> float:
    entry = safe_float(trade["entry"])
    risk = safe_float(trade["risk"])
    if risk <= 0:
        return 0.0
    if trade["direction"] == "LONG":
        return (price - entry) / risk
    return (entry - price) / risk


def stop_price_from_r(trade: Dict[str, Any], stop_r: float) -> float:
    entry = safe_float(trade["entry"])
    risk = safe_float(trade["risk"])
    if trade["direction"] == "LONG":
        return entry + stop_r * risk
    return entry - stop_r * risk


def close_at_stop(trade: Dict[str, Any], reason: str) -> Dict[str, Any]:
    stop_r = price_to_r(trade, safe_float(trade["active_stop"]))
    trade["realized_price_r"] += trade["remaining_fraction"] * stop_r
    trade["remaining_fraction"] = 0.0
    trade["closed"] = True
    trade["close_reason"] = reason
    trade["closed_at"] = now_ts()
    return trade


def close_at_market(trade: Dict[str, Any], price: float, reason: str) -> Dict[str, Any]:
    current_r = price_to_r(trade, price)
    trade["realized_price_r"] += trade["remaining_fraction"] * current_r
    trade["remaining_fraction"] = 0.0
    trade["closed"] = True
    trade["close_reason"] = reason
    trade["close_price"] = price
    trade["closed_at"] = now_ts()
    return trade


def process_bar(trade: Dict[str, Any], bar: Dict[str, float]) -> Dict[str, Any]:
    """
    Aynı mumda hem stop hem yeni TP görülürse konservatif olarak STOP önce kabul edilir.
    Bu, gölge performansı yapay biçimde şişirmemek içindir.
    """
    high = safe_float(bar["high"])
    low = safe_float(bar["low"])

    high_r = price_to_r(trade, high)
    low_r = price_to_r(trade, low)
    trade["best_r"] = max(safe_float(trade.get("best_r")), high_r, low_r)
    trade["worst_r"] = min(safe_float(trade.get("worst_r")), high_r, low_r)

    direction = trade["direction"]
    active_stop = safe_float(trade["active_stop"])
    stop_hit = low <= active_stop if direction == "LONG" else high >= active_stop

    next_tp = None
    next_tp_name = None
    if not trade.get("tp1_hit"):
        next_tp = safe_float(trade["tp1"])
        next_tp_name = "TP1"
    elif not trade.get("tp2_hit"):
        next_tp = safe_float(trade["tp2"])
        next_tp_name = "TP2"
    elif not trade.get("tp3_hit"):
        next_tp = safe_float(trade["tp3"])
        next_tp_name = "TP3"

    tp_hit = False
    if next_tp is not None:
        tp_hit = high >= next_tp if direction == "LONG" else low <= next_tp

    if stop_hit and tp_hit:
        return close_at_stop(trade, "AMBIGUOUS_BAR_STOP_FIRST")

    if stop_hit:
        stage = "INITIAL_SL"
        if trade.get("tp2_hit"):
            stage = "AFTER_TP2_TRAIL"
        elif trade.get("tp1_hit"):
            stage = "AFTER_TP1_PROTECT"
        return close_at_stop(trade, stage)

    if tp_hit and next_tp_name == "TP1":
        trade["tp1_hit"] = True
        trade["realized_price_r"] += TP1_FRACTION * TP1_R
        trade["remaining_fraction"] = 1.0 - TP1_FRACTION
        trade["active_stop"] = stop_price_from_r(trade, AFTER_TP1_STOP_R)

    elif tp_hit and next_tp_name == "TP2":
        trade["tp2_hit"] = True
        trade["realized_price_r"] += TP2_FRACTION * TP2_R
        trade["remaining_fraction"] = RUNNER_FRACTION
        trade["active_stop"] = stop_price_from_r(trade, AFTER_TP2_STOP_R)

    elif tp_hit and next_tp_name == "TP3":
        trade["tp3_hit"] = True
        trade["realized_price_r"] += RUNNER_FRACTION * TP3_R
        trade["remaining_fraction"] = 0.0
        trade["closed"] = True
        trade["close_reason"] = "TP3"
        trade["closed_at"] = now_ts()

    return trade


def funding_r_for_closed_trade(exchange, trade: Dict[str, Any]) -> Tuple[float, bool]:
    """
    Kapanışta mümkünse funding geçmişini çekip R etkisini hesaplar.
    Pozitif değer kazanç, negatif değer maliyet.
    """
    try:
        since_ms = int(trade["opened_at"]) * 1000
        history = exchange.fetch_funding_rate_history(
            to_okx_symbol(trade["symbol"]),
            since=since_ms,
            limit=100,
        ) or []

        signed_cost_rate = 0.0
        for item in history:
            ts = int(item.get("timestamp") or 0)
            if ts and ts > int(trade.get("closed_at", now_ts())) * 1000:
                continue
            rate = safe_float(item.get("fundingRate"))
            signed_cost_rate += rate if trade["direction"] == "LONG" else -rate

        risk_decimal = safe_float(trade["risk_percent"]) / 100.0
        if risk_decimal <= 0:
            return 0.0, False

        return -(signed_cost_rate / risk_decimal), True
    except Exception:
        return 0.0, False


def finalize_closed_trade(exchange, trade: Dict[str, Any]) -> Dict[str, Any]:
    price_r = safe_float(trade.get("realized_price_r"))
    risk_decimal = safe_float(trade.get("risk_percent")) / 100.0

    fee_r = 0.0
    if risk_decimal > 0:
        fee_r = -(ESTIMATED_ROUND_TRIP_FEE_RATE / risk_decimal)

    funding_r, funding_exact = funding_r_for_closed_trade(exchange, trade)

    trade["price_net_r"] = round(price_r, 6)
    trade["estimated_fee_r"] = round(fee_r, 6)
    trade["funding_r"] = round(funding_r, 6)
    trade["funding_exact"] = bool(funding_exact)
    trade["net_r_after_costs"] = round(price_r + fee_r + funding_r, 6)
    trade["hold_hours"] = round(
        max(0, int(trade.get("closed_at", now_ts())) - int(trade["opened_at"])) / 3600,
        2,
    )
    trade["closed_at_text"] = utc_text(int(trade.get("closed_at", now_ts())))
    return trade


def trend_broken(exchange, trade: Dict[str, Any]) -> Tuple[bool, float]:
    try:
        h4 = fetch_df(exchange, trade["symbol"], "4h", H4_LIMIT)
        if h4.empty:
            return False, 0.0
        row = h4.iloc[-1]
        price = safe_float(row["close"])

        if trade["direction"] == "LONG":
            broken = row["close"] < row["ema50"] and row["ema20"] < row["ema50"]
        else:
            broken = row["close"] > row["ema50"] and row["ema20"] > row["ema50"]

        return bool(broken), price
    except Exception:
        return False, 0.0


def manage_open_trades(exchange, state: Dict[str, Any], ledger: Dict[str, Any]) -> None:
    open_trades = state.setdefault("open_trades", {})
    closed_ids = []

    for trade_id, trade in list(open_trades.items()):
        try:
            h1 = fetch_df(exchange, trade["symbol"], "1h", 220)
            if h1.empty:
                continue

            last_seen = int(trade.get("last_checked_candle_ts") or 0)
            for _, row in h1.iterrows():
                candle_ts = int(row["ts"])
                if candle_ts <= last_seen:
                    continue

                trade = process_bar(trade, {
                    "high": safe_float(row["high"]),
                    "low": safe_float(row["low"]),
                })
                trade["last_checked_candle_ts"] = candle_ts

                if trade.get("closed"):
                    break

            if not trade.get("closed"):
                held_hours = (now_ts() - int(trade["opened_at"])) / 3600
                if held_hours >= MAX_HOLD_HOURS:
                    price = safe_float(h1.iloc[-1]["close"])
                    trade = close_at_market(trade, price, "MAX_HOLD_7D")
                else:
                    broken, price = trend_broken(exchange, trade)
                    if broken and price > 0:
                        trade = close_at_market(trade, price, "H4_TREND_BREAK")

            open_trades[trade_id] = trade

            if trade.get("closed"):
                trade = finalize_closed_trade(exchange, trade)
                ledger.setdefault("closed_trades", []).append(trade)
                state.setdefault("last_closed_by_symbol", {})[
                    trade["symbol"]
                ] = int(trade.get("closed_at", now_ts()))
                closed_ids.append(trade_id)

        except Exception as exc:
            print("Açık pozisyon takip hatası:", trade_id, type(exc).__name__)

    for trade_id in closed_ids:
        open_trades.pop(trade_id, None)


def rebuild_summary(ledger: Dict[str, Any]) -> Dict[str, Any]:
    records = ledger.get("closed_trades", [])
    net_values = [safe_float(r.get("net_r_after_costs")) for r in records]
    raw_values = [safe_float(r.get("price_net_r")) for r in records]

    wins = sum(1 for x in net_values if x > 0)
    losses = sum(1 for x in net_values if x < 0)
    neutral = len(net_values) - wins - losses

    gross_profit = sum(x for x in net_values if x > 0)
    gross_loss = abs(sum(x for x in net_values if x < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None

    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for value in net_values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)

    summary = {
        "total_closed": len(records),
        "wins": wins,
        "losses": losses,
        "neutral": neutral,
        "win_rate_percent": round((wins / len(records) * 100), 2) if records else 0.0,
        "raw_price_net_r": round(sum(raw_values), 6),
        "net_r_after_costs": round(sum(net_values), 6),
        "avg_r_after_costs": round(sum(net_values) / len(records), 6) if records else 0.0,
        "profit_factor": round(profit_factor, 4) if profit_factor is not None else None,
        "max_drawdown_r": round(max_drawdown, 6),
        "tp3": sum(1 for r in records if r.get("close_reason") == "TP3"),
        "initial_sl": sum(1 for r in records if r.get("close_reason") == "INITIAL_SL"),
        "after_tp1_protect": sum(1 for r in records if r.get("close_reason") == "AFTER_TP1_PROTECT"),
        "after_tp2_trail": sum(1 for r in records if r.get("close_reason") == "AFTER_TP2_TRAIL"),
        "h4_trend_break": sum(1 for r in records if r.get("close_reason") == "H4_TREND_BREAK"),
        "max_hold_7d": sum(1 for r in records if r.get("close_reason") == "MAX_HOLD_7D"),
        "avg_hold_hours": round(
            sum(safe_float(r.get("hold_hours")) for r in records) / len(records),
            2,
        ) if records else 0.0,
        "funding_exact_count": sum(1 for r in records if r.get("funding_exact")),
    }
    ledger["summary"] = summary
    ledger["last_update"] = utc_text()
    return summary


def cooldown_ok(state: Dict[str, Any], symbol: str) -> bool:
    last_closed = int(state.get("last_closed_by_symbol", {}).get(symbol, 0) or 0)
    return (now_ts() - last_closed) >= SAME_SYMBOL_COOLDOWN_HOURS * 3600


def run() -> None:
    # ccxt yalnız gerçek runtime'da import edilir; saf testler bağımlı değildir.
    import ccxt

    exchange = ccxt.okx({
        "enableRateLimit": True,
        "timeout": 20000,
    })
    exchange.options["defaultType"] = "swap"

    state = load_state()
    ledger = load_ledger()

    print("=== ANA TREND POZISYON RADARI ===")
    print("Version:", VERSION)
    print("Mode:", MODE)

    manage_open_trades(exchange, state, ledger)

    universe, universe_meta = build_universe(exchange)
    market_regime = get_market_regime(exchange)

    reasons: Dict[str, int] = {}
    candidates: List[Dict[str, Any]] = []

    open_symbols = {
        trade.get("symbol")
        for trade in state.get("open_trades", {}).values()
    }

    if len(state.get("open_trades", {})) < MAX_OPEN_TRADES:
        for symbol in universe:
            if symbol in open_symbols:
                continue
            if not cooldown_ok(state, symbol):
                reasons["SYMBOL_COOLDOWN"] = reasons.get("SYMBOL_COOLDOWN", 0) + 1
                continue

            signal, reason = analyze_symbol(exchange, symbol, market_regime)
            reasons[reason] = reasons.get(reason, 0) + 1
            if signal:
                candidates.append(signal)

    candidates.sort(
        key=lambda x: (
            safe_float(x.get("score")),
            -safe_float(x.get("risk_percent")),
        ),
        reverse=True,
    )

    slots = max(0, MAX_OPEN_TRADES - len(state.get("open_trades", {})))
    to_open = candidates[: min(MAX_NEW_TRADES_PER_RUN, slots)]

    for signal in to_open:
        trade_id = open_shadow_trade(state, signal)
        print(
            "SANAL POZISYON:",
            trade_id,
            signal["setup_type"],
            "score",
            signal["score"],
            "risk%",
            round(signal["risk_percent"], 3),
        )

    summary = rebuild_summary(ledger)

    state["last_run"] = utc_text()
    state["last_universe"] = {
        **universe_meta,
        "market_regime": market_regime,
    }
    state["run_stats"] = {
        "scanned": len(universe),
        "candidate_count": len(candidates),
        "opened_this_run": len(to_open),
        "open_trades": len(state.get("open_trades", {})),
        "reason_counts": dict(sorted(reasons.items())),
    }

    atomic_save_json(STATE_FILE, state)
    atomic_save_json(LEDGER_FILE, ledger)

    print("Universe:", len(universe))
    print("Market regime:", market_regime)
    print("Candidates:", len(candidates))
    print("Opened:", len(to_open))
    print("Open:", len(state.get("open_trades", {})))
    print("Closed:", summary["total_closed"])
    print("Net R after costs:", summary["net_r_after_costs"])


if __name__ == "__main__":
    run()
