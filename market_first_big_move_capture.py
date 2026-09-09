"""Market First Big Move Capture V1.

Purpose:
- reuse Movement Start V2 + the proven Premium Big Move evaluator,
- surface only large-move starts with meaningful projected room,
- keep the existing Market First trade engine unchanged,
- track +1/+2/+3/+5% movement outcomes for evidence.

This module sends a separate Telegram heads-up only. It never creates a trade
candidate, never places an order and never bypasses Market First guards.
"""
from __future__ import annotations

from collections import Counter
import json
import math
import os
import tempfile
import time
from typing import Any, Dict, Mapping

import market_first_runner as runner
import movement_start_v2_shadow as movement_v2
import premium_big_move_live as big_live

VERSION = "MARKET_FIRST_BIG_MOVE_CAPTURE_V1_2026_09_09"
MODE = "TELEGRAM_BIG_MOVE_ALERT_ONLY_NO_TRADE_PROMOTION_NO_ORDERS"
STATE_FILE = "market_first_big_move_capture.json"

MIN_BASE_STAGE = {"ARMED", "TRIGGER"}
MIN_PROJECTED_TP2_PERCENT = 0.80
MIN_PROJECTED_TP3_PERCENT = 1.50
ALERT_COOLDOWN_SECONDS = 2 * 60 * 60
MAX_OPEN_TRACKS = 8
MAX_TRACK_SECONDS = 12 * 60 * 60
MAX_HISTORY = 600
MOVE_LEVELS = (1.0, 2.0, 3.0, 5.0)

_INSTALLED = False
_STATE: Dict[str, Any] = {}
_DIRTY = False
_CACHE: Dict[str, Dict[str, Any]] = {}


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _default_state() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "mode": MODE,
        "updated_at": 0,
        "open": {},
        "history": [],
        "last_alert": {},
        "summary": {},
    }


def _load() -> Dict[str, Any]:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            data = _default_state()
    except Exception:
        data = _default_state()
    data.setdefault("open", {})
    data.setdefault("history", [])
    data.setdefault("last_alert", {})
    data.setdefault("summary", {})
    data["version"] = VERSION
    data["mode"] = MODE
    return data


def _atomic_save(data: Mapping[str, Any]) -> None:
    folder = os.path.dirname(os.path.abspath(STATE_FILE)) or "."
    os.makedirs(folder, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=folder,
            prefix=".market_first_big_move_capture.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(dict(data), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, STATE_FILE)
        temp_path = None
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


def _state() -> Dict[str, Any]:
    global _STATE
    if not _STATE:
        _STATE = _load()
    return _STATE


def _arg(args, kwargs, name: str, index: int, default=None):
    if name in kwargs:
        return kwargs.get(name)
    return args[index] if len(args) > index else default


def _key(symbol: str, direction: str) -> str:
    return f"{str(symbol or '').upper()}:{str(direction or '').upper()}"


def _directional_percent(direction: str, entry: float, price: float) -> float:
    if min(entry, price) <= 0:
        return 0.0
    raw = (price / entry - 1.0) * 100.0
    return raw if str(direction).upper() == "LONG" else -raw


def _projected_percent(candidate: Mapping[str, Any], target_key: str) -> float:
    direction = str(candidate.get("direction") or "").upper()
    entry = _sf(candidate.get("entry"))
    target = _sf(candidate.get(target_key))
    return max(0.0, _directional_percent(direction, entry, target))


def _has_meaningful_room(candidate: Mapping[str, Any]) -> bool:
    return (
        _projected_percent(candidate, "tp2") >= MIN_PROJECTED_TP2_PERCENT
        and _projected_percent(candidate, "tp3") >= MIN_PROJECTED_TP3_PERCENT
    )


def _market_allows(context: Any, direction: str) -> bool:
    direction = str(direction or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return False
    preferred = str(getattr(context, "preferred_direction", None) or "").upper()
    allow_countertrend = bool(getattr(context, "allow_countertrend", True))
    opposite = "SHORT" if direction == "LONG" else "LONG"
    return not (preferred == opposite and not allow_countertrend)


def _can_alert(symbol: str, direction: str, now: int) -> bool:
    state = _state()
    key = _key(symbol, direction)
    if key in state.get("open", {}):
        return False
    last = int((state.get("last_alert", {}).get(key) or {}).get("at") or 0)
    return now - last >= ALERT_COOLDOWN_SECONDS


def _format_message(candidate: Mapping[str, Any]) -> str:
    direction = str(candidate.get("direction") or "").upper()
    icon = "🟢" if direction == "LONG" else "🔴"
    tp2_percent = _projected_percent(candidate, "tp2")
    tp3_percent = _projected_percent(candidate, "tp3")
    base_score = int(_sf(candidate.get("big_move_base_score")))
    live_score = int(_sf(candidate.get("score")))
    return (
        "🚀 BÜYÜK HAREKET BAŞLANGICI\n\n"
        f"🪙 Parite: {candidate.get('symbol')}\n"
        f"📊 Yön: {icon} {direction}\n"
        f"📍 İzleme fiyatı: {runner.bot.format_price(candidate.get('entry'))}\n"
        f"🛑 Yapısal stop: {runner.bot.format_price(candidate.get('sl'))}\n"
        f"🎯 Güçlü hedef: {runner.bot.format_price(candidate.get('tp2'))} (~%{tp2_percent:.2f})\n"
        f"🚀 Ana hareket hedefi: {runner.bot.format_price(candidate.get('tp3'))} (~%{tp3_percent:.2f})\n"
        f"⭐ Büyük hareket skoru: {live_score}/100 | başlangıç {base_score}/100\n"
        f"🔊 Hacim: {_sf(candidate.get('volume_ratio')):.2f}x\n"
        "ℹ️ Küçük scalp değil; yönlü hareket başlangıcı takibidir. Otomatik emir açmaz."
    )


def _new_track(candidate: Mapping[str, Any], now: int) -> Dict[str, Any]:
    direction = str(candidate.get("direction") or "").upper()
    symbol = str(candidate.get("symbol") or "").upper()
    return {
        "id": f"{symbol}_{direction}_{now}",
        "symbol": symbol,
        "direction": direction,
        "opened_at": now,
        "last_checked_at": now,
        "last_bar_at": 0,
        "entry": _sf(candidate.get("entry")),
        "stop": _sf(candidate.get("sl")),
        "score": int(_sf(candidate.get("score"))),
        "base_score": int(_sf(candidate.get("big_move_base_score"))),
        "risk_percent": round(_sf(candidate.get("risk_percent")), 4),
        "projected_tp2_percent": round(_projected_percent(candidate, "tp2"), 4),
        "projected_tp3_percent": round(_projected_percent(candidate, "tp3"), 4),
        "origin_move_percent": round(_sf(candidate.get("big_move_origin_move_percent")), 4),
        "reached": {str(int(level)): False for level in MOVE_LEVELS},
        "reached_at": {},
        "mfe_percent": 0.0,
        "mae_percent": 0.0,
        "status": "OPEN",
        "closed_at": 0,
    }


def _register_alert(candidate: Mapping[str, Any], now: int) -> None:
    global _DIRTY
    state = _state()
    key = _key(str(candidate.get("symbol")), str(candidate.get("direction")))
    state.setdefault("open", {})[key] = _new_track(candidate, now)
    state.setdefault("last_alert", {})[key] = {"at": now}
    _DIRTY = True


def _closed_rows(df5m: Any):
    if df5m is None or not hasattr(df5m, "copy"):
        return []
    frame = df5m.copy()
    needed = {"time", "high", "low"}
    if not needed.issubset(set(frame.columns)):
        return []
    for column in needed:
        try:
            frame[column] = frame[column].astype(float)
        except Exception:
            return []
    frame = frame.dropna(subset=list(needed)).reset_index(drop=True)
    if len(frame) <= 1:
        return []
    return [row for _, row in frame.iloc[:-1].iterrows()]


def _bar_stats(record: Mapping[str, Any], high: float, low: float) -> tuple[float, float, bool]:
    direction = str(record.get("direction") or "").upper()
    entry = _sf(record.get("entry"))
    stop = _sf(record.get("stop"))
    if direction == "LONG":
        favorable = max(0.0, _directional_percent("LONG", entry, high))
        adverse = max(0.0, -_directional_percent("LONG", entry, low))
        stop_hit = stop > 0 and low <= stop
    else:
        favorable = max(0.0, _directional_percent("SHORT", entry, low))
        adverse = max(0.0, -_directional_percent("SHORT", entry, high))
        stop_hit = stop > 0 and high >= stop
    return favorable, adverse, stop_hit


def _archive(key: str, record: Dict[str, Any], status: str, now: int) -> None:
    global _DIRTY
    state = _state()
    record["status"] = status
    record["closed_at"] = now
    state.setdefault("history", []).append(dict(record))
    state["history"] = state["history"][-MAX_HISTORY:]
    state.setdefault("open", {}).pop(key, None)
    _DIRTY = True


def _update_track(symbol: str, df5m: Any, now: int) -> None:
    global _DIRTY
    state = _state()
    matches = [
        (key, record)
        for key, record in list(state.get("open", {}).items())
        if str((record or {}).get("symbol") or "").upper() == str(symbol or "").upper()
    ]
    if not matches:
        return

    rows = _closed_rows(df5m)
    for key, record in matches:
        opened_at = int(record.get("opened_at") or 0)
        last_bar_at = int(record.get("last_bar_at") or 0)
        if now - opened_at >= MAX_TRACK_SECONDS:
            _archive(key, record, "EXPIRED", now)
            continue

        changed = False
        for row in rows:
            bar_at = int(_sf(row.get("time")) / 1000.0)
            # Do not score the candle that was already in progress at alert time.
            if bar_at <= opened_at or bar_at <= last_bar_at:
                continue
            high = _sf(row.get("high"))
            low = _sf(row.get("low"))
            if min(high, low) <= 0:
                continue

            favorable, adverse, stop_hit = _bar_stats(record, high, low)
            record["mfe_percent"] = round(max(_sf(record.get("mfe_percent")), favorable), 4)
            record["mae_percent"] = round(max(_sf(record.get("mae_percent")), adverse), 4)
            record["last_bar_at"] = bar_at
            changed = True

            touched = [level for level in MOVE_LEVELS if favorable >= level]
            if stop_hit:
                # Conservative ordering when stop and a milestone share one unseen bar.
                reached_before = any(bool(record.get("reached", {}).get(str(int(level)))) for level in MOVE_LEVELS)
                _archive(
                    key,
                    record,
                    "STOP_AFTER_MOVE" if reached_before else "STOP_BEFORE_1P",
                    now,
                )
                break

            for level in touched:
                label = str(int(level))
                if not bool(record.get("reached", {}).get(label)):
                    record.setdefault("reached", {})[label] = True
                    record.setdefault("reached_at", {})[label] = bar_at
                    changed = True

            if bool(record.get("reached", {}).get("5")):
                _archive(key, record, "MOVE_5P_REACHED", now)
                break
        else:
            record["last_checked_at"] = now

        if changed:
            _DIRTY = True


def _open_symbols() -> list[str]:
    rows = list(_state().get("open", {}).values())
    rows.sort(key=lambda item: int((item or {}).get("opened_at") or 0))
    return [
        str(item.get("symbol") or "").upper()
        for item in rows
        if str(item.get("symbol") or "").strip()
    ]


def _summary() -> Dict[str, Any]:
    state = _state()
    history = [row for row in state.get("history", []) if isinstance(row, dict)]
    all_rows = history + [row for row in state.get("open", {}).values() if isinstance(row, dict)]
    status_counts = Counter(str(row.get("status") or "UNKNOWN") for row in history)

    reached_counts = {}
    for level in MOVE_LEVELS:
        label = str(int(level))
        reached_counts[f"reached_{label}p"] = sum(
            1 for row in all_rows if bool((row.get("reached") or {}).get(label))
        )

    by_direction: Dict[str, Dict[str, int]] = {}
    for direction in ("LONG", "SHORT"):
        subset = [row for row in all_rows if str(row.get("direction") or "") == direction]
        by_direction[direction] = {
            "total": len(subset),
            "reached_1p": sum(1 for row in subset if bool((row.get("reached") or {}).get("1"))),
            "reached_2p": sum(1 for row in subset if bool((row.get("reached") or {}).get("2"))),
            "reached_3p": sum(1 for row in subset if bool((row.get("reached") or {}).get("3"))),
            "reached_5p": sum(1 for row in subset if bool((row.get("reached") or {}).get("5"))),
        }

    return {
        "version": VERSION,
        "mode": MODE,
        "total_alerts": len(all_rows),
        "open": len(state.get("open", {})),
        "closed": len(history),
        **reached_counts,
        "stop_before_1p": status_counts.get("STOP_BEFORE_1P", 0),
        "stop_after_move": status_counts.get("STOP_AFTER_MOVE", 0),
        "expired": status_counts.get("EXPIRED", 0),
        "move_5p_reached": status_counts.get("MOVE_5P_REACHED", 0),
        "avg_mfe_percent": round(
            sum(_sf(row.get("mfe_percent")) for row in all_rows) / len(all_rows), 4
        ) if all_rows else 0.0,
        "avg_mae_percent": round(
            sum(_sf(row.get("mae_percent")) for row in all_rows) / len(all_rows), 4
        ) if all_rows else 0.0,
        "by_direction": by_direction,
        "note": "Alert-only evidence; not realized PnL and not an automatic trade route.",
    }


def install() -> None:
    global _INSTALLED, _STATE
    if _INSTALLED:
        return
    _INSTALLED = True
    _STATE = _load()
    big_live.begin()

    original_frames = runner._candidate_frames
    original_analyze = runner.analyze_candidate
    original_select = runner._select_deep_scan

    def frames_with_big_move(exchange: Any, symbol: str):
        frames = original_frames(exchange, symbol)
        try:
            _, df5m, df15m, df1h = frames
            base = movement_v2.analyze(
                symbol=symbol,
                df5m=df5m,
                df15m=df15m,
                df1h=df1h,
                df4h=None,
                current_price=None,
            )
            cache = {"base": base, "df4h": None}
            if (
                isinstance(base, Mapping)
                and str(base.get("stage") or "").upper() in MIN_BASE_STAGE
                and int(_sf(base.get("score"))) >= big_live.MIN_BASE_SCORE
            ):
                try:
                    cache["df4h"] = runner.bot.fetch_df(
                        exchange, symbol, "4h", 110, min_len=70
                    )
                except Exception as exc:
                    print("BIG MOVE 4H veri hatası:", symbol, type(exc).__name__, exc)
            _CACHE[str(symbol).upper()] = cache
            if len(_CACHE) > 300:
                for old_key in list(_CACHE.keys())[: len(_CACHE) - 300]:
                    _CACHE.pop(old_key, None)
        except Exception as exc:
            print("BIG MOVE ön tarama hatası:", symbol, type(exc).__name__, exc)
        return frames

    def analyze_with_big_move(*args, **kwargs):
        result = original_analyze(*args, **kwargs)
        symbol = str(_arg(args, kwargs, "symbol", 0, "") or "").upper()
        df5m = _arg(args, kwargs, "df5m", 2)
        df15m = _arg(args, kwargs, "df15m", 3)
        df1h = _arg(args, kwargs, "df1h", 4)
        current_price = _sf(_arg(args, kwargs, "current_price", 5, 0.0))
        context = _arg(args, kwargs, "context", 7)
        now = int(time.time())

        try:
            _update_track(symbol, df5m, now)
            cached = _CACHE.get(symbol) or {}
            base = cached.get("base")
            df4h = cached.get("df4h")
            if isinstance(base, Mapping) and df4h is not None and current_price > 0:
                candidate = big_live.analyze_live_candidate(
                    symbol=symbol,
                    base_result=dict(base),
                    df15m=df15m,
                    df1h=df1h,
                    df4h=df4h,
                    current_price=current_price,
                    flow_snapshot=None,
                    now_ts=now,
                )
                if (
                    isinstance(candidate, Mapping)
                    and _has_meaningful_room(candidate)
                    and _market_allows(context, str(candidate.get("direction") or ""))
                    and _can_alert(symbol, str(candidate.get("direction") or ""), now)
                    and len(_state().get("open", {})) < MAX_OPEN_TRACKS
                ):
                    if runner._send(
                        _format_message(candidate),
                        delivery_key=(
                            f"BIG_MOVE_CAPTURE_V1|{symbol}|"
                            f"{candidate.get('direction')}|{now // ALERT_COOLDOWN_SECONDS}"
                        ),
                    ):
                        _register_alert(candidate, now)
                        print(
                            "BIG MOVE ALERT:",
                            symbol,
                            candidate.get("direction"),
                            "score=", candidate.get("score"),
                            "tp3%=", round(_projected_percent(candidate, "tp3"), 2),
                        )
        except Exception as exc:
            print("BIG MOVE canlı değerlendirme hatası:", symbol, type(exc).__name__, exc)
        return result

    def select_with_big_move(*args, **kwargs):
        selected = list(original_select(*args, **kwargs) or [])
        if not selected:
            return selected
        result = []
        seen = set()
        for symbol in _open_symbols() + selected:
            symbol = str(symbol or "").upper()
            if symbol and symbol not in seen:
                seen.add(symbol)
                result.append(symbol)
            if len(result) >= len(selected):
                break
        return result

    runner._candidate_frames = frames_with_big_move
    runner.analyze_candidate = analyze_with_big_move
    runner._select_deep_scan = select_with_big_move


def finish() -> Dict[str, Any]:
    global _DIRTY
    state = _state()
    now = int(time.time())
    for key, record in list(state.get("open", {}).items()):
        opened_at = int((record or {}).get("opened_at") or 0)
        if opened_at and now - opened_at >= MAX_TRACK_SECONDS:
            _archive(key, record, "EXPIRED", now)

    summary = _summary()
    state["version"] = VERSION
    state["mode"] = MODE
    state["updated_at"] = now
    state["summary"] = summary
    if _DIRTY or not os.path.exists(STATE_FILE):
        _atomic_save(state)
        _DIRTY = False
    try:
        big_live.finish()
    except Exception as exc:
        print("BIG MOVE premium teşhis kaydı hatası:", type(exc).__name__, exc)
    return summary


def summary() -> Dict[str, Any]:
    return _summary()
