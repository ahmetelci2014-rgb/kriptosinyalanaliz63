"""Once-per-day outcome and missed-opportunity report for Market First.

This module is measurement-only. It does not change strategy filters, scores,
directions, entries, stops, targets, leverage, or exchange requests.

V2 fixes an important reporting ambiguity: a large eventual favorable move is
not automatically called a "correct direction". When the ledgers contain enough
timing information, the report distinguishes TP-first from SL-first. When event
order is unknown, it explicitly says so instead of overstating the opportunity.
It also carries episode/rejection metadata forward for diagnosis.
"""
from __future__ import annotations

from datetime import date, datetime, time as dt_time, timedelta, timezone
import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

VERSION = "MARKET_FIRST_DAILY_REPORT_V2_2026_09_09"
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
    "candidate_at", "first_seen_at",
)


def _record_touches_date(record: Mapping[str, Any], target: date) -> bool:
    for key in _TIME_FIELDS:
        if _local_date(_timestamp(record.get(key))) == target:
            return True
    return False


def _iter_records(
    payload: Any,
    containers: Sequence[str] = ("trades", "episodes"),
) -> Iterable[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if not isinstance(payload, Mapping):
        return []
    for key in containers:
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            return [item for item in nested.values() if isinstance(item, Mapping)]
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, Mapping)]
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
    initial = _sf(
        record.get("initial_price")
        or record.get("prep_price")
        or record.get("reference_price")
        or record.get("entry")
    )
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
    initial = _sf(
        record.get("initial_price")
        or record.get("prep_price")
        or record.get("reference_price")
        or record.get("entry")
    )
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


def _first_nonzero_ts(record: Mapping[str, Any], names: Sequence[str]) -> int:
    values = [_timestamp(record.get(name)) for name in names]
    values = [value for value in values if value > 0]
    return min(values) if values else 0


def _first_touch(record: Mapping[str, Any]) -> str:
    """Return TP_FIRST, SL_FIRST or UNKNOWN without guessing from final MFE alone."""
    text = _result_text(record)
    explicit = str(
        record.get("first_decisive_event")
        or record.get("first_touch")
        or record.get("first_event")
        or ""
    ).upper()

    combined = f"{explicit} {text}"
    if "SL_FIRST" in combined or "STOP_FIRST" in combined:
        return "SL_FIRST"
    if any(token in combined for token in (
        "TP1_FIRST", "TP2_FIRST", "TP3_FIRST", "TP_FIRST",
    )):
        return "TP_FIRST"

    tp_ts = _first_nonzero_ts(
        record,
        ("tp1_at", "tp2_at", "tp3_at", "tp_hit_at", "first_tp_at"),
    )
    sl_ts = _first_nonzero_ts(
        record,
        ("sl_at", "stop_at", "stop_hit_at", "sl_hit_at", "first_sl_at"),
    )
    if tp_ts and sl_ts:
        return "TP_FIRST" if tp_ts < sl_ts else "SL_FIRST"
    if tp_ts and not sl_ts:
        return "TP_FIRST"
    if sl_ts and not tp_ts:
        return "SL_FIRST"

    if (
        ("TP3_REACHED" in text or "TP2_REACHED" in text or "TP1_REACHED" in text)
        and "SL" not in text and "STOP" not in text
    ):
        return "TP_FIRST"
    return "UNKNOWN"


def _candidate_at(record: Mapping[str, Any]) -> int:
    for key in (
        "candidate_at", "first_at", "first_seen_at", "created_at", "alert_time",
        "signal_time", "entry_time", "timestamp", "last_plan_at",
    ):
        ts = _timestamp(record.get(key))
        if ts:
            return ts
    return 0


def _reference_price(record: Mapping[str, Any]) -> float:
    for key in ("reference_price", "initial_price", "prep_price", "entry", "entry_price"):
        value = _sf(record.get(key))
        if value > 0:
            return value
    return 0.0


def _episode_id(record: Mapping[str, Any]) -> str:
    return str(
        record.get("episode_id")
        or record.get("id")
        or record.get("trade_id")
        or ""
    ).strip()


def _rejection_reason(record: Mapping[str, Any]) -> str:
    for key in (
        "primary_obstacle", "rejection_reason", "reject_reason", "blocked_by",
        "entry_block_reason", "skip_reason", "reason",
    ):
        value = record.get(key)
        if value:
            return str(value).strip()
    flags = record.get("diagnostic_flags")
    if isinstance(flags, list) and flags:
        return str(flags[0])
    return ""


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


def _real_diagnosis(result: str, favorable: float, adverse: float) -> str:
    if result == "STOP":
        if favorable >= 0.50:
            return "KÂR GÖRDÜ → STOP"
        if favorable >= 0.15:
            return "AZ LEHTE → STOP"
        return "DİREKT/ZAYIF STOP"
    if result == "TP1 + BE":
        return "BE SONRASI TAKİP"
    return ""


def _background_result(record: Mapping[str, Any]) -> str:
    entry_sent = bool(record.get("entry_signal_sent") or record.get("real_entry_signal_sent"))
    first = _first_touch(record)

    if first == "TP_FIRST":
        return "TP ÖNCE / GİRİŞ YOK" if not entry_sent else "TP ÖNCE"
    if first == "SL_FIRST":
        return "SL ÖNCE / GİRİŞ YOK" if not entry_sent else "SL ÖNCE"

    text = _result_text(record)
    if "MIXED" in text or "AMBIGUOUS" in text:
        return "KARIŞIK"
    if "BAD_MOVE" in text or "NO_FOLLOWTHROUGH" in text:
        return "YÖN TERS"
    if "CHASED" in text or "NO_ENTRY" in text or bool(record.get("tp1_before_entry_signal")):
        if _favorable(record) >= 0.8:
            return "LEHTE HAREKET / SIRA BELİRSİZ"
        return "GİRİŞ OLMADI"

    favorable = _favorable(record)
    adverse = _adverse(record)
    if favorable >= 0.8:
        return "LEHTE HAREKET / SIRA BELİRSİZ"
    if bool(record.get("resolved")) and adverse >= 0.8 and adverse > favorable:
        return "YÖN TERS"
    if bool(record.get("resolved")):
        return "BİTTİ"
    return "TAKİPTE"


def _progress_text(favorable: float, adverse: float, result: str) -> str:
    favorable = max(0.0, favorable)
    adverse = max(0.0, adverse)
    if favorable > 0.0:
        return f"+{favorable:.1f}%"
    if adverse > 0.0 and ("STOP" in result or "TERS" in result or "SL ÖNCE" in result):
        return f"-{adverse:.1f}%"
    return "0.0%"


def _diagnosis_index(payload: Any) -> Tuple[Dict[str, Mapping[str, Any]], Dict[Tuple[str, str], Mapping[str, Any]]]:
    by_episode: Dict[str, Mapping[str, Any]] = {}
    by_pair: Dict[Tuple[str, str], Mapping[str, Any]] = {}
    for record in _iter_records(
        payload,
        containers=("diagnostics", "episodes", "records", "items"),
    ):
        episode = _episode_id(record)
        if episode:
            by_episode[episode] = record
        symbol, direction = _symbol_direction(record)
        if symbol and direction in {"LONG", "SHORT"}:
            old = by_pair.get((symbol, direction))
            if old is None or _candidate_at(record) >= _candidate_at(old):
                by_pair[(symbol, direction)] = record
    return by_episode, by_pair


def _enrich_with_diagnosis(
    row: Dict[str, Any],
    record: Mapping[str, Any],
    by_episode: Mapping[str, Mapping[str, Any]],
    by_pair: Mapping[Tuple[str, str], Mapping[str, Any]],
) -> None:
    reason = _rejection_reason(record)
    diagnosis: Optional[Mapping[str, Any]] = None
    episode = str(row.get("episode_id") or "")
    if episode:
        diagnosis = by_episode.get(episode)
    if diagnosis is None:
        diagnosis = by_pair.get((str(row.get("symbol")), str(row.get("direction"))))
    if not reason and diagnosis is not None:
        reason = _rejection_reason(diagnosis)
    if reason:
        row["rejection_reason"] = reason
    if diagnosis is not None:
        outcome = str(diagnosis.get("outcome") or "")
        if outcome:
            row["diagnostic_outcome"] = outcome


def _merge_background_row(
    target: Dict[Tuple[str, str], Dict[str, Any]],
    row: Dict[str, Any],
) -> None:
    """Keep favorable/adverse from the same representative episode.

    V1 independently maxed favorable and adverse across sources/episodes, which
    could create a synthetic row that never existed. V2 replaces the representative
    episode as a unit and only merges source/episode metadata.
    """
    key = (row["symbol"], row["direction"])
    old = target.get(key)
    if old is None:
        row["episode_count"] = 1
        target[key] = row
        return

    old["episode_count"] = int(old.get("episode_count") or 1) + 1
    old_sources = old.setdefault("sources", [])
    for source in row.get("sources", []):
        if source not in old_sources:
            old_sources.append(source)

    strict_rank = {"TP_FIRST": 3, "SL_FIRST": 2, "UNKNOWN": 1}
    old_rank = strict_rank.get(str(old.get("first_touch")), 0)
    new_rank = strict_rank.get(str(row.get("first_touch")), 0)
    replace = False
    if new_rank > old_rank:
        replace = True
    elif new_rank == old_rank and _sf(row.get("favorable_percent")) > _sf(old.get("favorable_percent")):
        replace = True

    if replace:
        preserved_sources = list(old_sources)
        preserved_count = int(old.get("episode_count") or 1)
        target[key] = row
        target[key]["sources"] = preserved_sources
        target[key]["episode_count"] = preserved_count


def _add_opposite_direction_flags(rows: List[Dict[str, Any]]) -> None:
    directions: Dict[str, set] = {}
    for row in rows:
        directions.setdefault(str(row.get("symbol")), set()).add(str(row.get("direction")))
    for row in rows:
        row["opposite_direction_seen"] = len(directions.get(str(row.get("symbol")), set())) > 1


def build_report(bot: Any, now: Optional[int] = None, target_date: Optional[date] = None) -> Dict[str, Any]:
    now = int(now or datetime.now(tz=TRT).timestamp())
    target = target_date or datetime.fromtimestamp(now, TRT).date()

    trade_payload = bot.load_json_file(getattr(bot, "TRADE_LEDGER_FILE", "trade_ledger.json"), {})
    open_payload = bot.load_json_file(getattr(bot, "OPEN_SIGNALS_FILE", "open_signals.json"), {})
    diagnosis_payload = bot.load_json_file("market_first_entry_condition_diagnosis.json", {})
    diagnosis_by_episode, diagnosis_by_pair = _diagnosis_index(diagnosis_payload)

    real: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for payload in (trade_payload, open_payload):
        for record in _iter_records(payload, containers=("trades", "open_signals")):
            symbol, direction = _symbol_direction(record)
            if not symbol or direction not in {"LONG", "SHORT"} or not _record_touches_date(record, target):
                continue
            favorable = round(_favorable(record), 4)
            adverse = round(_adverse(record), 4)
            result = _real_result(record)
            row = {
                "symbol": symbol,
                "direction": direction,
                "favorable_percent": favorable,
                "adverse_percent": adverse,
                "result": result,
                "diagnosis": _real_diagnosis(result, favorable, adverse),
            }
            key = (symbol, direction)
            old = real.get(key)
            if old is None:
                real[key] = row
            else:
                old["favorable_percent"] = max(_sf(old.get("favorable_percent")), favorable)
                old["adverse_percent"] = max(_sf(old.get("adverse_percent")), adverse)
                if old.get("result") == "AÇIK" and result != "AÇIK":
                    old["result"] = result
                    old["diagnosis"] = _real_diagnosis(
                        result,
                        _sf(old.get("favorable_percent")),
                        _sf(old.get("adverse_percent")),
                    )

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

            first_touch = _first_touch(record)
            row: Dict[str, Any] = {
                "symbol": symbol,
                "direction": direction,
                "favorable_percent": round(_favorable(record), 4),
                "adverse_percent": round(_adverse(record), 4),
                "result": _background_result(record),
                "first_touch": first_touch,
                "candidate_at": _candidate_at(record),
                "reference_price": _reference_price(record),
                "episode_id": _episode_id(record),
                "sources": [source],
            }
            _enrich_with_diagnosis(
                row, record, diagnosis_by_episode, diagnosis_by_pair,
            )
            _merge_background_row(background, row)

    real_rows = sorted(real.values(), key=lambda item: (item["symbol"], item["direction"]))
    background_rows = sorted(
        background.values(),
        key=lambda item: (-_sf(item.get("favorable_percent")), item["symbol"], item["direction"]),
    )
    _add_opposite_direction_flags(background_rows)

    strict_tp_first = sum(1 for item in background_rows if item.get("first_touch") == "TP_FIRST")
    strict_sl_first = sum(1 for item in background_rows if item.get("first_touch") == "SL_FIRST")
    unknown_order = sum(1 for item in background_rows if item.get("first_touch") == "UNKNOWN")
    stop_after_profit = sum(
        1 for item in real_rows
        if item.get("result") == "STOP" and _sf(item.get("favorable_percent")) >= 0.15
    )
    be_followup = sum(1 for item in real_rows if item.get("result") == "TP1 + BE")

    return {
        "version": VERSION,
        "date": target.isoformat(),
        "generated_at": now,
        "real_trades": real_rows,
        "background": background_rows,
        "summary": {
            "real_trade_count": len(real_rows),
            "background_count": len(background_rows),
            "background_tp_first": strict_tp_first,
            "background_sl_first": strict_sl_first,
            "background_unknown_order": unknown_order,
            "real_stop_after_positive_move": stop_after_profit,
            "tp1_be_needs_followup": be_followup,
        },
    }


def _row_line(row: Mapping[str, Any]) -> str:
    result = str(row.get("result") or "-")
    progress = _progress_text(
        _sf(row.get("favorable_percent")),
        _sf(row.get("adverse_percent")),
        result,
    )
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
        f"📌 Toplam: {int(_sf(summary.get('real_trade_count')))} gerçek | "
        f"{int(_sf(summary.get('background_count')))} izleme",
        f"🧪 Arka plan: {int(_sf(summary.get('background_tp_first')))} TP önce | "
        f"{int(_sf(summary.get('background_sl_first')))} SL önce | "
        f"{int(_sf(summary.get('background_unknown_order')))} sıra belirsiz",
        f"🔎 Gerçek stop teşhisi: {int(_sf(summary.get('real_stop_after_positive_move')))} "
        "işlem stop öncesi lehte hareket gördü",
        f"🟡 TP1+BE sonrası takip adayı: {int(_sf(summary.get('tp1_be_needs_followup')))}",
        "ℹ️ Arka plan yüzdeleri gerçekleşmiş kâr değildir. "
        "'Sıra belirsiz' kayıtları TP/SL olay sırası kanıtlanmadan başarılı sayılmaz.",
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
