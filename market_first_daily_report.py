"""Simple once-per-day outcome report for Market First.

Telegram stays quiet during the day (real trades/results only).  Near the end of
Türkiye's trading day this module summarizes two things in one compact report:
1) REAL TRADES that were actually sent/opened by the live system,
2) BACKGROUND OPPORTUNITIES that the internal early/prep/swing ledgers watched but
   never became the same real trade.

Background movement is observational and must never be presented as realised P&L.
No strategy, score, direction, entry, stop, target or exchange request is changed
here.
"""
from __future__ import annotations

from datetime import date, datetime, time as dt_time, timedelta, timezone
import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

VERSION = "MARKET_FIRST_DAILY_REPORT_V1_2026_09_06"
STATE_FILE = "market_first_daily_report_state.json"
REPORT_FILE = "market_first_daily_report.json"
REPORT_HOUR = 23
REPORT_MINUTE = 45
CHUNK_LIMIT = 3400
TRT = timezone(timedelta(hours=3))


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _timestamp(value: Any) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, (int, float)):
        number = _sf(value)
        if number > 10_000_000_000:
            number /= 1000.0
        return int(number) if number > 0 else 0
    text = str(value).strip()
    if not text:
        return 0
    try:
        number = float(text)
        if number > 10_000_000_000:
            number /= 1000.0
        if number > 0:
            return int(number)
    except Exception:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=TRT)
        return int(parsed.timestamp())
    except Exception:
        return 0


def _local_date(ts: int) -> Optional[date]:
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts), TRT).date()
    except Exception:
        return None


_TIME_FIELDS = (
    "first_at", "alert_time", "opened_at", "created_at", "timestamp",
    "entry_time", "signal_time", "sent_at", "updated_at", "resolved_at",
    "closed_at", "last_update", "last_plan_at", "last_seen_at",
)


def _record_touches_date(record: Mapping[str, Any], target: date) -> bool:
    for key in _TIME_FIELDS:
        if _local_date(_timestamp(record.get(key))) == target:
            return True
    return False


def _iter_records(payload: Any, containers: Sequence[str] = ("trades", "episodes")) -> Iterable[Mapping[str, Any]]:
    if not isinstance(payload, Mapping):
        return []
    for key in containers:
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            return [item for item in nested.values() if isinstance(item, Mapping)]
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, Mapping)]
    # Some repo state files store records directly under their ids.
    values = [item for item in payload.values() if isinstance(item, Mapping)]
    return values


def _symbol_direction(record: Mapping[str, Any]) -> Tuple[str, str]:
    symbol = str(record.get("symbol") or record.get("coin") or "").upper().strip()
    direction = str(record.get("direction") or record.get("side") or "").upper().strip()
    if direction in {"BUY", "AL", "LONG"}:
        direction = "LONG"
    elif direction in {"SELL", "SAT", "SHORT"}:
        direction = "SHORT"
    return symbol, direction


def _favorable(record: Mapping[str, Any]) -> float:
    for key in (
        "best_favorable_percent", "mfe_percent", "best_move_percent",
        "max_favorable_percent", "favorable_percent",
    ):
        if record.get(key) is not None:
            return max(0.0, _sf(record.get(key)))
    initial = _sf(record.get("initial_price") or record.get("prep_price") or record.get("entry"))
    best = _sf(record.get("best_price"))
    direction = str(record.get("direction") or "").upper()
    if initial > 0 and best > 0:
        raw = (best / initial - 1.0) * 100.0
        return max(0.0, raw if direction == "LONG" else -raw)
    return 0.0


def _adverse(record: Mapping[str, Any]) -> float:
    for key in (
        "worst_adverse_percent", "mae_percent", "max_adverse_percent",
        "best_adverse_percent", "adverse_percent",
    ):
        if record.get(key) is not None:
            return abs(_sf(record.get(key)))
    initial = _sf(record.get("initial_price") or record.get("prep_price") or record.get("entry"))
    worst = _sf(record.get("worst_price"))
    direction = str(record.get("direction") or "").upper()
    if initial > 0 and worst > 0:
        raw = (worst / initial - 1.0) * 100.0
        adverse = -raw if direction == "LONG" else raw
        return max(0.0, adverse)
    return 0.0


def _has(record: Mapping[str, Any], *names: str) -> bool:
    return any(bool(record.get(name)) for name in names)


def _result_text(record: Mapping[str, Any]) -> str:
    value = (
        record.get("final_result") or record.get("result") or record.get("outcome")
        or record.get("status") or ""
    )
    return str(value).upper().strip()


def _real_result(record: Mapping[str, Any]) -> str:
    text = _result_text(record)
    if "TP3" in text or _has(record, "tp3_hit", "tp3_at"):
        return "TP3"
    if "TP2" in text or _has(record, "tp2_hit", "tp2_at"):
        return "TP2"
    if "TP1" in text or _has(record, "tp1_hit", "tp1_at"):
        if "BE" in text:
            return "TP1 + BE"
        return "TP1"
    if "STOP" in text or "SL" in text:
        return "STOP"
    if "BE" in text or "BREAKEVEN" in text:
        return "BE"
    if bool(record.get("closed")):
        return text or "KAPANDI"
    return "AÇIK"


def _background_result(record: Mapping[str, Any]) -> str:
    text = _result_text(record)
    first = str(record.get("first_decisive_event") or "").upper()
    entry_sent = bool(record.get("entry_signal_sent") or record.get("real_entry_signal_sent"))

    if _has(record, "tp3_at") or "TP3" in first or "TP3" in text:
        return "DOĞRU YÖN / GİRİŞ YOK" if not entry_sent else "TP3 YÖNÜ"
    if _has(record, "tp2_at") or "TP2" in first or "TP2" in text:
        return "DOĞRU YÖN / GİRİŞ YOK" if not entry_sent else "TP2 YÖNÜ"
    if _has(record, "tp1_at") or "TP1" in first or "TP1_FIRST" in text:
        return "DOĞRU YÖN / GİRİŞ YOK" if not entry_sent else "TP1 YÖNÜ"

    if (
        "SL_FIRST" in first or "SL_FIRST" in text or "STOP_FIRST" in text
        or "BAD_MOVE" in text or "NO_FOLLOWTHROUGH" in text
    ):
        return "YÖN TERS"
    if "STRONG_MOVE" in text or "GOOD_MOVE" in text or "FAVORABLE_MOVE" in text:
        return "DOĞRU YÖN / GİRİŞ YOK"
    if "MIXED" in text or "AMBIGUOUS" in text:
        return "KARIŞIK"
    if "CHASED" in text or "NO_ENTRY" in text or bool(record.get("tp1_before_entry_signal")):
        if _favorable(record) >= 0.8:
            return "DOĞRU YÖN / GİRİŞ YOK"
        return "GİRİŞ OLMADI"
    if bool(record.get("resolved")):
        if _favorable(record) >= max(1.0, _adverse(record)):
            return "DOĞRU YÖN / GİRİŞ YOK"
        if _adverse(record) > _favorable(record) and _adverse(record) >= 0.8:
            return "YÖN TERS"
        return "BİTTİ"
    return "TAKİPTE"


def _progress_text(favorable: float, adverse: float, result: str) -> str:
    favorable = max(0.0, favorable)
    adverse = max(0.0, adverse)
    if favorable > 0.0:
        return f"+{favorable:.1f}%"
    if adverse > 0.0 and ("STOP" in result or "TERS" in result):
        return f"-{adverse:.1f}%"
    return "0.0%"


def _merge_row(target: Dict[Tuple[str, str], Dict[str, Any]], row: Dict[str, Any]) -> None:
    key = (row["symbol"], row["direction"])
    old = target.get(key)
    if old is None:
        target[key] = row
        return
    old["favorable_percent"] = max(_sf(old.get("favorable_percent")), _sf(row.get("favorable_percent")))
    old["adverse_percent"] = max(_sf(old.get("adverse_percent")), _sf(row.get("adverse_percent")))
    old.setdefault("sources", [])
    for source in row.get("sources", []):
        if source not in old["sources"]:
            old["sources"].append(source)

    priority = {
        "DOĞRU YÖN / GİRİŞ YOK": 6,
        "YÖN TERS": 5,
        "KARIŞIK": 4,
        "GİRİŞ OLMADI": 3,
        "BİTTİ": 2,
        "TAKİPTE": 1,
    }
    if priority.get(str(row.get("result")), 0) > priority.get(str(old.get("result")), 0):
        old["result"] = row["result"]


def build_report(bot: Any, now: Optional[int] = None, target_date: Optional[date] = None) -> Dict[str, Any]:
    now = int(now or datetime.now(tz=TRT).timestamp())
    target = target_date or datetime.fromtimestamp(now, TRT).date()

    trade_payload = bot.load_json_file(getattr(bot, "TRADE_LEDGER_FILE", "trade_ledger.json"), {})
    open_payload = bot.load_json_file(getattr(bot, "OPEN_SIGNALS_FILE", "open_signals.json"), {})

    real: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for payload in (trade_payload, open_payload):
        for record in _iter_records(payload, containers=("trades", "open_signals")):
            symbol, direction = _symbol_direction(record)
            if not symbol or direction not in {"LONG", "SHORT"} or not _record_touches_date(record, target):
                continue
            row = {
                "symbol": symbol,
                "direction": direction,
                "favorable_percent": round(_favorable(record), 4),
                "adverse_percent": round(_adverse(record), 4),
                "result": _real_result(record),
            }
            key = (symbol, direction)
            old = real.get(key)
            if old is None:
                real[key] = row
            else:
                old["favorable_percent"] = max(_sf(old.get("favorable_percent")), row["favorable_percent"])
                old["adverse_percent"] = max(_sf(old.get("adverse_percent")), row["adverse_percent"])
                if old.get("result") == "AÇIK" and row["result"] != "AÇIK":
                    old["result"] = row["result"]

    background: Dict[Tuple[str, str], Dict[str, Any]] = {}
    background_sources = (
        ("ENTRY_PLAN", "market_first_entry_plan_ledger.json"),
        ("SWING_2H", "market_first_swing_2h_ledger.json"),
        ("EARLY", "market_first_early_ledger.json"),
    )
    for source, filename in background_sources:
        payload = bot.load_json_file(filename, {})
        for record in _iter_records(payload, containers=("episodes",)):
            symbol, direction = _symbol_direction(record)
            if not symbol or direction not in {"LONG", "SHORT"} or not _record_touches_date(record, target):
                continue
            if (symbol, direction) in real:
                continue
            row = {
                "symbol": symbol,
                "direction": direction,
                "favorable_percent": round(_favorable(record), 4),
                "adverse_percent": round(_adverse(record), 4),
                "result": _background_result(record),
                "sources": [source],
            }
            _merge_row(background, row)

    real_rows = sorted(real.values(), key=lambda item: (item["symbol"], item["direction"]))
    background_rows = sorted(
        background.values(),
        key=lambda item: (-_sf(item.get("favorable_percent")), item["symbol"], item["direction"]),
    )
    return {
        "version": VERSION,
        "date": target.isoformat(),
        "generated_at": now,
        "real_trades": real_rows,
        "background": background_rows,
        "summary": {
            "real_trade_count": len(real_rows),
            "background_count": len(background_rows),
            "background_correct_direction": sum(1 for item in background_rows if "DOĞRU YÖN" in str(item.get("result"))),
            "background_wrong_direction": sum(1 for item in background_rows if item.get("result") == "YÖN TERS"),
        },
    }


def _row_line(row: Mapping[str, Any]) -> str:
    result = str(row.get("result") or "-")
    progress = _progress_text(_sf(row.get("favorable_percent")), _sf(row.get("adverse_percent")), result)
    return f"{row.get('symbol')} {row.get('direction')} | {progress} | {result}"


def _sections(report: Mapping[str, Any]) -> List[str]:
    date_text = str(report.get("date") or "")
    try:
        date_text = datetime.strptime(date_text, "%Y-%m-%d").strftime("%d.%m.%Y")
    except Exception:
        pass
    lines = [f"📋 GÜNLÜK İŞLEM ÖZETİ | {date_text}", "", "✅ GERÇEK İŞLEMLER"]
    real_rows = report.get("real_trades") if isinstance(report.get("real_trades"), list) else []
    if real_rows:
        lines.extend(_row_line(item) for item in real_rows if isinstance(item, Mapping))
    else:
        lines.append("Yok")

    lines.extend(["", "👀 ARKA PLANDA İZLENEN"])
    background = report.get("background") if isinstance(report.get("background"), list) else []
    if background:
        lines.extend(_row_line(item) for item in background if isinstance(item, Mapping))
    else:
        lines.append("Yok")

    summary = report.get("summary") if isinstance(report.get("summary"), Mapping) else {}
    lines.extend([
        "",
        f"📌 Toplam: {int(_sf(summary.get('real_trade_count')))} gerçek | {int(_sf(summary.get('background_count')))} izleme",
        "ℹ️ Arka plan yüzdeleri gerçekleşmiş kâr değildir; sistemin izlediği yönün hareketidir.",
    ])
    return lines


def format_report(report: Mapping[str, Any]) -> List[str]:
    lines = _sections(report)
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0
    for line in lines:
        extra = len(line) + (1 if current else 0)
        if current and current_len + extra > CHUNK_LIMIT:
            chunks.append("\n".join(current))
            current = ["📋 GÜNLÜK İŞLEM ÖZETİ | DEVAM", line]
            current_len = len(current[0]) + 1 + len(line)
        else:
            current.append(line)
            current_len += extra
    if current:
        chunks.append("\n".join(current))
    return chunks


def _target_ready(now: int) -> bool:
    local = datetime.fromtimestamp(now, TRT)
    return local.time() >= dt_time(REPORT_HOUR, REPORT_MINUTE)


def maybe_send(bot: Any, send_func: Any, now: Optional[int] = None, force: bool = False) -> bool:
    now = int(now or datetime.now(tz=TRT).timestamp())
    local = datetime.fromtimestamp(now, TRT)
    target = local.date()
    if not force and not _target_ready(now):
        return False

    state = bot.load_json_file(STATE_FILE, {})
    if not isinstance(state, dict):
        state = {}
    date_key = target.isoformat()
    if not force and str(state.get("last_sent_date") or "") == date_key:
        return False

    report = build_report(bot, now=now, target_date=target)
    if hasattr(bot, "save_json_file"):
        bot.save_json_file(REPORT_FILE, report)

    chunks = format_report(report)
    for index, text in enumerate(chunks, start=1):
        key = f"DAILY_REPORT|{date_key}|{index}"
        try:
            sent = bool(send_func(text, delivery_key=key))
        except TypeError:
            sent = bool(send_func(text, key))
        if not sent:
            return False

    state.update({
        "version": VERSION,
        "last_sent_date": date_key,
        "last_sent_at": now,
        "parts": len(chunks),
    })
    bot.save_json_file(STATE_FILE, state)
    return True
