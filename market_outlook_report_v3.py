"""Clear, detailed-but-readable Telegram formatter for Market Outlook V3."""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable

from market_outlook_research_v3 import derive_research
from market_outlook_report_v2 import scenario_weights

VERSION = "MARKET_OUTLOOK_REPORT_V3_1_2026_08_23"


def sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def fmt_price(value: Any) -> str:
    price = sf(value)
    if price >= 1000:
        return f"{price:,.0f}"
    if price >= 10:
        return f"{price:.2f}"
    if price >= 1:
        return f"{price:.4f}"
    return f"{price:.8f}".rstrip("0").rstrip(".")


def fmt_pct(value: Any, digits: int = 2) -> str:
    return f"%{sf(value):+.{digits}f}"


def _symbol(symbol: Any) -> str:
    return str(symbol or "").upper().replace("USDT", "")


def _movers(rows: Iterable[Dict[str, Any]], limit: int = 3) -> str:
    items = []
    for row in list(rows or [])[:limit]:
        items.append(f"{_symbol(row.get('symbol'))} {fmt_pct(row.get('change'))}")
    return " | ".join(items) if items else "veri yok"


def _accuracy(state: Dict[str, Any], horizon: str) -> str:
    item = (state.get("accuracy") or {}).get(horizon) or {}
    sample = int(sf(item.get("sample")))
    accuracy = item.get("accuracy_percent")
    if accuracy is None or sample < 5:
        return f"veri birikiyor ({sample} sonuç)"
    return f"%{sf(accuracy):.1f} ({sample} sonuç)"


def _level_distance(price: float, level: Any) -> str:
    value = sf(level)
    if price <= 0 or value <= 0:
        return fmt_price(value)
    pct = (value - price) / price * 100.0
    return f"{fmt_price(value)} ({pct:+.2f}%)"


def _strategy_text(snapshot: Dict[str, Any], research: Dict[str, Any]) -> str:
    outlook = snapshot.get("outlook") or {}
    direction = str(outlook.get("direction_24h") or "FLAT").upper()
    breadth_up = sf((snapshot.get("breadth") or {}).get("up_pct"))
    heat = (research.get("heat") or {}).get("label")

    if direction == "UP":
        if breadth_up >= 55:
            base = "LONG tarafı öncelikli; fakat kırılımı kovalamak yerine geri test + Premium teyit daha sağlıklı."
        else:
            base = "Majörlerde LONG tarafı avantajlı; altcoinlerde genelleme yerine seçici Premium teyit gerekli."
        if heat in ("ısınmış", "çok ısınmış"):
            base += " Piyasa ısındığı için acele giriş yerine geri çekilmeyi beklemek önemli."
        return base
    if direction == "DOWN":
        if breadth_up <= 45:
            return "SHORT tarafı öncelikli; tepki yükselişlerinde teyitsiz giriş yerine yapı + Premium teyit beklenmeli."
        return "Majör yapı aşağı olsa da altcoin direnci var; SHORT tarafında seçicilik önemli."
    return "Net yön yok; kırılım gelmeden taraf zorlamak yerine Premium teyit beklemek daha sağlıklı."


def build_message(snapshot: Dict[str, Any], state: Dict[str, Any]) -> str:
    refs = snapshot.get("references") or {}
    btc = refs.get("BTCUSDT") or {}
    eth = refs.get("ETHUSDT") or {}
    sol = refs.get("SOLUSDT") or {}
    outlook = snapshot.get("outlook") or {}
    breadth = snapshot.get("breadth") or {}
    levels = btc.get("levels") or {}
    research = derive_research(snapshot, state)

    up_w, flat_w, down_w = scenario_weights(outlook)
    price = sf(btc.get("price"))
    b6 = (research.get("changes") or {}).get("breadth_6h")
    btc6 = (research.get("changes") or {}).get("btc_6h")
    history_note = (
        f"{research.get('snapshot_count', 0)} snapshot / ~{research.get('history_hours', 0):.1f}s geçmiş"
        if research.get("snapshot_count") else "geçmiş veri birikiyor"
    )

    change_parts = []
    if btc6 is not None:
        change_parts.append(f"BTC ~6S {btc6:+.2f}%")
    if b6 is not None:
        change_parts.append(f"yükselen coin oranı ~6S {b6:+.1f} puan")
    change_text = " | ".join(change_parts) if change_parts else "zaman karşılaştırması için veri birikiyor"

    risks = research.get("risks") or []
    risk_lines = "\n".join(f"• {item}" for item in risks[:3])

    derivative = research.get("derivatives") or {}
    derivative_text = f"{derivative.get('funding_label', 'funding veri yok')}; {derivative.get('oi_label', 'OI veri yok')}."

    strategy = _strategy_text(snapshot, research)

    message = (
        "🌍 GENEL PİYASA DEĞERLENDİRMESİ\n\n"
        f"🧭 GENEL GÖRÜNÜM: {outlook.get('bias_24h')} | Güven %{int(sf(outlook.get('confidence_24h')))}\n"
        f"🧠 Piyasa ne yapıyor? {research.get('pulse')}\n"
        f"{research.get('narrative')}\n\n"
        f"⚖️ 24S senaryo: ↑ %{up_w} | ↔ %{flat_w} | ↓ %{down_w}\n"
        f"⏱ Son birkaç saat: {change_text}\n\n"
        "₿ BTC YOL HARİTASI\n"
        f"Şimdi: {fmt_price(price)}\n"
        f"🟢 Yukarı: {_level_distance(price, levels.get('resistance1'))} üstünde kalıcılık → {_level_distance(price, levels.get('resistance2'))}\n"
        f"🟡 Düzeltme: {_level_distance(price, levels.get('support1'))} altı → {_level_distance(price, levels.get('support2'))} riski\n"
        f"🔴 Görüş bozulur: {_level_distance(price, levels.get('support2'))} altında kalıcılık\n\n"
        "🌐 ALTCOINLER\n"
        f"Yükselen %{sf(breadth.get('up_pct')):.1f} | Düşen %{sf(breadth.get('down_pct')):.1f} | Medyan {fmt_pct(breadth.get('median_change'))}\n"
        f"📈 İç yapı: {research.get('leadership', {}).get('label', 'veri birikiyor')}\n"
        f"🔥 Güçlüler: {_movers(breadth.get('top') or [])}\n"
        f"🧊 Zayıflar: {_movers(breadth.get('bottom') or [], 2)}\n\n"
        "💸 VADELİ PİYASA\n"
        f"{derivative_text}\n\n"
        "⚠️ ANA RİSKLER\n"
        f"{risk_lines}\n\n"
        "🎯 BUGÜNÜN YAKLAŞIMI\n"
        f"{strategy}\n\n"
        "🧪 MODEL TAKİBİ\n"
        f"6S isabet: {_accuracy(state, '6h')} | 24S isabet: {_accuracy(state, '24h')}\n"
        f"Arka plan araştırması: {history_note} | tarama 2 saatte bir\n\n"
        f"💰 BTC {fmt_price(btc.get('price'))} | ETH {fmt_price(eth.get('price'))} | SOL {fmt_price(sol.get('price'))}\n"
        "📌 Senaryo yüzdeleri model ağırlığıdır; kesin fiyat olasılığı değildir."
    )

    if len(message) > 3900:
        message = message[:3820].rstrip() + "\n\n📌 Rapor güvenli uzunluk sınırında kısaltıldı."
    return message
