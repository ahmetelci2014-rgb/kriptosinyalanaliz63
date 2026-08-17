"""V3.32.9 sosyal medya işlem kartı üreticisi. Yalnız panel sunum katmanıdır."""
from __future__ import annotations

import html
import math
import re
import urllib.parse
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_32_9_SHARE_CARDS_2026_08_17"
W, H = 1080, 1350


def n(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def esc(value: Any) -> str:
    return html.escape(str(value if value not in (None, "") else "—"))


def fmt(value: Any) -> str:
    number = n(value)
    if number is None:
        return "—"
    if abs(number) >= 1000:
        return f"{number:,.2f}".replace(",", ".")
    if abs(number) >= 1:
        return f"{number:.5f}".rstrip("0").rstrip(".")
    return f"{number:.9f}".rstrip("0").rstrip(".")


def symbol(row: dict[str, Any]) -> str:
    return str(row.get("symbol") or row.get("display_symbol") or "—").replace("/USDT:USDT", "USDT").replace("/", "").upper()


def direction(row: dict[str, Any]) -> str:
    return str(row.get("direction") or "").upper()


def system_name(row: dict[str, Any]) -> str:
    return str(row.get("system_label") or row.get("system") or row.get("source") or "Sistem")


def outcome(row: dict[str, Any]) -> str:
    return str(row.get("outcome") or row.get("result") or row.get("final_result") or "KAPALI").upper()


def score(row: dict[str, Any]) -> str:
    for key in ("score", "signal_score", "confidence", "quality_score", "strength"):
        value = n(row.get(key))
        if value is not None:
            return f"{value:.0f}" if value.is_integer() else f"{value:.1f}"
    return str(row.get("quality") or row.get("grade") or "—")


def stamp(row: dict[str, Any]) -> str:
    for key in ("trade_id", "signal_id", "id", "opened_at", "open_time", "entry_time", "created_at", "created_ts", "timestamp", "closed_at", "close_time", "ended_at"):
        if row.get(key) not in (None, ""):
            return str(row[key])
    return ""


def selector_params(row: dict[str, Any], kind: str, stage: str) -> dict[str, str]:
    params = {"kind": kind, "stage": stage, "symbol": symbol(row), "direction": direction(row), "system": system_name(row)}
    if n(row.get("entry")) is not None:
        params["entry"] = str(float(row["entry"]))
    if kind == "result":
        params["outcome"] = outcome(row)
    if stamp(row):
        params["stamp"] = stamp(row)
    return params


def share_href(row: dict[str, Any], kind: str = "open", stage: str = "signal") -> str:
    return "/share/trade?" + urllib.parse.urlencode(selector_params(row, kind, stage))


def svg_href(row: dict[str, Any], kind: str = "open", stage: str = "signal") -> str:
    return "/share/card.svg?" + urllib.parse.urlencode(selector_params(row, kind, stage))


def _matches(row: dict[str, Any], query: dict[str, list[str]], kind: str) -> bool:
    wanted_symbol = str((query.get("symbol") or [""])[0]).upper()
    if not wanted_symbol or symbol(row) != wanted_symbol:
        return False
    wanted_direction = str((query.get("direction") or [""])[0]).upper()
    if wanted_direction and direction(row) != wanted_direction:
        return False
    wanted_system = str((query.get("system") or [""])[0])
    if wanted_system and system_name(row) != wanted_system:
        return False
    wanted_entry, actual_entry = n((query.get("entry") or [""])[0]), n(row.get("entry"))
    if wanted_entry is not None and actual_entry is not None and abs(wanted_entry - actual_entry) > max(1e-10, abs(wanted_entry) * 1e-10):
        return False
    if kind == "result":
        wanted_outcome = str((query.get("outcome") or [""])[0]).upper()
        if wanted_outcome and outcome(row) != wanted_outcome:
            return False
    wanted_stamp = str((query.get("stamp") or [""])[0])
    if wanted_stamp and stamp(row) and stamp(row) != wanted_stamp:
        return False
    return True


def find_record(data: dict[str, Any], query: dict[str, list[str]]) -> tuple[str, dict[str, Any]] | None:
    kind = str((query.get("kind") or ["open"])[0]).lower()
    if kind not in {"open", "result"}:
        return None
    source = data.get("open_trades") if kind == "open" else data.get("recent_results")
    rows = [row for row in (source or []) if isinstance(row, dict)]
    for row in rows:
        if _matches(row, query, kind):
            return kind, row
    if query.get("stamp"):
        relaxed = dict(query)
        relaxed.pop("stamp", None)
        for row in rows:
            if _matches(row, relaxed, kind):
                return kind, row
    return None


def event_time(row: dict[str, Any]) -> str:
    raw: Any = None
    for key in ("closed_at", "close_time", "ended_at", "opened_at", "open_time", "entry_time", "created_at", "created_ts", "timestamp"):
        if row.get(key) not in (None, ""):
            raw = row[key]
            break
    if raw is None:
        return "Sistem kaydı"
    try:
        value = float(raw)
        if value > 10_000_000_000:
            value /= 1000
        return datetime.fromtimestamp(value, tz=timezone.utc).astimezone(ZoneInfo("Europe/Istanbul")).strftime("%d.%m.%Y %H:%M")
    except Exception:
        return str(raw).replace("T", " ").replace("Z", "")[:19]


def rr_text(row: dict[str, Any]) -> str:
    entry, sl = n(row.get("entry")), n(row.get("sl"))
    target = n(row.get("tp3")) or n(row.get("tp2")) or n(row.get("tp1"))
    if entry is None or sl is None or target is None or abs(entry - sl) <= 0:
        return "—"
    return f"1:{abs(target - entry) / abs(entry - sl):.2f}".rstrip("0").rstrip(".")


def result_color(text: str) -> str:
    if text.startswith("TP") and "BE" not in text:
        return "#42e28c"
    if text == "SL" or text.startswith("SL_"):
        return "#ff627d"
    if "BE" in text:
        return "#ffbd59"
    return "#69a9ff"


def _candle_rows(candles: list[dict[str, Any]]) -> list[tuple[float, float, float, float]]:
    rows = []
    for item in (candles or [])[-70:]:
        if not isinstance(item, dict) or n(item.get("close")) is None:
            continue
        close = n(item["close"])
        opened = n(item.get("open")) if n(item.get("open")) is not None else close
        high = n(item.get("high")) if n(item.get("high")) is not None else max(opened, close)
        low = n(item.get("low")) if n(item.get("low")) is not None else min(opened, close)
        rows.append((opened, high, low, close))
    return rows


def render_svg(row: dict[str, Any], *, kind: str, stage: str, candles: list[dict[str, Any]], source: str = "PUBLIC") -> str:
    sym, direct, result = symbol(row), direction(row) or "İŞLEM", outcome(row)
    dir_color = "#42e28c" if direct == "LONG" else "#ff627d" if direct == "SHORT" else "#69a9ff"
    if kind == "result":
        headline, badge, badge_color = "İŞLEM SONUCU", f"{result} GERÇEKLEŞTİ", result_color(result)
    elif stage == "tracking":
        headline, badge, badge_color = "AÇIK İŞLEM TAKİBİ", "CANLI TAKİP", "#2ce6bf"
    else:
        headline, badge, badge_color = "YENİ İŞLEM SİNYALİ", "CANLI TAKİP", "#2ce6bf"

    cx, cy, cw, ch = 420.0, 360.0, 590.0, 650.0
    candle_rows = _candle_rows(candles)
    levels = [("TP3", n(row.get("tp3")), "#42e28c"), ("TP2", n(row.get("tp2")), "#42e28c"), ("TP1", n(row.get("tp1")), "#42e28c"), ("GİRİŞ", n(row.get("entry")), "#2ce6bf"), ("SL", n(row.get("sl")), "#ff627d")]
    prices = [value for item in candle_rows for value in (item[1], item[2])] + [value for _, value, _ in levels if value is not None]
    if prices:
        pmin, pmax = min(prices), max(prices)
        span = max(pmax - pmin, abs(pmax) * 0.002, 1e-9)
        pmin, pmax = pmin - span * 0.08, pmax + span * 0.08
    else:
        pmin, pmax = 0.0, 1.0
    py = lambda price: cy + ch - ((price - pmin) / (pmax - pmin)) * ch

    svg = [f'''<svg id="shareCard" xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}"><defs><linearGradient id="bg" x2="1" y2="1"><stop stop-color="#061018"/><stop offset="1" stop-color="#02070b"/></linearGradient></defs><rect width="1080" height="1350" fill="url(#bg)"/><rect x="28" y="28" width="1024" height="1294" rx="34" fill="none" stroke="#8f6b34" stroke-width="2"/><circle cx="90" cy="95" r="46" fill="#101a20" stroke="#d5a64d" stroke-width="3"/><text x="90" y="113" text-anchor="middle" fill="#e7bd67" font-size="48" font-weight="900" font-family="Arial">K</text><text x="158" y="86" fill="#f1c66f" font-size="48" font-weight="900" font-family="Arial">KRİPTO</text><text x="160" y="124" fill="#d9d6ca" font-size="24" font-weight="700" letter-spacing="3" font-family="Arial">KONTROL MERKEZİ</text><line x1="70" y1="160" x2="1010" y2="160" stroke="#8b6731"/><text x="70" y="218" fill="#2ce6bf" font-size="38" font-weight="900" font-family="Arial">{esc(headline)}</text><rect x="750" y="180" width="260" height="58" rx="29" fill="#0d1c20" stroke="{badge_color}"/><text x="880" y="218" text-anchor="middle" fill="{badge_color}" font-size="21" font-weight="900" font-family="Arial">{esc(badge)}</text><rect x="70" y="270" width="310" height="740" rx="24" fill="#0b171f" stroke="#604a2d"/><text x="95" y="340" fill="#f4f7f6" font-size="50" font-weight="900" font-family="Arial">{esc(sym)}</text><rect x="94" y="365" width="260" height="76" rx="18" fill="{dir_color}" fill-opacity=".15" stroke="{dir_color}" stroke-width="2"/><text x="224" y="417" text-anchor="middle" fill="{dir_color}" font-size="42" font-weight="900" font-family="Arial">{esc(direct)}</text>''']
    y = 480
    for label, value, color in [("Giriş", row.get("entry"), "#2ce6bf"), ("TP1", row.get("tp1"), "#42e28c"), ("TP2", row.get("tp2"), "#42e28c"), ("TP3", row.get("tp3"), "#42e28c"), ("SL", row.get("sl"), "#ff627d")]:
        svg.append(f'<rect x="94" y="{y}" width="260" height="70" rx="13" fill="#07131a" stroke="#2a3940"/><text x="112" y="{y+27}" fill="{color}" font-size="18" font-weight="800" font-family="Arial">{label}</text><text x="112" y="{y+55}" fill="#f0f5f4" font-size="24" font-weight="900" font-family="Arial">{esc(fmt(value))}</text>')
        y += 84
    svg.append(f'''<text x="95" y="925" fill="#8fa6a3" font-size="16" font-family="Arial">Sistem</text><text x="95" y="953" fill="#f0f4f3" font-size="19" font-weight="800" font-family="Arial">{esc(system_name(row)[:24])}</text><text x="260" y="925" fill="#8fa6a3" font-size="16" font-family="Arial">Skor</text><text x="260" y="953" fill="#f0f4f3" font-size="20" font-weight="800" font-family="Arial">{esc(score(row))}</text><text x="95" y="988" fill="#728985" font-size="14" font-family="Arial">{esc(event_time(row))}</text><rect x="420" y="270" width="590" height="740" rx="24" fill="#07131a" stroke="#604a2d"/><text x="446" y="316" fill="#eef6f4" font-size="25" font-weight="900" font-family="Arial">{esc(sym)} · 15M</text><text x="986" y="316" text-anchor="end" fill="#6d8581" font-size="13" font-family="Arial">{esc(source[:28])}</text>''')
    for index in range(1, 6):
        gy = cy + ch * index / 6
        svg.append(f'<line x1="{cx+18}" y1="{gy:.1f}" x2="{cx+cw-18}" y2="{gy:.1f}" stroke="#13242c"/>')
    if candle_rows:
        left, right = cx + 22, cx + cw - 24
        step = (right - left) / len(candle_rows)
        body_width = max(3.0, min(9.0, step * 0.58))
        for index, (opened, high, low, close) in enumerate(candle_rows):
            x = left + (index + 0.5) * step
            yo, yh, yl, yc = py(opened), py(high), py(low), py(close)
            color = "#42e28c" if close >= opened else "#ff627d"
            svg.append(f'<line x1="{x:.1f}" y1="{yh:.1f}" x2="{x:.1f}" y2="{yl:.1f}" stroke="{color}" stroke-width="2"/><rect x="{x-body_width/2:.1f}" y="{min(yo,yc):.1f}" width="{body_width:.1f}" height="{max(2.0,abs(yc-yo)):.1f}" fill="{color}"/>')
    else:
        svg.append(f'<text x="{cx+cw/2}" y="{cy+ch/2}" text-anchor="middle" fill="#647d79" font-size="20" font-family="Arial">Mum verisi alınamadı</text>')
    for label, value, color in levels:
        if value is None:
            continue
        y = py(value)
        hit = kind == "result" and (result.startswith(label) or ("BE" in result and label == "GİRİŞ"))
        dash = "" if hit else ' stroke-dasharray="10 8"'
        svg.append(f'<line x1="{cx+16}" y1="{y:.1f}" x2="{cx+cw-18}" y2="{y:.1f}" stroke="{color}" stroke-width="{4 if hit else 2}"{dash}/><rect x="{cx+cw-132}" y="{y-19:.1f}" width="114" height="38" rx="8" fill="#07131a" stroke="{color}"/><text x="{cx+cw-75}" y="{y+7:.1f}" text-anchor="middle" fill="{color}" font-size="15" font-weight="900" font-family="Arial">{label} {esc(fmt(value))}</text>')
    r_value = n(row.get("r_result"))
    r_text = f"{r_value:+.2f}R" if r_value is not None else "—"
    state = result if kind == "result" else str(row.get("progress") or "AÇIK").upper()
    svg.append(f'''<rect x="70" y="1045" width="940" height="150" rx="24" fill="#0a151c" stroke="#263943"/><text x="100" y="1090" fill="#829a96" font-size="15" font-family="Arial">Tahmini R:R</text><text x="100" y="1140" fill="#2ce6bf" font-size="38" font-weight="900" font-family="Arial">{esc(rr_text(row))}</text><text x="330" y="1090" fill="#829a96" font-size="15" font-family="Arial">Durum</text><text x="330" y="1138" fill="{badge_color}" font-size="30" font-weight="900" font-family="Arial">{esc(state)}</text><text x="650" y="1090" fill="#829a96" font-size="15" font-family="Arial">Net R</text><text x="650" y="1138" fill="{badge_color}" font-size="30" font-weight="900" font-family="Arial">{esc(r_text)}</text><text x="100" y="1236" fill="#627a77" font-size="14" font-family="Arial">Gerçek panel kaydından otomatik üretildi</text><line x1="70" y1="1264" x2="1010" y2="1264" stroke="#4e3b25"/><text x="540" y="1303" text-anchor="middle" fill="#c9cecb" font-size="16" font-family="Arial">Bilgilendirme amaçlıdır. Yatırım tavsiyesi değildir.</text></svg>''')
    return "".join(svg)


def render_page(row: dict[str, Any], *, kind: str, stage: str, candles: list[dict[str, Any]], source: str, nonce: str) -> str:
    svg = render_svg(row, kind=kind, stage=stage, candles=candles, source=source)
    filename = f"kripto-kontrol-{re.sub(r'[^A-Za-z0-9]', '', symbol(row)).lower()}-{'sonuc' if kind == 'result' else 'sinyal'}.png"
    caption = f"{symbol(row)} {outcome(row) if kind == 'result' else direction(row)} · Kripto Kontrol Merkezi"
    return f'''<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="color-scheme" content="dark"><title>{esc(symbol(row))} paylaş</title><style>*{{box-sizing:border-box}}body{{margin:0;background:#061018;color:#eef6f4;font:14px system-ui;padding:16px}}.w{{max-width:760px;margin:auto}}.p{{background:#03080d;border:1px solid #604a2d;border-radius:18px;padding:8px}}svg{{display:block;width:100%;height:auto}}.a{{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:12px}}button,a{{border:1px solid #29413f;border-radius:12px;padding:12px;background:#0d2027;color:#eef6f4;font-weight:900;text-align:center;text-decoration:none}}button:first-child{{background:#0e332b;color:#2ce6bf}}small{{display:block;color:#829a96;margin:10px 2px}}</style></head><body><div class="w"><div class="p">{svg}</div><div class="a"><button id="share">↗ Paylaş</button><button id="down">PNG indir</button><a href="{esc(svg_href(row,kind,stage))}" target="_blank" rel="noopener">SVG aç</a><a href="javascript:history.back()">← Geri</a></div><small>Paylaş desteklenmiyorsa PNG otomatik indirilir.</small></div><script nonce="{esc(nonce)}">(()=>{{const s=document.getElementById('shareCard'),fn={filename!r},cap={caption!r};function png(){{return new Promise((ok,no)=>{{const x=new XMLSerializer().serializeToString(s),i=new Image();i.onload=()=>{{const c=document.createElement('canvas');c.width=1080;c.height=1350;c.getContext('2d').drawImage(i,0,0,1080,1350);c.toBlob(b=>b?ok(b):no(new Error('PNG')),'image/png')}};i.onerror=no;i.src='data:image/svg+xml;charset=utf-8,'+encodeURIComponent(x)}})}}function dl(b){{const u=URL.createObjectURL(b),a=document.createElement('a');a.href=u;a.download=fn;a.click();setTimeout(()=>URL.revokeObjectURL(u),1000)}}document.getElementById('down').onclick=async()=>dl(await png());document.getElementById('share').onclick=async()=>{{const b=await png(),f=new File([b],fn,{{type:'image/png'}});try{{if(navigator.share&&(!navigator.canShare||navigator.canShare({{files:[f]}}))){{await navigator.share({{title:cap,text:cap,files:[f]}});return}}}}catch(e){{if(e&&e.name==='AbortError')return}}dl(b)}}}})()</script></body></html>'''
