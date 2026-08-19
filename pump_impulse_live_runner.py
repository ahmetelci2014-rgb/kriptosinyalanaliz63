"""Live Pump entry point with all-market impulse prioritisation.

This wrapper adds a single bulk-ticker market pulse before the existing Pump
runner. Fast movers across the whole active USDT perpetual universe are moved
to the front of the deep scan even when they are outside the normal top-volume
slice. A very strong pulse may emit one early WATCH message, but real entries
still come only from the existing Pump/Trend-Continuation quality paths.
"""
from __future__ import annotations

from typing import Any, Dict

import live_entry_safety as safety
import market_impulse_guard as impulse
import pump_live_runner as live

VERY_STRONG_ALERT_COOLDOWN_MINUTES = 30


def _format_move(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"%{float(value):+.2f}"
    except Exception:
        return "—"


def maybe_send_very_strong_alert(radar: Any, state: Dict[str, Any]) -> int:
    item = impulse.strongest_very_strong_impulse(state)
    if not item:
        return 0

    last_sent = state.setdefault("last_alert_sent", {})
    key = f"{item.get('symbol')}_{item.get('direction')}"
    now = radar.now_ts()
    cooldown = VERY_STRONG_ALERT_COOLDOWN_MINUTES * 60
    if now - int(last_sent.get(key, 0) or 0) < cooldown:
        return 0

    direction = str(item.get("direction") or "").upper()
    icon = "🟢" if direction == "LONG" else "🔴"
    message = (
        "⚠️ SADECE TAKİP — HENÜZ İŞLEM AÇMA\n"
        "Gerçek giriş ayrıca Giriş + TP + SL ile gelecek.\n\n"
        "⚡ TÜM PİYASA CANLI İMPULS\n\n"
        f"{icon} {direction} | {item.get('symbol')}\n"
        f"Canlı fiyat: {radar.format_price(item.get('price'))}\n"
        f"5M hareket: {_format_move(item.get('move5_percent'))}\n"
        f"15M hareket: {_format_move(item.get('move15_percent'))}\n"
        f"30M hareket: {_format_move(item.get('move30_percent'))}\n"
        f"24s hacim: {float(item.get('quote_volume') or 0):,.0f} USDT\n\n"
        "📌 Bu katman mum kapanışını beklemeden tüm aktif USDT perpetual evrenindeki "
        "hızlanmayı yakalar ve coini detay taramasının önüne taşır.\n"
        "⚠️ Tek başına giriş değildir; hareketin peşinden koşma."
    )
    bucket = now // max(1, cooldown)
    if radar.send_telegram(message, delivery_key=f"MARKET_IMPULSE|{key}|{bucket}"):
        last_sent[key] = now
        impulse.atomic_save_json(impulse.STATE_FILE, state)
        return 1
    return 0


def run(radar: Any | None = None) -> None:
    if radar is None:
        import pump_radar as radar  # type: ignore[no-redef]

    exchange = radar.get_exchange()
    state = impulse.update_market_impulse_state(exchange)
    scan_coins = impulse.scan_universe_from_state(
        state,
        normal_min_quote_volume=radar.MIN_24H_QUOTE_VOLUME,
        normal_max_scan_coins=radar.MAX_SCAN_COINS,
    )
    priority_count = len(impulse.priority_symbols(state))

    # Reuse the same exchange and cached universe for the normal Pump run.
    radar.get_exchange = lambda: exchange
    radar.get_scan_coins = lambda _exchange: list(scan_coins)

    # Raw impulse warning is informational. Real entry messages below receive
    # an explicit stop/position-discipline footer before Telegram delivery.
    sent_alert = maybe_send_very_strong_alert(radar, state)
    radar.send_telegram = safety.make_entry_safety_sender(radar.send_telegram)

    print(
        "Tüm piyasa impuls katmanı AKTİF | derin tarama:", len(scan_coins),
        "| öncelikli:", priority_count, "| canlı impuls Telegram:", sent_alert,
    )
    live.run(radar)


if __name__ == "__main__":
    run()
