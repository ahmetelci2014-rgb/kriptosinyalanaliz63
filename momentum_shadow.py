"""
Momentum Doğrulama v1 — yalnız gölge ölçüm motoru.

Amaç:
- Ana MTF botunun 5M_RADAR erken işlem sinyallerini sonradan bağımsız
  piyasa verisiyle değerlendirir.
- PASS / CAUTION / WOULD_BLOCK hipotezi üretir.
- Canlı sinyali engellemez, Telegram mesajı göndermez ve emir açmaz.
- Sonuçlar trade_ledger.json üzerinden sonradan eşleştirilir.
- Veriler momentum_shadow.json dosyasına atomik biçimde yazılır.

İlk sürüm yalnız MAIN MTF içindeki 5M_RADAR işlem sinyallerini kapsar.
15M_ENTRY A+ çekirdek yoluna dokunmaz.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import ccxt
except ImportError:  # Self-test ortamında ağ paketi bulunmayabilir.
    ccxt = None

try:
    import pandas as pd
    from ta.momentum import RSIIndicator
    from ta.trend import ADXIndicator, EMAIndicator, MACD
    from ta.volatility import AverageTrueRange
except ImportError:  # Saf self-test için opsiyonel; workflow requirements kurar.
    pd = None
    RSIIndicator = None
    ADXIndicator = None
    EMAIndicator = None
    MACD = None
    AverageTrueRange = None


OPEN_SIGNALS_FILE = "open_signals.json"
TRADE_LEDGER_FILE = "trade_ledger.json"
MOMENTUM_LEDGER_FILE = "momentum_shadow.json"

MOMENTUM_VERSION = "MOMENTUM_SHADOW_V1_2026_08_04"
TRACKED_SOURCE = "5M_RADAR"
MAX_RECORDS = 5000
OHLCV_LIMIT = 260

# Bunlar canlı filtre değildir; yalnız gölge hipotez eşikleridir.
WEAK_ADX_4H = 20.0
WEAK_ADX_1H = 18.0
WEAK_VOLUME_15M = 1.0
CAUTION_ENTRY_DISTANCE = 0.25
SEVERE_ENTRY_DISTANCE = 0.40
MIN_RETEST_TOUCH_TOLERANCE = 0.0025


def now_ts() -> int:
    return int(time.time())


def safe_float(value: Any, default: Optional[float] = 0.0) -> Optional[float]:
    try:
        if value in (None, "", "-"):
            return default
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return default
        return number
    except Exception:
        return default


def normalize_symbol(symbol: Any) -> str:
    value = str(symbol or "").upper().strip()
    return (
        value.replace("/", "")
        .replace(":", "")
        .replace("-", "")
        .replace("_", "")
        .replace(" ", "")
    )


def to_okx_swap_symbol(symbol: str) -> str:
    normalized = normalize_symbol(symbol)
    if normalized.endswith("USDT"):
        base = normalized[:-4]
        if base:
            return f"{base}/USDT:USDT"
    raise ValueError(f"Desteklenmeyen sembol biçimi: {symbol}")


def load_json(filename: str, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    fallback = default if isinstance(default, dict) else {}
    if not filename or not os.path.exists(filename):
        return fallback

    try:
        with open(filename, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else fallback
    except Exception as exc:
        print(filename, "okuma hatası:", exc)
        return fallback


def save_json_atomically(filename: str, data: Dict[str, Any]) -> bool:
    directory = os.path.dirname(os.path.abspath(filename)) or "."
    os.makedirs(directory, exist_ok=True)
    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=directory,
            prefix=f".{os.path.basename(filename)}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        with open(temp_path, "r", encoding="utf-8") as verify:
            verified = json.load(verify)
        if not isinstance(verified, dict):
            raise ValueError("Gölge momentum JSON kökü sözlük değil.")

        os.replace(temp_path, filename)
        temp_path = None
        return True
    except Exception as exc:
        print(filename, "atomik yazma hatası:", exc)
        return False
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


def add_indicators(df: Optional[Any]) -> Optional[Any]:
    if pd is None or RSIIndicator is None:
        raise RuntimeError("pandas/ta paketleri kurulu değil; requirements.txt kurulmalı.")
    if df is None or df.empty:
        return None

    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(set(df.columns)):
        return None

    frame = df.copy()
    for column in required:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    frame = frame.dropna().reset_index(drop=True)
    if len(frame) < 220:
        return None

    frame["rsi"] = RSIIndicator(frame["close"], window=14).rsi()
    frame["ema20"] = EMAIndicator(frame["close"], window=20).ema_indicator()
    frame["ema50"] = EMAIndicator(frame["close"], window=50).ema_indicator()
    frame["ema200"] = EMAIndicator(frame["close"], window=200).ema_indicator()

    macd = MACD(frame["close"])
    frame["macd"] = macd.macd()
    frame["macd_signal"] = macd.macd_signal()
    frame["macd_hist"] = frame["macd"] - frame["macd_signal"]

    frame["adx"] = ADXIndicator(
        frame["high"],
        frame["low"],
        frame["close"],
        window=14,
    ).adx()

    frame["atr"] = AverageTrueRange(
        frame["high"],
        frame["low"],
        frame["close"],
        window=14,
    ).average_true_range()

    frame["volume_avg"] = frame["volume"].rolling(20).mean()
    frame["volume_ratio"] = frame["volume"] / frame["volume_avg"]
    frame = frame.dropna().reset_index(drop=True)

    return frame if len(frame) >= 20 else None


def frame_from_ohlcv(rows: Iterable[Iterable[Any]]) -> Any:
    if pd is None:
        raise RuntimeError("pandas paketi kurulu değil; requirements.txt kurulmalı.")
    return pd.DataFrame(
        list(rows),
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )


def close_power_percent(row: Any) -> float:
    high = safe_float(row.get("high"), 0.0) or 0.0
    low = safe_float(row.get("low"), 0.0) or 0.0
    close = safe_float(row.get("close"), 0.0) or 0.0
    candle_range = high - low
    if candle_range <= 0:
        return 50.0
    return (close - low) / candle_range * 100


def upper_wick_percent(row: Any) -> float:
    high = safe_float(row.get("high"), 0.0) or 0.0
    low = safe_float(row.get("low"), 0.0) or 0.0
    opened = safe_float(row.get("open"), 0.0) or 0.0
    close = safe_float(row.get("close"), 0.0) or 0.0
    candle_range = high - low
    if candle_range <= 0:
        return 0.0
    return max(0.0, (high - max(opened, close)) / candle_range * 100)


def lower_wick_percent(row: Any) -> float:
    high = safe_float(row.get("high"), 0.0) or 0.0
    low = safe_float(row.get("low"), 0.0) or 0.0
    opened = safe_float(row.get("open"), 0.0) or 0.0
    close = safe_float(row.get("close"), 0.0) or 0.0
    candle_range = high - low
    if candle_range <= 0:
        return 0.0
    return max(0.0, (min(opened, close) - low) / candle_range * 100)


def candle_move_percent(row: Any) -> float:
    opened = safe_float(row.get("open"), 0.0) or 0.0
    close = safe_float(row.get("close"), 0.0) or 0.0
    if opened <= 0:
        return 0.0
    return (close - opened) / opened * 100


def slope_percent(frame: Any, bars: int = 3) -> float:
    current = safe_float(frame.iloc[-2].get("ema20"), 0.0) or 0.0
    previous = safe_float(frame.iloc[-2 - bars].get("ema20"), 0.0) or 0.0
    if previous <= 0:
        return 0.0
    return (current - previous) / previous * 100


def macd_direction(frame: Any, direction: str) -> Tuple[bool, float, float]:
    current = safe_float(frame.iloc[-2].get("macd_hist"), 0.0) or 0.0
    previous = safe_float(frame.iloc[-3].get("macd_hist"), 0.0) or 0.0
    if direction == "LONG":
        return current > previous, current, previous
    return current < previous, current, previous


def has_recent_retest(frame: Any, direction: str) -> bool:
    recent = frame.iloc[-6:-1]
    if recent.empty:
        return False

    if direction == "LONG":
        touched = recent["low"] <= recent["ema20"] * (1 + MIN_RETEST_TOUCH_TOLERANCE)
        reclaimed = recent["close"] >= recent["ema20"]
        return bool((touched & reclaimed).any())

    touched = recent["high"] >= recent["ema20"] * (1 - MIN_RETEST_TOUCH_TOLERANCE)
    rejected = recent["close"] <= recent["ema20"]
    return bool((touched & rejected).any())


def extract_feature_snapshot(
    signal: Dict[str, Any],
    frames: Dict[str, Any],
) -> Dict[str, Any]:
    direction = str(signal.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("Sinyal yönü LONG veya SHORT değil.")

    f5 = frames["5m"]
    f15 = frames["15m"]
    f1h = frames["1h"]
    f4h = frames["4h"]

    row5 = f5.iloc[-2]
    row15 = f15.iloc[-2]
    row1h = f1h.iloc[-2]
    row4h = f4h.iloc[-2]

    slope5 = slope_percent(f5)
    slope15 = slope_percent(f15)
    slope5_ok = slope5 > 0 if direction == "LONG" else slope5 < 0
    slope15_ok = slope15 >= 0 if direction == "LONG" else slope15 <= 0

    macd5_ok, macd5, macd5_prev = macd_direction(f5, direction)
    macd15_ok, macd15, macd15_prev = macd_direction(f15, direction)

    close_power = close_power_percent(row5)
    upper_wick = upper_wick_percent(row5)
    lower_wick = lower_wick_percent(row5)

    if direction == "LONG":
        candle_ok = close_power >= 55
        rejection_ok = lower_wick >= 15 or close_power >= 62
    else:
        candle_ok = close_power <= 45
        rejection_ok = upper_wick >= 15 or close_power <= 38

    market_allowed = (
        bool(signal.get("market_guard_long_allowed"))
        if direction == "LONG"
        else bool(signal.get("market_guard_short_allowed"))
    )

    return {
        "direction": direction,
        "adx_4h": round(safe_float(row4h.get("adx"), 0.0) or 0.0, 2),
        "adx_1h": round(safe_float(row1h.get("adx"), 0.0) or 0.0, 2),
        "adx_15m": round(safe_float(row15.get("adx"), 0.0) or 0.0, 2),
        "volume_ratio_15m": round(
            safe_float(row15.get("volume_ratio"), 0.0) or 0.0,
            3,
        ),
        "ema20_slope_5m_percent": round(slope5, 5),
        "ema20_slope_15m_percent": round(slope15, 5),
        "slope_5m_ok": slope5_ok,
        "slope_15m_ok": slope15_ok,
        "macd_hist_5m": round(macd5, 8),
        "macd_hist_5m_previous": round(macd5_prev, 8),
        "macd_5m_ok": macd5_ok,
        "macd_hist_15m": round(macd15, 8),
        "macd_hist_15m_previous": round(macd15_prev, 8),
        "macd_15m_ok": macd15_ok,
        "candle_move_5m_percent": round(candle_move_percent(row5), 4),
        "close_power_5m_percent": round(close_power, 2),
        "upper_wick_5m_percent": round(upper_wick, 2),
        "lower_wick_5m_percent": round(lower_wick, 2),
        "candle_direction_ok": candle_ok,
        "rejection_ok": rejection_ok,
        "recent_retest": has_recent_retest(f5, direction),
        "entry_distance_percent": round(
            safe_float(
                signal.get("entry_distance_at_send_percent")
                if signal.get("entry_distance_at_send_percent") is not None
                else signal.get("zone_distance_percent"),
                0.0,
            )
            or 0.0,
            4,
        ),
        "market_guard_allowed": market_allowed,
        "signal_score": int(safe_float(signal.get("score"), 0) or 0),
        "signal_quality": signal.get("quality"),
        "source": signal.get("source"),
    }


def evaluate_feature_snapshot(features: Dict[str, Any]) -> Dict[str, Any]:
    direction = str(features.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("Momentum değerlendirmesi için geçerli yön gerekli.")

    score = 100
    critical: List[str] = []
    cautions: List[str] = []
    strengths: List[str] = []

    adx4h = safe_float(features.get("adx_4h"), 0.0) or 0.0
    adx1h = safe_float(features.get("adx_1h"), 0.0) or 0.0
    volume15 = safe_float(features.get("volume_ratio_15m"), 0.0) or 0.0
    entry_distance = safe_float(features.get("entry_distance_percent"), 0.0) or 0.0

    if adx4h < WEAK_ADX_4H:
        score -= 8
        cautions.append(f"4H ADX zayıf/sınırda: {adx4h:.2f}")
    else:
        strengths.append(f"4H ADX yeterli: {adx4h:.2f}")

    if adx1h < WEAK_ADX_1H:
        score -= 10
        cautions.append(f"1H ADX zayıf/sınırda: {adx1h:.2f}")
    else:
        strengths.append(f"1H ADX yeterli: {adx1h:.2f}")

    if volume15 < WEAK_VOLUME_15M:
        score -= 10
        cautions.append(f"15M hacim düşük: {volume15:.2f}x")
    else:
        strengths.append(f"15M hacim yeterli: {volume15:.2f}x")

    if entry_distance > SEVERE_ENTRY_DISTANCE:
        score -= 25
        critical.append(f"giriş kayması çok yüksek: %{entry_distance:.3f}")
    elif entry_distance > CAUTION_ENTRY_DISTANCE:
        score -= 10
        cautions.append(f"giriş kayması yüksek: %{entry_distance:.3f}")
    else:
        strengths.append(f"giriş bölgesine yakın: %{entry_distance:.3f}")

    slope5_ok = bool(features.get("slope_5m_ok"))
    slope15_ok = bool(features.get("slope_15m_ok"))
    if not slope5_ok and not slope15_ok:
        score -= 25
        critical.append("5M ve 15M EMA20 eğimi işlem yönüne ters")
    elif not slope5_ok or not slope15_ok:
        score -= 10
        cautions.append("EMA20 eğimlerinden biri işlem yönünü desteklemiyor")
    else:
        strengths.append("5M ve 15M EMA20 eğimi yönü destekliyor")

    macd5_ok = bool(features.get("macd_5m_ok"))
    macd15_ok = bool(features.get("macd_15m_ok"))
    if not macd5_ok and not macd15_ok:
        score -= 22
        critical.append("5M ve 15M MACD histogram momentumu yönün tersine")
    elif not macd5_ok or not macd15_ok:
        score -= 9
        cautions.append("MACD zaman dilimlerinden biri zayıflıyor")
    else:
        strengths.append("5M ve 15M MACD momentumu yönü destekliyor")

    candle_ok = bool(features.get("candle_direction_ok"))
    rejection_ok = bool(features.get("rejection_ok"))
    retest = bool(features.get("recent_retest"))

    if not candle_ok and not rejection_ok:
        score -= 15
        cautions.append("5M kapanış gücü ve reddedilme zayıf")
    elif candle_ok and rejection_ok:
        strengths.append("5M kapanış ve reddedilme güçlü")

    if not retest:
        score -= 8
        cautions.append("yakın 5M retest doğrulaması yok")
    else:
        strengths.append("yakın 5M retest doğrulandı")

    if not bool(features.get("market_guard_allowed")):
        score -= 30
        critical.append("BTC/ETH/SOL piyasa koruması işlem yönünü desteklemiyor")
    else:
        strengths.append("piyasa koruması işlem yönünü destekliyor")

    score = max(0, min(100, int(round(score))))

    # Kazanan profilleri gereksiz engellememek için tek bir zayıflık yeterli değildir.
    would_block = (
        len(critical) >= 2
        or score < 52
        or (
            len(critical) >= 1
            and score < 65
        )
    )

    if would_block:
        decision = "WOULD_BLOCK"
    elif score < 75 or cautions:
        decision = "CAUTION"
    else:
        decision = "PASS"

    return {
        "version": MOMENTUM_VERSION,
        "decision": decision,
        "would_block": would_block,
        "shadow_score": score,
        "critical_reasons": critical,
        "cautions": cautions,
        "strengths": strengths,
    }


def create_exchange():
    if ccxt is None:
        raise RuntimeError("ccxt paketi kurulu değil; requirements.txt kurulmalı.")
    return ccxt.okx({
        "enableRateLimit": True,
        "options": {"defaultType": "swap"},
        "timeout": 20000,
    })


def fetch_frames(exchange: Any, symbol: str) -> Dict[str, Any]:
    okx_symbol = to_okx_swap_symbol(symbol)
    frames: Dict[str, Any] = {}

    for timeframe in ("5m", "15m", "1h", "4h"):
        rows = exchange.fetch_ohlcv(
            okx_symbol,
            timeframe=timeframe,
            limit=OHLCV_LIMIT,
        )
        frame = add_indicators(frame_from_ohlcv(rows))
        if frame is None:
            raise ValueError(f"{symbol} {timeframe} gösterge verisi yetersiz.")
        frames[timeframe] = frame

    return frames


def iter_eligible_signals(open_signals: Dict[str, Any]) -> Iterable[Tuple[str, Dict[str, Any]]]:
    for key, signal in open_signals.items():
        if not isinstance(signal, dict):
            continue
        if bool(signal.get("closed")):
            continue
        if str(signal.get("source") or "").upper() != TRACKED_SOURCE:
            continue
        if str(signal.get("signal_class") or "TRADE").upper() != "TRADE":
            continue
        if not signal.get("symbol") or not signal.get("direction"):
            continue
        yield str(key), signal


def find_trade(
    trade_ledger: Dict[str, Any],
    signal: Dict[str, Any],
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    trades = trade_ledger.get("trades")
    if not isinstance(trades, dict):
        return None, None

    direct_id = str(signal.get("trade_id") or "")
    if direct_id and direct_id in trades and isinstance(trades[direct_id], dict):
        return direct_id, trades[direct_id]

    symbol = normalize_symbol(signal.get("symbol"))
    direction = str(signal.get("direction") or "").upper()
    source = str(signal.get("source") or "").upper()
    opened_at = int(safe_float(signal.get("opened_at"), 0) or 0)

    best: Tuple[Optional[str], Optional[Dict[str, Any]], int] = (None, None, 10**18)

    for trade_id, trade in trades.items():
        if not isinstance(trade, dict):
            continue
        if normalize_symbol(trade.get("symbol")) != symbol:
            continue
        if str(trade.get("direction") or "").upper() != direction:
            continue
        if str(trade.get("source") or "").upper() != source:
            continue

        trade_opened = int(safe_float(trade.get("opened_at"), 0) or 0)
        distance = abs(trade_opened - opened_at) if opened_at and trade_opened else 0
        if distance < best[2]:
            best = (str(trade_id), trade, distance)

    return best[0], best[1]


def classify_outcome(trade: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(trade, dict):
        return {
            "resolved": False,
            "final_result": None,
            "r_result": None,
            "tp1_hit": False,
            "tp2_hit": False,
            "tp3_hit": False,
        }

    final_result = trade.get("final_result")
    status = str(trade.get("status") or "").upper()
    resolved = bool(final_result) or status in {"CLOSED", "FINAL", "EXPIRED"}

    return {
        "resolved": resolved,
        "final_result": final_result,
        "r_result": safe_float(trade.get("r_result"), None),
        "tp1_hit": bool(trade.get("tp1_hit")),
        "tp2_hit": bool(trade.get("tp2_hit")),
        "tp3_hit": bool(trade.get("tp3_hit")),
        "closed_at": trade.get("closed_at"),
    }


def outcome_is_positive(outcome: Dict[str, Any]) -> Optional[bool]:
    if not outcome.get("resolved"):
        return None

    r_result = safe_float(outcome.get("r_result"), None)
    if r_result is not None:
        return r_result > 0

    if outcome.get("tp3_hit") or outcome.get("tp2_hit") or outcome.get("tp1_hit"):
        return True

    final_result = str(outcome.get("final_result") or "").upper()
    if final_result in {"TP1", "TP2", "TP3"}:
        return True
    if final_result == "SL":
        return False
    return None


def build_summary(records: Dict[str, Any]) -> Dict[str, Any]:
    values = [item for item in records.values() if isinstance(item, dict)]
    resolved = [item for item in values if (item.get("outcome") or {}).get("resolved")]

    blocked_winners = 0
    blocked_losers = 0
    passed_winners = 0
    passed_losers = 0

    for item in resolved:
        positive = outcome_is_positive(item.get("outcome") or {})
        if positive is None:
            continue
        blocked = bool((item.get("evaluation") or {}).get("would_block"))
        if blocked and positive:
            blocked_winners += 1
        elif blocked and not positive:
            blocked_losers += 1
        elif not blocked and positive:
            passed_winners += 1
        else:
            passed_losers += 1

    return {
        "total_records": len(values),
        "pass_records": sum(
            1 for item in values
            if (item.get("evaluation") or {}).get("decision") == "PASS"
        ),
        "caution_records": sum(
            1 for item in values
            if (item.get("evaluation") or {}).get("decision") == "CAUTION"
        ),
        "would_block_records": sum(
            1 for item in values
            if (item.get("evaluation") or {}).get("would_block")
        ),
        "resolved_records": len(resolved),
        "blocked_winners": blocked_winners,
        "blocked_losers": blocked_losers,
        "passed_winners": passed_winners,
        "passed_losers": passed_losers,
    }


def empty_ledger() -> Dict[str, Any]:
    return {
        "version": MOMENTUM_VERSION,
        "scope": "MAIN_MTF_5M_RADAR_ONLY",
        "records": {},
        "summary": build_summary({}),
        "last_update": 0,
    }


def run_shadow_cycle(
    exchange: Optional[Any] = None,
    open_signals_file: str = OPEN_SIGNALS_FILE,
    trade_ledger_file: str = TRADE_LEDGER_FILE,
    output_file: str = MOMENTUM_LEDGER_FILE,
) -> Dict[str, Any]:
    open_signals = load_json(open_signals_file, {})
    trade_ledger = load_json(trade_ledger_file, {"trades": {}})
    ledger = load_json(output_file, empty_ledger())

    records = ledger.get("records")
    if not isinstance(records, dict):
        records = {}

    active_exchange = exchange
    created = 0
    updated = 0

    for key, signal in iter_eligible_signals(open_signals):
        trade_id, trade = find_trade(trade_ledger, signal)
        record_id = trade_id or str(signal.get("trade_id") or key)
        if not record_id:
            continue

        if record_id not in records:
            if active_exchange is None:
                active_exchange = create_exchange()

            try:
                frames = fetch_frames(active_exchange, str(signal.get("symbol")))
                features = extract_feature_snapshot(signal, frames)
                evaluation = evaluate_feature_snapshot(features)

                records[record_id] = {
                    "record_id": record_id,
                    "trade_id": trade_id,
                    "symbol": normalize_symbol(signal.get("symbol")),
                    "direction": str(signal.get("direction") or "").upper(),
                    "source": signal.get("source"),
                    "opened_at": signal.get("opened_at"),
                    "entry": safe_float(signal.get("entry"), None),
                    "tp1": safe_float(signal.get("tp1"), None),
                    "tp2": safe_float(signal.get("tp2"), None),
                    "tp3": safe_float(signal.get("tp3"), None),
                    "sl": safe_float(signal.get("sl"), None),
                    "features": features,
                    "evaluation": evaluation,
                    "outcome": classify_outcome(trade),
                    "recorded_at": now_ts(),
                    "outcome_checked_at": now_ts(),
                }
                created += 1
            except Exception as exc:
                print(
                    signal.get("symbol"),
                    "momentum gölge değerlendirme hatası:",
                    exc,
                )
                continue
        else:
            outcome = classify_outcome(trade)
            if records[record_id].get("outcome") != outcome:
                records[record_id]["outcome"] = outcome
                records[record_id]["outcome_checked_at"] = now_ts()
                updated += 1

    # Kapanan işlemler open_signals'dan çıkmış olsa bile ledger sonucunu güncelle.
    for record_id, record in records.items():
        if not isinstance(record, dict):
            continue
        trade_id = str(record.get("trade_id") or record_id)
        trade = (trade_ledger.get("trades") or {}).get(trade_id)
        if not isinstance(trade, dict):
            continue
        outcome = classify_outcome(trade)
        if record.get("outcome") != outcome:
            record["outcome"] = outcome
            record["outcome_checked_at"] = now_ts()
            updated += 1

    if len(records) > MAX_RECORDS:
        ordered = sorted(
            records.items(),
            key=lambda item: int(
                safe_float((item[1] or {}).get("recorded_at"), 0) or 0
            ),
        )
        records = dict(ordered[-MAX_RECORDS:])

    ledger = {
        "version": MOMENTUM_VERSION,
        "scope": "MAIN_MTF_5M_RADAR_ONLY",
        "records": records,
        "summary": build_summary(records),
        "last_update": now_ts(),
        "last_cycle": {
            "created_records": created,
            "updated_outcomes": updated,
        },
    }

    if not save_json_atomically(output_file, ledger):
        raise RuntimeError("momentum_shadow.json kaydedilemedi.")

    print(
        "Momentum Shadow v1:",
        f"yeni={created}",
        f"sonuç_güncelleme={updated}",
        f"toplam={ledger['summary']['total_records']}",
    )
    return ledger


def run_self_test() -> None:
    strong = {
        "direction": "LONG",
        "adx_4h": 28,
        "adx_1h": 24,
        "volume_ratio_15m": 1.6,
        "entry_distance_percent": 0.12,
        "slope_5m_ok": True,
        "slope_15m_ok": True,
        "macd_5m_ok": True,
        "macd_15m_ok": True,
        "candle_direction_ok": True,
        "rejection_ok": True,
        "recent_retest": True,
        "market_guard_allowed": True,
    }
    weak = {
        "direction": "SHORT",
        "adx_4h": 14,
        "adx_1h": 12,
        "volume_ratio_15m": 0.6,
        "entry_distance_percent": 0.48,
        "slope_5m_ok": False,
        "slope_15m_ok": False,
        "macd_5m_ok": False,
        "macd_15m_ok": False,
        "candle_direction_ok": False,
        "rejection_ok": False,
        "recent_retest": False,
        "market_guard_allowed": False,
    }

    strong_result = evaluate_feature_snapshot(strong)
    weak_result = evaluate_feature_snapshot(weak)

    assert strong_result["decision"] == "PASS", strong_result
    assert strong_result["would_block"] is False, strong_result
    assert weak_result["decision"] == "WOULD_BLOCK", weak_result
    assert weak_result["would_block"] is True, weak_result

    print("Momentum Shadow v1 self-test başarılı.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        run_self_test()
        return

    run_shadow_cycle()


if __name__ == "__main__":
    main()
