import json
import os
import time
from datetime import datetime, timedelta, timezone

LEDGER_FILE = "trade_ledger.json"
TR_TIMEZONE = timezone(timedelta(hours=3))


def now_ts():
    return int(time.time())


def day_of(ts=None):
    dt = datetime.fromtimestamp(ts or now_ts(), TR_TIMEZONE)
    return dt.strftime("%Y-%m-%d")


def clock_of(ts):
    try:
        return datetime.fromtimestamp(
            int(ts),
            TR_TIMEZONE,
        ).strftime("%H:%M:%S")
    except Exception:
        return "--:--:--"


def load_ledger():
    try:
        if not os.path.exists(LEDGER_FILE):
            return {
                "trades": {},
                "last_update": 0,
            }

        with open(
            LEDGER_FILE,
            "r",
            encoding="utf-8",
        ) as f:
            data = json.load(f)

        if not isinstance(data, dict):
            data = {}

        data.setdefault("trades", {})
        data.setdefault("last_update", 0)

        return data

    except Exception as exc:
        print("trade_ledger okuma hatası:", exc)

        return {
            "trades": {},
            "last_update": 0,
        }


def save_ledger(data):
    try:
        with open(
            LEDGER_FILE,
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(
                data,
                f,
                indent=2,
                ensure_ascii=False,
            )

        return True

    except Exception as exc:
        print("trade_ledger kaydetme hatası:", exc)
        return False


def num(value, default=None):
    try:
        return float(value)
    except Exception:
        return default


def trade_id_of(signal):
    current = str(
        signal.get("trade_id") or ""
    ).strip()

    if current:
        return current

    opened_at = int(
        signal.get("opened_at")
        or now_ts()
    )

    return (
        f"{signal.get('symbol')}_"
        f"{signal.get('direction')}_"
        f"{signal.get('source', 'MTF')}_"
        f"{opened_at}"
    )


def ensure_trade(signal):
    ledger = load_ledger()
    trade_id = trade_id_of(signal)
    trades = ledger["trades"]

    if trade_id not in trades:
        opened_at = int(
            signal.get("opened_at")
            or now_ts()
        )

        trades[trade_id] = {
            "trade_id": trade_id,
            "symbol": signal.get("symbol"),
            "direction": signal.get("direction"),
            "source": signal.get(
                "source",
                "MTF",
            ),
            "entry": num(signal.get("entry")),
            "tp1": num(signal.get("tp1")),
            "tp2": num(signal.get("tp2")),
            "tp3": num(signal.get("tp3")),
            "sl": num(signal.get("sl")),
            "score": signal.get("score"),
            "risk_percent": signal.get(
                "risk_percent"
            ),
            "opened_at": opened_at,
            "opened_day": day_of(opened_at),
            "tp1_hit": bool(
                signal.get("tp1_hit", False)
            ),
            "tp2_hit": bool(
                signal.get("tp2_hit", False)
            ),
            "tp3_hit": bool(
                signal.get("tp3_hit", False)
            ),
            "status": "OPEN",
            "final_result": None,
            "r_result": None,
            "closed_at": None,
            "closed_day": None,
            "events": [
                {
                    "time": opened_at,
                    "event": "OPENED",
                    "price": num(
                        signal.get("entry")
                    ),
                }
            ],
        }

        ledger["last_update"] = now_ts()
        save_ledger(ledger)

    return trade_id


def open_trade(signal):
    return ensure_trade(signal)


def target_r(trade, target_name):
    entry = num(trade.get("entry"))
    sl = num(trade.get("sl"))
    target = num(trade.get(target_name))

    if (
        entry is None
        or sl is None
        or target is None
    ):
        return None

    risk = abs(entry - sl)

    if risk <= 0:
        return None

    return abs(target - entry) / risk


def record_event(
    signal,
    result,
    exit_price=None,
):
    trade_id = ensure_trade(signal)

    ledger = load_ledger()
    trade = ledger["trades"].get(
        trade_id
    )

    if trade is None:
        return

    result = str(result).upper()
    event_time = now_ts()

    duplicate_event = any(
        item.get("event") == result
        for item in trade["events"]
    )

    if not duplicate_event:
        trade["events"].append(
            {
                "time": event_time,
                "event": result,
                "price": num(exit_price),
            }
        )

    if result == "TP1":
        trade["tp1_hit"] = True

    elif result == "TP2":
        trade["tp1_hit"] = True
        trade["tp2_hit"] = True

    elif result == "TP3":
        trade["tp1_hit"] = True
        trade["tp2_hit"] = True
        trade["tp3_hit"] = True

    tp1_r = target_r(trade, "tp1")
    tp3_r = target_r(trade, "tp3")

    if result == "SL":
        trade["final_result"] = "SL"
        trade["r_result"] = -1.0

    elif result == "BE":
        if trade.get("tp2_hit"):
            trade["final_result"] = (
                "TP2_SONRASI_BE"
            )
        else:
            trade["final_result"] = (
                "TP1_SONRASI_BE"
            )

        trade["r_result"] = (
            round(
                0.50 * tp1_r,
                4,
            )
            if tp1_r is not None
            else 0.0
        )

    elif result == "TP3":
        trade["final_result"] = "TP3"

        if (
            tp1_r is not None
            and tp3_r is not None
        ):
            trade["r_result"] = round(
                0.50 * tp1_r
                + 0.50 * tp3_r,
                4,
            )
        else:
            trade["r_result"] = None

    elif result == "EXPIRED":
        trade["final_result"] = "EXPIRED"
        trade["r_result"] = None

    else:
        ledger["last_update"] = event_time
        save_ledger(ledger)
        return

    trade["status"] = "CLOSED"
    trade["exit_price"] = num(exit_price)
    trade["closed_at"] = event_time
    trade["closed_day"] = day_of(
        event_time
    )

    ledger["last_update"] = event_time
    save_ledger(ledger)


def build_daily_r_report():
    trades = list(
        load_ledger()
        .get("trades", {})
        .values()
    )

    today = day_of()

    opened = [
        trade
        for trade in trades
        if trade.get("opened_day") == today
    ]

    closed = [
        trade
        for trade in trades
        if trade.get("closed_day") == today
    ]

    measurable = [
        trade
        for trade in closed
        if trade.get("r_result") is not None
    ]

    open_total = sum(
        trade.get("status") == "OPEN"
        for trade in trades
    )

    net_r = round(
        sum(
            float(trade["r_result"])
            for trade in measurable
        ),
        3,
    )

    average_r = (
        round(
            net_r / len(measurable),
            3,
        )
        if measurable
        else 0.0
    )

    positive_count = sum(
        float(trade["r_result"]) > 0
        for trade in measurable
    )

    positive_rate = (
        round(
            positive_count
            / len(measurable)
            * 100,
            2,
        )
        if measurable
        else 0.0
    )

    result_counts = {
        "TP3": 0,
        "TP2_SONRASI_BE": 0,
        "TP1_SONRASI_BE": 0,
        "SL": 0,
        "EXPIRED": 0,
    }

    for trade in closed:
        result = trade.get(
            "final_result"
        )

        if result in result_counts:
            result_counts[result] += 1

    long_r = round(
        sum(
            float(trade["r_result"])
            for trade in measurable
            if trade.get("direction")
            == "LONG"
        ),
        3,
    )

    short_r = round(
        sum(
            float(trade["r_result"])
            for trade in measurable
            if trade.get("direction")
            == "SHORT"
        ),
        3,
    )

    ordered = sorted(
        closed,
        key=lambda trade: int(
            trade.get("closed_at")
            or 0
        ),
    )

    current_stop_streak = 0
    max_stop_streak = 0

    for trade in ordered:
        if (
            trade.get("final_result")
            == "SL"
        ):
            current_stop_streak += 1

            max_stop_streak = max(
                max_stop_streak,
                current_stop_streak,
            )
        else:
            current_stop_streak = 0

    labels = {
        "TP3": "TP3",
        "TP2_SONRASI_BE": (
            "TP2 sonrası BE"
        ),
        "TP1_SONRASI_BE": (
            "TP1 sonrası BE"
        ),
        "SL": "SL",
        "EXPIRED": "Süre doldu",
    }

    recent_lines = []

    for trade in ordered[-8:]:
        r_value = trade.get("r_result")

        if r_value is not None:
            r_text = (
                f"{float(r_value):+.3f}R"
            )
        else:
            r_text = "R yok"

        recent_lines.append(
            f"{clock_of(trade.get('closed_at'))}"
            f" | {trade.get('symbol')}"
            f" {trade.get('direction')}"
            f" → "
            f"{labels.get(
                trade.get('final_result'),
                trade.get('final_result'),
            )}"
            f" ({r_text})"
        )

    recent_text = (
        "\n".join(recent_lines)
        if recent_lines
        else "Bugün yeni v2 kapanışı yok."
    )

    return f"""📈 NET R PERFORMANS RAPORU v2

Tarih: {today}
Bugün Açılan Yeni Kayıt: {len(opened)}
Bugün Kapanan Yeni Kayıt: {len(closed)}
Toplam Açık Ledger Kaydı: {open_total}

🏁 TP3 ile Kapanan: {result_counts['TP3']}
✅ TP2 Sonrası BE: {result_counts['TP2_SONRASI_BE']}
✅ TP1 Sonrası BE: {result_counts['TP1_SONRASI_BE']}
❌ Doğrudan Stop: {result_counts['SL']}
⏳ Süresi Dolan: {result_counts['EXPIRED']}

📊 Ölçülebilir Kapanış: {len(measurable)}
📈 Net Sonuç: {net_r:+.3f}R
📉 İşlem Başına Ortalama: {average_r:+.3f}R
🎯 Pozitif Kapanış Oranı: %{positive_rate}
⚠️ En Uzun Stop Serisi: {max_stop_streak}

🟢 LONG Net: {long_r:+.3f}R
🔴 SHORT Net: {short_r:+.3f}R

Son Nihai Kapanışlar:
{recent_text}

Not: Yalnızca Performans v2 sistemine kaydedilen işlemler ölçülür.
TP1'de %50 kâr, kalan %50'nin TP3 veya girişten kapanması esas alınır."""