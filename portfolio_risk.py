"""
Dört bot için ortak portföy risk ve sinyal çakışma denetimi.

Bu modül emir açmaz ve mevcut state dosyalarını değiştirmez.
Ana MTF, Scalp, Pump/Dump ve Swing state JSON dosyalarını yalnızca
okuyarak yeni sinyalin mevcut açık sinyallerle çakışıp çakışmadığını
değerlendirir.
"""

import json
import os
import time


DEFAULT_STATE_SOURCES = {
    "MAIN_MTF": {
        "filename": "open_signals.json",
        "containers": [None],
    },
    "SCALP": {
        "filename": "scalp_radar_state.json",
        "containers": [
            "open_scalp_signals",
        ],
    },
    "PUMP_DUMP": {
        "filename": "pump_radar_state.json",
        "containers": [
            "open_signals",
            "open_pump_signals",
        ],
    },
    "SWING": {
        "filename": "swing_radar_state.json",
        "containers": [
            "open_swing_signals",
        ],
    },
    "NEW_LISTING": {
        "filename": "new_listing_performance_ledger.json",
        "containers": [
            "records",
        ],
        # Yalnız TP/SL içeren gerçek giriş onaylarını say.
        # Eski "izle" performans kayıtları portföy riski değildir.
        "required_record_type": "CONFIRMED_TRADE",
    },
}

DEFAULT_MAX_DIRECTION_RISK = 4.0
DEFAULT_MAX_TOTAL_RISK = 8.0


def normalize_symbol(symbol):
    """
    BTC/USDT:USDT, BTC-USDT ve BTCUSDT gibi biçimleri BTCUSDT'ye
    yaklaştırır.
    """
    value = str(symbol or "").upper().strip()

    if not value:
        return ""

    if "/" in value:
        base, remainder = value.split("/", 1)
        quote = remainder.split(":", 1)[0]
        value = base + quote

    value = (
        value.replace("-", "")
        .replace("_", "")
        .replace(":", "")
        .replace("/", "")
        .replace(" ", "")
    )

    return value


def safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_json_safely(filename):
    if not filename or not os.path.exists(filename):
        return {}

    try:
        with open(
            filename,
            "r",
            encoding="utf-8",
        ) as handle:
            loaded = json.load(handle)

        return (
            loaded
            if isinstance(loaded, dict)
            else {}
        )

    except Exception as exc:
        print(
            filename,
            "portföy risk dosyası okuma hatası:",
            exc,
        )
        return {}


def signal_is_open(signal):
    if not isinstance(signal, dict):
        return False

    if bool(signal.get("closed", False)):
        return False

    if bool(signal.get("tp3_hit", False)):
        return False

    status = str(
        signal.get("status", "")
    ).upper()

    if status in {
        "CLOSED",
        "EXPIRED",
        "CANCELLED",
        "FINAL",
    }:
        return False

    return bool(
        signal.get("symbol")
        and signal.get("direction")
    )


def signal_risk_weight(signal):
    """
    TP1 görülmüş işlemde kullanıcının %50 kâr aldığı varsayımına
    uygun olarak kalan risk ağırlığını 0.5 kabul eder.
    """
    if bool(signal.get("tp1_hit", False)):
        return 0.5

    return 1.0


def extract_container_signals(
    loaded,
    containers,
    required_record_type=None,
):
    results = []
    seen_keys = set()

    for container_name in containers:
        if container_name is None:
            candidate_container = loaded
        else:
            candidate_container = loaded.get(
                container_name,
                {},
            )

        if not isinstance(
            candidate_container,
            dict,
        ):
            continue

        for key, signal in (
            candidate_container.items()
        ):
            if not isinstance(signal, dict):
                continue

            if required_record_type:
                record_type = str(
                    signal.get("record_type") or ""
                ).upper()

                if (
                    record_type
                    != str(required_record_type).upper()
                ):
                    continue

            if not signal_is_open(signal):
                continue

            identity = (
                str(key),
                normalize_symbol(
                    signal.get("symbol")
                ),
                str(
                    signal.get("direction", "")
                ).upper(),
            )

            if identity in seen_keys:
                continue

            seen_keys.add(identity)
            results.append(signal)

    return results


def collect_open_portfolio(
    state_sources=None,
):
    sources = (
        state_sources
        if isinstance(state_sources, dict)
        else DEFAULT_STATE_SOURCES
    )

    records = []

    for bot_name, source in sources.items():
        filename = source.get("filename")
        containers = source.get(
            "containers",
            [None],
        )
        required_record_type = source.get(
            "required_record_type"
        )

        loaded = load_json_safely(
            filename
        )

        for signal in extract_container_signals(
            loaded,
            containers,
            required_record_type=(
                required_record_type
            ),
        ):
            records.append({
                "bot": str(bot_name),
                "symbol": normalize_symbol(
                    signal.get("symbol")
                ),
                "direction": str(
                    signal.get("direction", "")
                ).upper(),
                "source": (
                    signal.get("source")
                    or signal.get("alert_type")
                    or signal.get("record_type")
                ),
                "entry": safe_float(
                    signal.get("entry"),
                    None,
                ),
                "risk_percent": safe_float(
                    (
                        signal.get("risk_percent")
                        if signal.get("risk_percent")
                        is not None
                        else signal.get("stop_percent")
                    ),
                    None,
                ),
                "tp1_hit": bool(
                    signal.get("tp1_hit", False)
                ),
                "risk_weight": (
                    signal_risk_weight(signal)
                ),
                "opened_at": int(
                    safe_float(
                        (
                            signal.get("opened_at")
                            if signal.get("opened_at")
                            is not None
                            else signal.get("sent_at")
                        ),
                        0,
                    )
                    or 0
                ),
            })

    return records


def evaluate_portfolio_risk(
    symbol,
    direction,
    source_bot,
    state_sources=None,
    max_direction_risk=(
        DEFAULT_MAX_DIRECTION_RISK
    ),
    max_total_risk=DEFAULT_MAX_TOTAL_RISK,
):
    normalized_symbol = normalize_symbol(
        symbol
    )
    normalized_direction = str(
        direction or ""
    ).upper()
    normalized_bot = str(
        source_bot or "UNKNOWN"
    ).upper()

    open_records = collect_open_portfolio(
        state_sources=state_sources
    )

    same_symbol = [
        item
        for item in open_records
        if item["symbol"]
        == normalized_symbol
    ]

    same_symbol_same_direction = [
        item
        for item in same_symbol
        if item["direction"]
        == normalized_direction
    ]

    same_symbol_opposite = [
        item
        for item in same_symbol
        if item["direction"]
        and item["direction"]
        != normalized_direction
    ]

    total_risk_before = round(
        sum(
            item["risk_weight"]
            for item in open_records
        ),
        2,
    )

    direction_risk_before = round(
        sum(
            item["risk_weight"]
            for item in open_records
            if item["direction"]
            == normalized_direction
        ),
        2,
    )

    total_risk_after = round(
        total_risk_before + 1.0,
        2,
    )
    direction_risk_after = round(
        direction_risk_before + 1.0,
        2,
    )

    hard_block = False
    block_code = None
    block_reason = None

    if same_symbol_opposite:
        hard_block = True
        block_code = (
            "SAME_COIN_OPPOSITE_DIRECTION"
        )
        bots = ", ".join(
            sorted({
                item["bot"]
                for item in same_symbol_opposite
            })
        )
        block_reason = (
            f"{normalized_symbol} başka botta "
            f"ters yönde açık: {bots}."
        )

    elif same_symbol_same_direction:
        hard_block = True
        block_code = (
            "SAME_COIN_SAME_DIRECTION"
        )
        bots = ", ".join(
            sorted({
                item["bot"]
                for item
                in same_symbol_same_direction
            })
        )
        block_reason = (
            f"{normalized_symbol} aynı yönde "
            f"başka açık sinyalle çakışıyor: "
            f"{bots}."
        )

    warnings = []

    if (
        direction_risk_after
        > float(max_direction_risk)
    ):
        warnings.append(
            f"{normalized_direction} yön ağırlığı "
            f"{direction_risk_after:.1f}/"
            f"{float(max_direction_risk):.1f}"
        )

    if (
        total_risk_after
        > float(max_total_risk)
    ):
        warnings.append(
            f"toplam açık risk ağırlığı "
            f"{total_risk_after:.1f}/"
            f"{float(max_total_risk):.1f}"
        )

    return {
        "version": "PORTFOLIO_RISK_V2_NEW_LISTING",
        "checked_at": int(time.time()),
        "candidate": {
            "bot": normalized_bot,
            "symbol": normalized_symbol,
            "direction": normalized_direction,
        },
        "hard_block": hard_block,
        "block_code": block_code,
        "block_reason": block_reason,
        "warnings": warnings,
        "has_soft_warning": bool(warnings),
        "open_signal_count": len(
            open_records
        ),
        "total_risk_before": (
            total_risk_before
        ),
        "total_risk_after": (
            total_risk_after
        ),
        "direction_risk_before": (
            direction_risk_before
        ),
        "direction_risk_after": (
            direction_risk_after
        ),
        "same_symbol_records": (
            same_symbol
        ),
    }


def format_portfolio_note(result):
    if not isinstance(result, dict):
        return ""

    if result.get("hard_block"):
        return (
            "⛔ Portföy Çakışması: "
            + str(
                result.get("block_reason")
                or "Aynı coinde açık risk var."
            )
        )

    warnings = result.get("warnings") or []

    if not warnings:
        return ""

    return (
        "⚠️ Portföy Yoğunluk Uyarısı: "
        + "; ".join(warnings)
        + "."
    )
