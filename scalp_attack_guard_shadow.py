"""ATAK_SCALP 62/38 -> 70/30 counterfactual gölge ölçümü.

Yalnız eski 62/38 kapanış gücü eşiğiyle sinyal olacakken canlı 70/30
eşiği nedeniyle elenen ATAK adaylarını izler. Telegram göndermez, gerçek açık
işlem sayısını ve portföy risk limitini etkilemez, canlı kurala otomatik yazmaz.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import scalp_quality_config as cfg

TR_TIMEZONE = timezone(timedelta(hours=3))


def now_ts() -> int:
    return int(time.time())


def tr_text(ts: int | None = None) -> str:
    value = int(ts if ts is not None else now_ts())
    return datetime.fromtimestamp(value, TR_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def normalize_symbol(symbol: Any) -> str:
    value = str(symbol or "").upper().strip()
    value = value.replace("/USDT:USDT", "USDT").replace(":USDT", "").replace("/", "")
    if value and not value.endswith("USDT"):
        value += "USDT"
    return value


def to_okx_symbol(symbol: Any) -> str:
    bot_symbol = normalize_symbol(symbol)
    base = bot_symbol[:-4] if bot_symbol.endswith("USDT") else bot_symbol
    return f"{base}/USDT:USDT"


def empty_shadow() -> dict[str, Any]:
    return {
        "version": cfg.SHADOW_VERSION,
        "mode": cfg.SHADOW_MODE,
        "auto_apply": False,
        "legacy_thresholds": {
            "long_min_close_power": cfg.LEGACY_ATTACK_LONG_MIN_CLOSE_POWER,
            "short_max_close_power": cfg.LEGACY_ATTACK_SHORT_MAX_CLOSE_POWER,
        },
        "live_thresholds": {
            "long_min_close_power": cfg.LIVE_ATTACK_LONG_MIN_CLOSE_POWER,
            "short_max_close_power": cfg.LIVE_ATTACK_SHORT_MAX_CLOSE_POWER,
        },
        "records": [],
        "summary": {},
    }


def load_shadow(filename: str | None = None) -> dict[str, Any]:
    path = Path(filename or cfg.SHADOW_FILE)
    try:
        if not path.exists():
            return empty_shadow()
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return empty_shadow()
    except Exception as exc:
        print("ATAK guard shadow okuma hatası:", type(exc).__name__)
        return empty_shadow()

    data.setdefault("records", [])
    data.setdefault("summary", {})
    data["version"] = cfg.SHADOW_VERSION
    data["mode"] = cfg.SHADOW_MODE
    data["auto_apply"] = False
    data["legacy_thresholds"] = {
        "long_min_close_power": cfg.LEGACY_ATTACK_LONG_MIN_CLOSE_POWER,
        "short_max_close_power": cfg.LEGACY_ATTACK_SHORT_MAX_CLOSE_POWER,
    }
    data["live_thresholds"] = {
        "long_min_close_power": cfg.LIVE_ATTACK_LONG_MIN_CLOSE_POWER,
        "short_max_close_power": cfg.LIVE_ATTACK_SHORT_MAX_CLOSE_POWER,
    }
    return data


def atomic_save_json(filename: str, data: dict[str, Any]) -> None:
    target = Path(filename).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        json.loads(tmp_path.read_text(encoding="utf-8"))
        os.replace(tmp_path, target)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _rate(count: int, sample: int) -> float:
    return round((count / sample) * 100.0, 2) if sample else 0.0


def rebuild_summary(data: dict[str, Any]) -> None:
    records = [row for row in data.get("records", []) if isinstance(row, dict)]
    resolved = [row for row in records if str(row.get("outcome", "OPEN")).upper() != "OPEN"]
    sample = len(resolved)
    tp1 = sum(1 for row in resolved if bool(row.get("tp1_hit")))
    tp3 = sum(1 for row in resolved if bool(row.get("tp3_hit")))
    stop = sum(1 for row in resolved if row.get("outcome") == "STOP_BEFORE_TP1")
    tp1_only = sum(1 for row in resolved if row.get("outcome") == "TP1_ONLY")
    expired = sum(1 for row in resolved if row.get("outcome") == "EXPIRED_NO_TP1")

    tp1_rate = _rate(tp1, sample)
    stop_rate = _rate(stop, sample)
    if sample < cfg.SHADOW_MIN_RESOLVED_SAMPLE:
        decision = "VERI_TOPLA"
        next_action = "En az 30 sonuçlanmış legacy-only ATAK adayı biriktir; canlı filtreyi otomatik değiştirme."
    elif tp1_rate >= 60.0:
        decision = "70_30_COK_SIKI_OLABILIR_GOLGE_INCELE"
        next_action = "Kaçırılan TP1/TP3 oranı yüksek; yalnız gölgede ara eşik testi değerlendir."
    elif stop_rate >= 50.0 and tp1_rate <= 40.0:
        decision = "70_30_GUARD_DESTEKLENIYOR"
        next_action = "Legacy-only adayların çoğu TP1 öncesi stop; 70/30 guardını koru ve örneği büyüt."
    else:
        decision = "KARSILASTIR"
        next_action = "TP1/TP3 ve TP1-öncesi stop dağılımını büyüt; canlı eşik değişikliği yapma."

    by_direction: dict[str, Any] = {}
    for direction in ("LONG", "SHORT"):
        subset = [row for row in resolved if str(row.get("direction", "")).upper() == direction]
        by_direction[direction] = {
            "sample": len(subset),
            "tp1_before_stop": sum(1 for row in subset if row.get("tp1_hit")),
            "tp3": sum(1 for row in subset if row.get("tp3_hit")),
            "stop_before_tp1": sum(1 for row in subset if row.get("outcome") == "STOP_BEFORE_TP1"),
        }

    data["summary"] = {
        "total_candidates": len(records),
        "open": len(records) - sample,
        "resolved": sample,
        "tp1_before_stop": tp1,
        "tp1_before_stop_rate_percent": tp1_rate,
        "tp3": tp3,
        "tp3_rate_percent": _rate(tp3, sample),
        "stop_before_tp1": stop,
        "stop_before_tp1_rate_percent": stop_rate,
        "tp1_only": tp1_only,
        "expired_no_tp1": expired,
        "by_direction": by_direction,
        "decision": decision,
        "next_action": next_action,
        "minimum_decision_sample": cfg.SHADOW_MIN_RESOLVED_SAMPLE,
        "auto_apply": False,
        "method": "Eski 62/38'de canlı sinyal olacak, yeni 70/30 kapanış gücü guardında elenecek ATAK adaylarında TP1/TP3 ve TP1-öncesi SL sırası izlenir.",
    }
    data["updated_at"] = now_ts()
    data["updated_at_tr"] = tr_text(data["updated_at"])


def save_shadow(data: dict[str, Any], filename: str | None = None) -> None:
    rebuild_summary(data)
    atomic_save_json(filename or cfg.SHADOW_FILE, data)


def record_candidate(
    signal: dict[str, Any] | None,
    legacy_debug: dict[str, Any] | None = None,
    live_debug: dict[str, Any] | None = None,
    *,
    filename: str | None = None,
    current_ts: int | None = None,
) -> str | None:
    if not isinstance(signal, dict):
        return None
    current = int(current_ts if current_ts is not None else now_ts())
    symbol = normalize_symbol(signal.get("symbol"))
    direction = str(signal.get("direction", "")).upper()
    entry = safe_float(signal.get("entry"))
    sl = safe_float(signal.get("sl"))
    if not symbol or direction not in {"LONG", "SHORT"} or entry <= 0 or sl <= 0:
        return None

    data = load_shadow(filename)
    records = data.setdefault("records", [])
    for row in records:
        if (
            normalize_symbol(row.get("symbol")) == symbol
            and str(row.get("direction", "")).upper() == direction
            and current - int(row.get("created_at", 0)) < cfg.SHADOW_DUPLICATE_SECONDS
        ):
            return None

    debug_old = legacy_debug if isinstance(legacy_debug, dict) else {}
    debug_live = live_debug if isinstance(live_debug, dict) else {}
    record_id = f"{symbol}_{direction}_ATTACK_GUARD_{current}"
    record = {
        "id": record_id,
        "symbol": symbol,
        "direction": direction,
        "source": "ATAK_SCALP",
        "stage": "ATTACK_GUARD_SHADOW",
        "created_at": current,
        "created_at_tr": tr_text(current),
        "last_checked_at": current - 1,
        "entry": entry,
        "sl": sl,
        "tp1": safe_float(signal.get("tp1")),
        "tp2": safe_float(signal.get("tp2")),
        "tp3": safe_float(signal.get("tp3")),
        "risk_percent": safe_float(signal.get("risk_percent")),
        "close_power": safe_float(signal.get("close_power")),
        "legacy_score": int(safe_float(debug_old.get("score", signal.get("score")), 0)),
        "live_score": int(safe_float(debug_live.get("score"), 0)),
        "legacy_close_power_threshold": (
            cfg.LEGACY_ATTACK_LONG_MIN_CLOSE_POWER if direction == "LONG"
            else cfg.LEGACY_ATTACK_SHORT_MAX_CLOSE_POWER
        ),
        "live_close_power_threshold": (
            cfg.LIVE_ATTACK_LONG_MIN_CLOSE_POWER if direction == "LONG"
            else cfg.LIVE_ATTACK_SHORT_MAX_CLOSE_POWER
        ),
        "rsi1": safe_float(signal.get("rsi1")),
        "rsi5": safe_float(signal.get("rsi5")),
        "vol1": safe_float(signal.get("vol1")),
        "vol5": safe_float(signal.get("vol5")),
        "move1": safe_float(signal.get("move1")),
        "move5": safe_float(signal.get("move5")),
        "move15": safe_float(signal.get("move15")),
        "tp1_hit": False,
        "tp2_hit": False,
        "tp3_hit": False,
        "outcome": "OPEN",
        "resolution_note": "Eski 62/38 uygun; canlı 70/30 kapanış gücü guardı nedeniyle elendi.",
    }
    records.append(record)
    cutoff = current - cfg.SHADOW_KEEP_DAYS * 24 * 60 * 60
    kept = [row for row in records if int(row.get("created_at", 0)) >= cutoff]
    kept.sort(key=lambda row: int(row.get("created_at", 0)))
    data["records"] = kept[-cfg.SHADOW_MAX_RECORDS:]
    save_shadow(data, filename)
    return record_id


def _fetch_candles(exchange: Any, symbol: str, since_seconds: int, limit: int = 180) -> list[dict[str, float]]:
    try:
        raw = exchange.fetch_ohlcv(
            to_okx_symbol(symbol),
            timeframe="1m",
            since=max(0, int(since_seconds)) * 1000,
            limit=limit,
        )
        return [
            {
                "time": int(row[0] / 1000),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
            }
            for row in (raw or [])
            if len(row) >= 5
        ]
    except Exception as exc:
        print(symbol, "ATAK guard shadow mum hatası:", type(exc).__name__)
        return []


def update_shadow(
    exchange: Any,
    *,
    filename: str | None = None,
    current_ts: int | None = None,
) -> None:
    current = int(current_ts if current_ts is not None else now_ts())
    data = load_shadow(filename)
    records = data.setdefault("records", [])
    open_rows = [row for row in records if str(row.get("outcome", "OPEN")).upper() == "OPEN"]

    for record in open_rows:
        symbol = normalize_symbol(record.get("symbol"))
        direction = str(record.get("direction", "")).upper()
        entry = safe_float(record.get("entry"))
        sl = safe_float(record.get("sl"))
        tp1 = safe_float(record.get("tp1"))
        tp2 = safe_float(record.get("tp2"))
        tp3 = safe_float(record.get("tp3"))
        created = int(record.get("created_at", current))
        last_checked = int(record.get("last_checked_at", created - 1))

        candles = _fetch_candles(exchange, symbol, max(created, last_checked - 120))
        for candle in candles:
            candle_time = int(candle.get("time") or 0)
            if candle_time <= last_checked:
                continue
            high = safe_float(candle.get("high"))
            low = safe_float(candle.get("low"))
            close = safe_float(candle.get("close"))

            if not record.get("tp1_hit"):
                if direction == "LONG":
                    hit_sl, hit_tp1 = low <= sl, high >= tp1
                    if hit_sl and hit_tp1:
                        if close >= entry:
                            hit_sl = False
                        else:
                            hit_tp1 = False
                else:
                    hit_sl, hit_tp1 = high >= sl, low <= tp1
                    if hit_sl and hit_tp1:
                        if close <= entry:
                            hit_sl = False
                        else:
                            hit_tp1 = False

                if hit_sl:
                    record["outcome"] = "STOP_BEFORE_TP1"
                    record["resolved_at"] = candle_time
                    record["resolved_at_tr"] = tr_text(candle_time)
                    last_checked = candle_time
                    break
                if hit_tp1:
                    record["tp1_hit"] = True
                    record["tp1_hit_at"] = candle_time

            if record.get("tp1_hit"):
                hit_tp2 = high >= tp2 if direction == "LONG" else low <= tp2
                hit_tp3 = high >= tp3 if direction == "LONG" else low <= tp3
                if hit_tp2 and not record.get("tp2_hit"):
                    record["tp2_hit"] = True
                    record["tp2_hit_at"] = candle_time
                if hit_tp3:
                    record["tp3_hit"] = True
                    record["tp3_hit_at"] = candle_time
                    record["outcome"] = "TP3"
                    record["resolved_at"] = candle_time
                    record["resolved_at_tr"] = tr_text(candle_time)
                    last_checked = candle_time
                    break

            last_checked = max(last_checked, candle_time)

        record["last_checked_at"] = last_checked
        if str(record.get("outcome", "OPEN")).upper() == "OPEN":
            age_minutes = max(0.0, (current - created) / 60.0)
            if age_minutes >= cfg.SHADOW_MAX_TRACK_MINUTES:
                record["outcome"] = "TP1_ONLY" if record.get("tp1_hit") else "EXPIRED_NO_TP1"
                record["resolved_at"] = current
                record["resolved_at_tr"] = tr_text(current)

    save_shadow(data, filename)
    summary = data.get("summary", {})
    print(
        "ATAK guard shadow | açık:", summary.get("open", 0),
        "| sonuçlanan:", summary.get("resolved", 0),
        "| karar:", summary.get("decision", "VERI_TOPLA"),
    )
