"""Detailed Telegram formatter for Market Outlook.

This module only changes how the daily market outlook is explained to the user.
It does not alter market scoring, forecasts, Premium trade filters, TP/SL, or
exchange-order behavior.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, Optional, Tuple


VERSION = "MARKET_OUTLOOK_REPORT_V2_2026_08_23"


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _fmt_price(value: Any) -> str:
    price = _sf(value)
    if price >= 1000:
        return f"{price:,.0f}"
    if price >= 10:
        return f"{price:.2f}"
    if price >= 1:
        return f"{price:.4f}"
    return f"{price:.8f}".rstrip("0").rstrip(".")


def _fmt_pct(value: Any, digits: int = 2) -> str:
    return f"%{_sf(value):+.{digits}f}"


def _trend_badge(score: Any) -> str:
    value = _sf(score)
    if value >= 45:
        return "🟢 GÜÇLÜ ↑"
    if value >= 20:
        return "🟢 ↑"
    if value <= -45:
        return "🔴 GÜÇLÜ ↓"
    if value <= -20:
        return "🔴 ↓"
    return "⚪ YATAY"


def _symbol_name(symbol: str) -> str:
    return str(symbol or "").upper().replace("USDT", "")


def _join_movers(rows: Iterable[Dict[str, Any]], limit: int = 3) -> str:
    items = []
    for row in list(rows or [])[:limit]:
        items.append(f"{_symbol_name(row.get('symbol'))} {_fmt_pct(row.get('change'))}")
    return " | ".join(items) if items else "veri yok"


def scenario_weights(outlook: Dict[str, Any]) -> Tuple[int, int, int]:
    """Return uncalibrated model scenario weights, not statistical probabilities."""
    score = max(-100.0, min(100.0, _sf(outlook.get("score_24h"))))
    confidence = max(45.0, min(90.0, _sf(outlook.get("confidence_24h"), 50.0)))

    # Direction comes from the existing Market Outlook score. Confidence controls
    # how much weight moves away from the neutral scenario.
    directional = score * (0.20 + (confidence - 45.0) / 450.0)
    up = 34.0 + directional
    down = 34.0 - directional
    flat = max(18.0, 32.0 - abs(score) * 0.08)

    up, down, flat = max(5.0, up), max(5.0, down), max(12.0, flat)
    total = up + down + flat
    values = [int(round(up / total * 100)), int(round(flat / total * 100)), int(round(down / total * 100))]
    values[0] += 100 - sum(values)
    return values[0], values[1], values[2]


def _distance(price: float, level: Any) -> Optional[float]:
    target = _sf(level)
    if price <= 0 or target <= 0:
        return None
    return (target - price) / price * 100.0


def _level_text(price: float, level: Any) -> str:
    value = _sf(level)
    distance = _distance(price, value)
    if value <= 0 or distance is None:
        return "veri yok"
    return f"{_fmt_price(value)} ({distance:+.2f}%)"


def _phase(outlook: Dict[str, Any], breadth: Dict[str, Any], btc: Dict[str, Any]) -> str:
    direction = str(outlook.get("direction_24h") or "FLAT").upper()
    up = _sf(breadth.get("up_pct"))
    down = _sf(breadth.get("down_pct"))
    median = _sf(breadth.get("median_change"))
    rsi = _sf(btc.get("rsi_4h"), 50.0)

    if direction == "UP" and (up < 45 or median < 0):
        base = "SEÇİCİ MAJÖR RALLİSİ — BTC/majörler güçlü, altcoin geneli geride"
    elif direction == "UP" and up >= 55:
        base = "GENİŞ TABANLI RİSK-ON — yükseliş altcoin geneline yayılıyor"
    elif direction == "DOWN" and down >= 55:
        base = "GENİŞ TABANLI RİSK-OFF — satış baskısı piyasa geneline yayılıyor"
    elif direction == "DOWN":
        base = "AŞAĞI EĞİLİMLİ — majörler zayıf fakat satış genişliği tam değil"
    else:
        base = "KONSOLİDASYON — piyasa yön seçmeye çalışıyor"

    if rsi >= 70 and direction == "UP":
        base += "; 4H aşırı ısınma nedeniyle düzeltme riski yüksek"
    elif rsi <= 30 and direction == "DOWN":
        base += "; 4H aşırı satış nedeniyle tepki riski yüksek"
    return base


def _scenario_text(snapshot: Dict[str, Any]) -> Tuple[str, str, str]:
    refs = snapshot.get("references") or {}
    outlook = snapshot.get("outlook") or {}
    btc = refs.get("BTCUSDT") or {}
    price = _sf(btc.get("price"))
    levels = btc.get("levels") or {}
    direction = str(outlook.get("direction_24h") or "FLAT").upper()

    s1 = _level_text(price, levels.get("support1"))
    s2 = _level_text(price, levels.get("support2"))
    r1 = _level_text(price, levels.get("resistance1"))
    r2 = _level_text(price, levels.get("resistance2"))
    macro_s = _level_text(price, levels.get("macro_support"))
    macro_r = _level_text(price, levels.get("macro_resistance"))

    if direction == "UP":
        main = f"Ana rota: {r1} üstünde kabul → {r2}; devamında makro direnç {macro_r}."
        alt = f"Düzeltme rotası: {s1} kaybedilirse {s2} test riski artar."
        invalid = f"24S yükseliş görüşü özellikle {s2} altındaki kalıcılıkta ciddi zayıflar; makro savunma {macro_s}."
    elif direction == "DOWN":
        main = f"Ana rota: {s1} altı kabul → {s2}; devamında makro destek {macro_s}."
        alt = f"Tepki rotası: {r1} geri alınırsa {r2} test ihtimali yükselir."
        invalid = f"24S düşüş görüşü özellikle {r2} üstündeki kalıcılıkta ciddi zayıflar; makro tavan {macro_r}."
    else:
        main = f"Ana rota: {s1}–{r1} bandında sıkışma; kırılan taraf kısa vadeli yönü belirler."
        alt = f"Yukarı kırılımda {r2}; aşağı kırılımda {s2} sonraki izleme bölgesi."
        invalid = f"Makro sınırlar: destek {macro_s} / direnç {macro_r}."
    return main, alt, invalid


def _derivative_line(snapshot: Dict[str, Any]) -> Tuple[str, str]:
    derivatives = snapshot.get("derivatives") or {}
    funding = derivatives.get("funding") or {}
    oi_change = derivatives.get("oi_change_since_last_run_percent") or {}

    funding_parts = []
    oi_parts = []
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        name = _symbol_name(symbol)
        rate = funding.get(symbol)
        if rate is not None:
            funding_parts.append(f"{name} {_sf(rate) * 100:+.4f}%")
        change = oi_change.get(symbol)
        if change is not None:
            oi_parts.append(f"{name} {_sf(change):+.2f}%")
    return (
        " | ".join(funding_parts) if funding_parts else "veri yok",
        " | ".join(oi_parts) if oi_parts else "ilk snapshot / veri yok",
    )


def _accuracy_text(state: Dict[str, Any], horizon: str) -> str:
    item = (state.get("accuracy") or {}).get(horizon) or {}
    sample = int(_sf(item.get("sample")))
    accuracy = item.get("accuracy_percent")
    if accuracy is None or sample < 5:
        return f"veri birikiyor ({sample} sonuç)"
    return f"%{_sf(accuracy):.1f} ({sample} sonuç)"


def _watch_items(snapshot: Dict[str, Any]) -> Tuple[str, str, str]:
    refs = snapshot.get("references") or {}
    outlook = snapshot.get("outlook") or {}
    breadth = snapshot.get("breadth") or {}
    btc = refs.get("BTCUSDT") or {}
    flags = list(outlook.get("risk_flags") or [])

    items = []
    if _sf(breadth.get("down_pct")) > _sf(breadth.get("up_pct")) and str(outlook.get("direction_24h")) == "UP":
        items.append("BTC/majör yükselişi altcoin geneline yayılmadan agresif altcoin LONG kovalamamak.")
    if _sf(btc.get("rsi_4h"), 50.0) >= 70:
        items.append("BTC 4H RSI yüksek: direnç kırılımında bile geri test/düzeltme ihtimalini hesaba katmak.")
    if flags:
        items.append("Risk bayrakları: " + " | ".join(flags[:2]) + ".")
    if not items:
        items.append("BTC ana destek/direnç kırılımlarında breadth ve hacmin aynı yönde teyidini beklemek.")
    items.append(f"LONG/SHORT denge: {int(_sf(outlook.get('long_suitability')))} / {int(_sf(outlook.get('short_suitability')))}.")
    items.append("Tek bir seviyeyi kesin hedef değil, senaryo geçiş noktası olarak kullanmak.")
    return items[0], items[1], items[2]


def build_message(snapshot: Dict[str, Any], state: Dict[str, Any]) -> str:
    refs = snapshot.get("references") or {}
    outlook = snapshot.get("outlook") or {}
    breadth = snapshot.get("breadth") or {}
    btc = refs.get("BTCUSDT") or {}
    eth = refs.get("ETHUSDT") or {}
    sol = refs.get("SOLUSDT") or {}
    levels = btc.get("levels") or {}

    up_w, flat_w, down_w = scenario_weights(outlook)
    main_scenario, alt_scenario, invalidation = _scenario_text(snapshot)
    funding_line, oi_line = _derivative_line(snapshot)
    watch1, watch2, watch3 = _watch_items(snapshot)

    def tf_line(label: str, ref: Dict[str, Any]) -> str:
        scores = ref.get("scores") or {}
        return (
            f"{label}: 15M {_trend_badge(scores.get('15m'))} | "
            f"1H {_trend_badge(scores.get('1h'))} | "
            f"4H {_trend_badge(scores.get('4h'))} | "
            f"1D {_trend_badge(scores.get('1d'))}"
        )

    flags = outlook.get("risk_flags") or []
    risk_text = "Yok" if not flags else " | ".join(flags[:4])
    eligible = int(_sf(breadth.get("eligible")))

    message = (
        "🌍 GENEL PİYASA DEĞERLENDİRMESİ — V2\n\n"
        f"🧠 Piyasa rejimi: {_phase(outlook, breadth, btc)}\n"
        f"🧭 6 Saat: {outlook.get('bias_6h')} | Güven %{int(_sf(outlook.get('confidence_6h')))}\n"
        f"🗓 24 Saat: {outlook.get('bias_24h')} | Güven %{int(_sf(outlook.get('confidence_24h')))}\n"
        f"⚖️ 24S model senaryo ağırlığı: ↑ %{up_w} | ↔ %{flat_w} | ↓ %{down_w}\n\n"
        "🔎 ÇOKLU ZAMAN DİLİMİ\n"
        f"{tf_line('₿ BTC', btc)}\n"
        f"{tf_line('Ξ ETH', eth)}\n"
        f"{tf_line('◎ SOL', sol)}\n\n"
        f"💰 Fiyatlar: BTC {_fmt_price(btc.get('price'))} | ETH {_fmt_price(eth.get('price'))} | SOL {_fmt_price(sol.get('price'))}\n"
        f"🌡 BTC 4H RSI: {_sf(btc.get('rsi_4h')):.1f} | ATR: %{_sf(btc.get('atr_4h_percent')):.2f}\n\n"
        "🗺️ BTC SENARYO HARİTASI\n"
        f"🟢 {main_scenario}\n"
        f"🟡 {alt_scenario}\n"
        f"🔴 Görüş bozulması: {invalidation}\n"
        f"🛡 Yakın destek: {_fmt_price(levels.get('support1'))} / {_fmt_price(levels.get('support2'))}\n"
        f"🚧 Yakın direnç: {_fmt_price(levels.get('resistance1'))} / {_fmt_price(levels.get('resistance2'))}\n\n"
        f"📊 ALTCOIN BREADTH ({eligible} coin)\n"
        f"Yükselen %{_sf(breadth.get('up_pct')):.1f} | Düşen %{_sf(breadth.get('down_pct')):.1f} | Yatay %{_sf(breadth.get('flat_pct')):.1f}\n"
        f"Medyan 24S {_fmt_pct(breadth.get('median_change'))} | Hacim ağırlıklı {_fmt_pct(breadth.get('volume_weighted_change'))}\n"
        f"🔥 Güçlüler: {_join_movers(breadth.get('top') or [])}\n"
        f"🧊 Zayıflar: {_join_movers(breadth.get('bottom') or [])}\n\n"
        "💸 TÜREV PİYASA\n"
        f"Funding: {funding_line}\n"
        f"OI değişimi (son snapshot): {oi_line}\n"
        f"⚠️ Risk bayrakları: {risk_text}\n\n"
        "🎯 BUGÜN İÇİN YAKLAŞIM\n"
        f"🟢 LONG uygunluğu: {int(_sf(outlook.get('long_suitability')))}/10 | 🔴 SHORT: {int(_sf(outlook.get('short_suitability')))}/10\n"
        f"• {watch1}\n"
        f"• {watch2}\n"
        f"• {watch3}\n\n"
        "🧪 MODEL TAKİBİ\n"
        f"6S yön isabeti: {_accuracy_text(state, '6h')}\n"
        f"24S yön isabeti: {_accuracy_text(state, '24h')}\n\n"
        "📌 Senaryo ağırlıkları kalibre edilmiş olasılık değildir. Rapor kesin fiyat tahmini değil; yön, seviye, risk ve görüş-bozulma haritasıdır."
    )

    # Telegram text limit is 4096 characters. Keep a safety margin if future labels grow.
    if len(message) > 3900:
        message = message[:3820].rstrip() + "\n\n📌 Rapor güvenli uzunluk sınırında kısaltıldı."
    return message
