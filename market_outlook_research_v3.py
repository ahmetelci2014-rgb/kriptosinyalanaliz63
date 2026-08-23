"""Deep background research layer for Market Outlook V3.

Uses the current OKX snapshot plus the rolling Market Outlook snapshot history to
measure *change through time*, not only the current market picture. It does not
alter Premium trade filters, TP/SL, forecasts, or exchange-order behavior.

The workflow runs every two hours to keep GitHub Actions usage low. Therefore the
historical comparisons are aligned to roughly 2H / 6H / 12H windows instead of
short noisy intervals.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

VERSION = "MARKET_OUTLOOK_RESEARCH_V3_1_2026_08_23"


def sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _history(state: Dict[str, Any], current_ts: int) -> List[Dict[str, Any]]:
    rows = [
        row for row in (state.get("snapshots") or [])
        if isinstance(row, dict) and int(row.get("ts") or 0) > 0 and int(row.get("ts") or 0) <= current_ts
    ]
    rows.sort(key=lambda row: int(row.get("ts") or 0))
    return rows


def _past_snapshot(rows: List[Dict[str, Any]], current_ts: int, hours: float) -> Optional[Dict[str, Any]]:
    target = current_ts - int(hours * 3600)
    candidates = [row for row in rows if int(row.get("ts") or 0) <= target]
    return candidates[-1] if candidates else None


def _price(snapshot: Optional[Dict[str, Any]], symbol: str) -> Optional[float]:
    if not snapshot:
        return None
    value = (((snapshot.get("references") or {}).get(symbol) or {}).get("price"))
    price = sf(value, 0.0)
    return price if price > 0 else None


def _pct_change(now: Optional[float], old: Optional[float]) -> Optional[float]:
    if now is None or old is None or old <= 0:
        return None
    return round((now - old) / old * 100.0, 3)


def _breadth_up(snapshot: Optional[Dict[str, Any]]) -> Optional[float]:
    if not snapshot:
        return None
    try:
        value = float((snapshot.get("breadth") or {}).get("up_pct"))
        return value if math.isfinite(value) else None
    except Exception:
        return None


def _score(snapshot: Optional[Dict[str, Any]], key: str) -> Optional[float]:
    if not snapshot:
        return None
    value = (snapshot.get("outlook") or {}).get(key)
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except Exception:
        return None


def _delta(now: Optional[float], old: Optional[float]) -> Optional[float]:
    if now is None or old is None:
        return None
    return round(now - old, 2)


def _direction_counts(snapshot: Dict[str, Any]) -> Dict[str, int]:
    up = down = flat = 0
    refs = snapshot.get("references") or {}
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        scores = ((refs.get(symbol) or {}).get("scores") or {})
        for timeframe in ("15m", "1h", "4h", "1d"):
            value = sf(scores.get(timeframe))
            if value >= 20:
                up += 1
            elif value <= -20:
                down += 1
            else:
                flat += 1
    return {"up": up, "down": down, "flat": flat, "total": up + down + flat}


def _mover_symbols(snapshot: Optional[Dict[str, Any]], key: str, limit: int = 5) -> List[str]:
    if not snapshot:
        return []
    rows = (snapshot.get("breadth") or {}).get(key) or []
    result = []
    for row in rows[:limit]:
        if isinstance(row, dict):
            symbol = str(row.get("symbol") or "").upper()
            if symbol:
                result.append(symbol)
    return result


def _leadership(snapshot: Dict[str, Any], past: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    now_top = set(_mover_symbols(snapshot, "top"))
    old_top = set(_mover_symbols(past, "top"))
    if not old_top:
        return {"label": "veri birikiyor", "overlap": None}
    overlap = len(now_top & old_top)
    if overlap >= 3:
        label = "lider coinler korunuyor; momentum sürekliliği var"
    elif overlap == 2:
        label = "liderlik kısmen korunuyor; sağlıklı rotasyon var"
    else:
        label = "liderlik hızlı değişiyor; seçicilik ve kovalamama önemli"
    return {"label": label, "overlap": overlap}


def _derivatives(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    derivatives = snapshot.get("derivatives") or {}
    funding_avg = derivatives.get("funding_average")
    funding = None if funding_avg is None else sf(funding_avg)
    oi = derivatives.get("oi_change_since_last_run_percent") or {}
    oi_values = []
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        value = oi.get(symbol)
        if value is None:
            continue
        try:
            number = float(value)
            if math.isfinite(number):
                oi_values.append(number)
        except Exception:
            continue
    oi_avg = round(sum(oi_values) / len(oi_values), 3) if oi_values else None

    if funding is None:
        funding_label = "funding verisi eksik"
    elif funding >= 0.0005:
        funding_label = "LONG tarafı kalabalık; squeeze/geri çekilme riski artmış"
    elif funding <= -0.0005:
        funding_label = "SHORT tarafı kalabalık; yukarı squeeze riski artmış"
    elif funding > 0:
        funding_label = "funding pozitif ama normal; aşırı LONG kalabalığı yok"
    elif funding < 0:
        funding_label = "funding negatif ama normal; aşırı SHORT kalabalığı yok"
    else:
        funding_label = "funding nötr"

    if oi_avg is None:
        oi_label = "OI geçmişi henüz yetersiz"
    elif oi_avg >= 1.0:
        oi_label = "açık pozisyonlar hızlı artıyor; kaldıraç birikimi yükseliyor"
    elif oi_avg >= 0.25:
        oi_label = "açık pozisyonlar ılımlı artıyor"
    elif oi_avg <= -1.0:
        oi_label = "açık pozisyonlar hızlı azalıyor; kaldıraç temizleniyor"
    elif oi_avg <= -0.25:
        oi_label = "açık pozisyonlar ılımlı azalıyor"
    else:
        oi_label = "açık pozisyonlar yatay; kaldıraç baskısı sınırlı"

    return {
        "funding_average": funding,
        "funding_label": funding_label,
        "oi_average_change": oi_avg,
        "oi_label": oi_label,
    }


def _heat(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    btc = (snapshot.get("references") or {}).get("BTCUSDT") or {}
    rsi = sf(btc.get("rsi_4h"), 50.0)
    atr = sf(btc.get("atr_4h_percent"), 0.0)
    if rsi >= 75:
        label = "çok ısınmış"
    elif rsi >= 70:
        label = "ısınmış"
    elif rsi <= 30:
        label = "aşırı satım bölgesine yakın"
    else:
        label = "normal"
    return {"rsi_4h": round(rsi, 2), "atr_4h_percent": round(atr, 3), "label": label}


def derive_research(snapshot: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    """Return structured deep-research context derived from current + historical snapshots."""
    current_ts = int(snapshot.get("ts") or 0)
    rows = _history(state, current_ts)
    p2 = _past_snapshot(rows, current_ts, 2.0)
    p6 = _past_snapshot(rows, current_ts, 6.0)
    p12 = _past_snapshot(rows, current_ts, 12.0)

    btc_now = _price(snapshot, "BTCUSDT")
    breadth_now = sf((snapshot.get("breadth") or {}).get("up_pct"))
    score6_now = _score(snapshot, "score_6h")
    score24_now = _score(snapshot, "score_24h")

    changes = {
        "btc_2h": _pct_change(btc_now, _price(p2, "BTCUSDT")),
        "btc_6h": _pct_change(btc_now, _price(p6, "BTCUSDT")),
        "btc_12h": _pct_change(btc_now, _price(p12, "BTCUSDT")),
        "breadth_2h": _delta(breadth_now, _breadth_up(p2)),
        "breadth_6h": _delta(breadth_now, _breadth_up(p6)),
        "breadth_12h": _delta(breadth_now, _breadth_up(p12)),
        "score6_6h": _delta(score6_now, _score(p6, "score_6h")),
        "score24_6h": _delta(score24_now, _score(p6, "score_24h")),
    }

    counts = _direction_counts(snapshot)
    derivatives = _derivatives(snapshot)
    heat = _heat(snapshot)
    leadership = _leadership(snapshot, p6 or p2)
    breadth = snapshot.get("breadth") or {}
    median = sf(breadth.get("median_change"))
    weighted = sf(breadth.get("volume_weighted_change"))
    down_pct = sf(breadth.get("down_pct"))
    direction24 = str((snapshot.get("outlook") or {}).get("direction_24h") or "FLAT").upper()
    b6 = changes.get("breadth_6h")

    if direction24 == "UP":
        if breadth_now >= 55 and (b6 is None or b6 >= -3):
            pulse = "YÜKSELİŞ GENİŞ TABANA YAYILIYOR"
            narrative = "Majör trend ile altcoin breadth aynı yönde; yükseliş yalnız BTC'ye bağlı değil."
        elif breadth_now < 45:
            pulse = "MAJÖRLER GÜÇLÜ, ALTCOINLER GERİDE"
            narrative = "BTC/ETH/SOL güçlü olsa da piyasanın çoğu aynı ölçüde katılmıyor; coin seçimi kritik."
        elif b6 is not None and b6 <= -10:
            pulse = "YÜKSELİŞ SÜRÜYOR AMA BREADTH DARALIYOR"
            narrative = "Fiyat yapısı yukarı kalırken katılım azalıyor; kısa vadeli yorulma uyarısı var."
        else:
            pulse = "YUKARI EĞİLİM KORUNUYOR"
            narrative = "Majör yapı pozitif, piyasa katılımı ise orta kuvvette."
    elif direction24 == "DOWN":
        if down_pct >= 55 and (b6 is None or b6 <= 3):
            pulse = "SATIŞ GENİŞ TABANA YAYILIYOR"
            narrative = "Majör zayıflık ve altcoin breadth aynı yönde; düşüş yalnız BTC'ye özgü değil."
        elif breadth_now > 55:
            pulse = "MAJÖRLER ZAYIF AMA ALTCOINLER DİRENÇLİ"
            narrative = "Majör yön aşağı olsa da altcoin katılımı dirençli; kısa vadeli tepki riski yükseliyor."
        else:
            pulse = "AŞAĞI EĞİLİM KORUNUYOR"
            narrative = "Majör yapı negatif, piyasa katılımı da zayıf."
    else:
        pulse = "YÖN ARANIYOR / SIKIŞMA"
        narrative = "Majörler ve breadth net ortak yön üretmiyor; kırılım teyidi beklemek daha sağlıklı."

    evidence: List[str] = []
    if counts["total"]:
        evidence.append(f"Majör MTF uyumu: {counts['up']}/{counts['total']} yukarı, {counts['down']}/{counts['total']} aşağı.")
    evidence.append(f"Breadth: %{breadth_now:.1f} yükselen, medyan 24S %{median:+.2f}, hacim ağırlıklı %{weighted:+.2f}.")
    if changes["breadth_6h"] is not None:
        evidence.append(f"Son ~6 saatte yükselen coin oranı {changes['breadth_6h']:+.1f} puan değişti.")
    if changes["btc_6h"] is not None:
        evidence.append(f"BTC son ~6 saatte %{changes['btc_6h']:+.2f} değişti.")
    evidence.append(leadership["label"].capitalize() + ".")
    evidence.append(derivatives["funding_label"].capitalize() + "; " + derivatives["oi_label"] + ".")

    risks: List[str] = []
    if heat["rsi_4h"] >= 70:
        risks.append(f"BTC 4H RSI {heat['rsi_4h']:.1f}: trend güçlü ama kısa vadede geri test/düzeltme riski yükselmiş.")
    if direction24 == "UP" and breadth_now < 45:
        risks.append("Yükseliş altcoin geneline yayılmıyor; geniş piyasa LONG iştahını sınırlamak gerekir.")
    if direction24 == "UP" and b6 is not None and b6 <= -10:
        risks.append("Breadth son saatlerde hızlı daralıyor; fiyat yükselse bile iç yapı zayıflıyor olabilir.")
    if derivatives["funding_average"] is not None and abs(derivatives["funding_average"]) >= 0.0005:
        risks.append("Funding kalabalığı ters yönlü squeeze riskini artırıyor.")
    if derivatives["oi_average_change"] is not None and derivatives["oi_average_change"] >= 1.0:
        risks.append("OI hızlı artıyor; kaldıraç birikimi ani tasfiye hareketlerini büyütebilir.")
    if not risks:
        risks.append("Belirgin sistemik risk bayrağı yok; ana seviyelerde kırılım teyidi yine gerekli.")

    history_hours = 0.0
    if rows:
        history_hours = round(max(0, current_ts - int(rows[0].get("ts") or current_ts)) / 3600.0, 1)

    return {
        "version": VERSION,
        "sampling_hours": 2,
        "snapshot_count": len(rows),
        "history_hours": history_hours,
        "pulse": pulse,
        "narrative": narrative,
        "changes": changes,
        "alignment": counts,
        "derivatives": derivatives,
        "heat": heat,
        "leadership": leadership,
        "evidence": evidence[:6],
        "risks": risks[:4],
    }
