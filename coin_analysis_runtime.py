from __future__ import annotations

import os
import re
import sys
import traceback
from typing import Any, Iterable, Optional

import requests


TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
RAW_SYMBOL = os.getenv("SYMBOL") or (sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT")
TELEGRAM_LIMIT = 3800
PRESENTATION_VERSION = "COIN_DETAIL_PREMIUM_UI_V1_2026_08_23"


def _send(text: Any) -> None:
    message = str(text or "")
    if not TOKEN or not CHAT_ID:
        print("TOKEN / CHAT_ID yok. Telegram gönderilmedi.")
        return
    response = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": message},
        timeout=25,
    )
    print("Telegram HTTP:", response.status_code)
    response.raise_for_status()


def _split(text: Any, limit: int = TELEGRAM_LIMIT):
    value = str(text or "")
    if len(value) <= limit:
        return [value]
    parts = []
    current = ""
    for line in value.splitlines(keepends=True):
        while len(line) > limit:
            if current:
                parts.append(current.rstrip())
                current = ""
            parts.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) > limit:
            if current:
                parts.append(current.rstrip())
            current = line
        else:
            current += line
    if current:
        parts.append(current.rstrip())
    return parts or [value[:limit]]


def _install_gate_compat(analyzer: Any) -> None:
    """Coin Analyzer V2.1 ile canlı Premium V4 gate tuple biçimini eşleştirir.

    premium_profit_runner._make_profit_gate canlıda (ok, reason) döndürür.
    Coin Analyzer'ın önceki sürümü üç değer bekliyordu. Bu adapter yalnız
    dönüş biçimini normalize eder; karar mantığını veya filtreleri değiştirmez.
    """
    original_builder = analyzer._build_preview_gates

    def compatible_builder(temp_dir: str):
        gate, pending_gate, entry_gate = original_builder(temp_dir)

        def compatible_entry_gate(signal: dict, current_price: Any):
            result = entry_gate(signal, current_price)
            if isinstance(result, tuple):
                if len(result) >= 3:
                    return result[0], result[1], result[2]
                if len(result) == 2:
                    return result[0], result[1], None
                if len(result) == 1:
                    return bool(result[0]), "Premium gate sonucu", None
            return bool(result), "Premium gate sonucu", None

        return gate, pending_gate, compatible_entry_gate

    analyzer._build_preview_gates = compatible_builder


def _clean_lines(text: Any) -> list[str]:
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


def _first(lines: Iterable[str], prefix: str, default: str = "-") -> str:
    for line in lines:
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return default


def _first_line(lines: Iterable[str], prefix: str, default: str = "-") -> str:
    for line in lines:
        if line.startswith(prefix):
            return line.strip()
    return default


def _section_bullet(lines: list[str], heading: str, default: str = "-") -> str:
    try:
        idx = lines.index(heading)
    except ValueError:
        return default
    for line in lines[idx + 1:]:
        if line.startswith("• "):
            return line[2:].strip()
        if line.startswith(("🧭", "🌍", "📈", "🧬", "🔄", "🚀", "💎", "📌", "⏳", "✅", "🛡")):
            break
    return default


def _trend_icon(text: str) -> str:
    value = str(text or "").lower()
    if any(word in value for word in ("yukarı", "alım", "long")) and not any(
        word in value for word in ("kararsız", "kapalı", "uygun değil")
    ):
        return "🟢"
    if any(word in value for word in ("aşağı", "satış", "short")) and not any(
        word in value for word in ("kararsız", "kapalı", "uygun değil")
    ):
        return "🔴"
    if any(word in value for word in ("kararsız", "geçiş", "orta", "bekle")):
        return "🟡"
    return "⚪"


def _decision_icon(decision: str) -> str:
    value = str(decision or "").upper()
    if value == "LONG":
        return "🟢"
    if value == "SHORT":
        return "🔴"
    return "🟡"


def _compact_reason(text: str) -> str:
    value = str(text or "-").strip()
    replacements = {
        "Canlı Premium karar yollarının hiçbiri işlem adayı üretmedi.": "Premium işlem adayı henüz oluşmadı.",
        "Yakın zamanda stop olduğu için canlı Premium cooldown koruması aktif.": "Yakın stop cooldown koruması aktif.",
        "Yakın zamanda kapanan işlem cooldown koruması aktif; Reversal istisnası oluşmadı.": "Yakın kapanış cooldown koruması aktif.",
        "Canlı Premium market guard bu yönü şu anda onaylamıyor.": "Market Guard bu yönü henüz onaylamıyor.",
        "Riskli açık Premium sinyal limiti dolu.": "Premium açık risk limiti dolu.",
    }
    return replacements.get(value, value)


def _shorten_mtf(line: str, timeframe: str) -> tuple[str, str]:
    if not line or line == "-":
        return f"{timeframe} ⚪ Veri yok", "-"
    text = line[2:].strip() if line.startswith("• ") else line.strip()
    text = re.sub(rf"^{re.escape(timeframe)}:\s*", "", text, flags=re.IGNORECASE)
    parts = [part.strip() for part in text.split("|")]
    state = parts[0] if parts else text
    state = re.sub(rf"^{re.escape(timeframe)}\s+", "", state, flags=re.IGNORECASE)
    state = state.replace("ana trend ", "").replace("hafif ", "")
    state = state.replace("yukarı eğilim ama güç orta", "Yukarı / orta")
    state = state.replace("aşağı eğilim ama güç orta", "Aşağı / orta")
    state = state.replace("yukarı", "Yukarı").replace("aşağı", "Aşağı")
    state = state.replace("kararsız", "Kararsız")
    state = state.replace("alım onayı", "Alım teyidi").replace("satış onayı", "Satış teyidi")

    volume = next((part for part in parts[1:] if part.lower().startswith("hacim:")), None)
    movement = next((part for part in parts[1:] if "movement start" in part.lower()), None)
    suffix = ""
    if volume:
        suffix = " • " + volume.replace("Hacim:", "Hacim ")
    if movement:
        move_text = movement.split(":", 1)[-1].strip()
        if "adayı yok" in move_text.lower():
            move_text = "Tetik yok"
        suffix = " • " + move_text

    technical = " • ".join(parts[1:]) if len(parts) > 1 else "-"
    return f"{timeframe} {_trend_icon(state)} {state}{suffix}", technical


def _compact_market(line: str) -> str:
    if not line or line == "-":
        return "Veri yok"
    text = line[2:].strip() if line.startswith("• ") else line.strip()
    text = text.replace("6s:", "6s").replace("24s:", "24s")
    return text


def _compact_guard(line: str) -> str:
    if not line or line == "-":
        return "LONG — • SHORT —"
    text = line[2:].strip() if line.startswith("• ") else line.strip()
    text = text.replace("Canlı legacy market guard:", "").strip()
    text = text.replace("LONG=AÇIK", "LONG ✅").replace("LONG=KAPALI", "LONG ⛔")
    text = text.replace("SHORT=AÇIK", "SHORT ✅").replace("SHORT=KAPALI", "SHORT ⛔")
    return text


def _compact_derivative(text: str) -> str:
    value = str(text or "-").strip()
    value = value.replace("sağlıklı / aşırı kalabalık değil", "Sağlıklı")
    value = value.replace("OI dengeli", "Dengeli")
    value = value.replace("OI hızlı artıyor", "Hızlı artıyor")
    value = value.replace("OI hızlı azalıyor", "Hızlı azalıyor")
    return value


def _candidate_exists(source: str, score: str) -> bool:
    return source not in {"-", "YOK"} and "Aday oluşmadı" not in str(score)


def _modernize_report(raw_report: str) -> str:
    """Eski ayrıntılı raporu karar mantığına dokunmadan premium Telegram görünümüne çevirir."""
    lines = _clean_lines(raw_report)
    if not lines:
        return raw_report

    coin = _first(lines, "Coin:", "-")
    market = _first(lines, "Market:", "-")
    price = _first(lines, "Fiyat:", "-")

    mtf1d_raw = _first_line(lines, "• 1D:")
    mtf4h_raw = _first_line(lines, "• 4H:")
    mtf1h_raw = _first_line(lines, "• 1H:")
    mtf15_raw = _first_line(lines, "• 15M:")
    mtf5_raw = _first_line(lines, "• 5M:")

    mtf1d, tech1d = _shorten_mtf(mtf1d_raw, "1D")
    mtf4h, tech4h = _shorten_mtf(mtf4h_raw, "4H")
    mtf1h, tech1h = _shorten_mtf(mtf1h_raw, "1H")
    mtf15, tech15 = _shorten_mtf(mtf15_raw, "15M")
    mtf5, tech5 = _shorten_mtf(mtf5_raw, "5M")

    outlook = _compact_market(_first_line(lines, "• 6s:"))
    guard = _compact_guard(_first_line(lines, "• Canlı legacy market guard:"))
    risk_flags = _first(lines, "• Risk bayrakları:", "Yok")
    funding = _compact_derivative(_first(lines, "• Funding:", "-"))
    oi = _compact_derivative(_first(lines, "• Open Interest:", "-"))
    orderflow = _section_bullet(lines, "🧬 ORDER-FLOW V3", "-")
    if "V2 PREP/ARMED/TRIGGER adayı yok" in orderflow:
        orderflow = "Sorgulanmadı • 5M yapı adayı yok"

    reversal_status = _section_bullet(lines, "🔄 REVERSAL CAPTURE", "Yok")
    continuation_status = _section_bullet(lines, "🚀 TREND CONTINUATION", "Yok")

    source = _first(lines, "• Kaynak:", "-")
    score = _first(lines, "• Premium skor:", "-")
    stop_cd = _first(lines, "• Yakın stop cooldown:", "-")
    close_cd = _first(lines, "• Yakın kapanış cooldown:", "-")
    base_entry = _first(lines, "• Base giriş güvenliği:", "-")
    cost = _first(lines, "• Maliyet kontrolü:", "-")
    portfolio = _first(lines, "• Portfolio Risk:", "-")
    open_risk = _first(lines, "• Açık Premium risk:", "-")
    duplicate = _first(lines, "• Duplicate:", "-")
    core_leverage = _first(lines, "• Çekirdek kaldıraç:", "-")
    contextual_leverage = _first(lines, "• Bağlamsal kaldıraç tavanı:", "-")

    decision_line = _first_line(lines, "📌 KARAR:", "📌 KARAR: BEKLE")
    decision = decision_line.split(":", 1)[-1].strip().upper()
    reason = _compact_reason(_first(lines, "Neden:", "-"))
    has_candidate = _candidate_exists(source, score)

    if not has_candidate:
        core_leverage = "-"
        contextual_leverage = "-"
        base_entry = "Aday yok"
        cost = "Aday yok"
        close_cd = "AKTİF" if "AKTİF" in close_cd else "YOK"

    decision_icon = _decision_icon(decision)
    decision_label = decision if decision in {"LONG", "SHORT"} else "BEKLE"

    market_icon = _trend_icon(outlook)
    cooldown_status = "✅ YOK" if stop_cd == "YOK" and close_cd == "YOK" else f"⚠️ Stop {stop_cd} • Kapanış {close_cd}"
    duplicate_status = "✅ YOK" if duplicate == "YOK" else f"⚠️ {duplicate}"
    portfolio_status = f"✅ {portfolio}" if portfolio == "UYGUN" else f"⚠️ {portfolio}"

    output = [
        "💎 PREMIUM COIN MİKROSKOP",
        "━━━━━━━━━━━━━━━━━━",
        f"{coin}  •  {price}",
        f"{decision_icon} KARAR  {decision_label}",
        f"📍 {_compact_reason(reason)}",
        "",
        "🧭 TREND HARİTASI",
        mtf1d,
        mtf4h,
        mtf1h,
        mtf15,
        mtf5,
        "",
        "🌐 PİYASA NABZI",
        f"{market_icon} {outlook}",
        f"🛡 Guard  {guard}",
        f"💸 Funding  {funding}",
        f"📊 OI  {oi}",
        f"🧬 Flow  {orderflow}",
    ]

    if risk_flags and risk_flags != "Yok":
        output.append(f"⚠️ Risk  {risk_flags}")

    output.extend([
        "",
        "🎯 PREMIUM KONTROL",
        f"Kaynak  •  {source}",
        f"Skor    •  {score}",
        f"Trend devam  •  {continuation_status}",
        f"Reversal     •  {reversal_status}",
        f"Portfolio    •  {portfolio_status}",
        f"Açık risk    •  {open_risk}",
        f"Cooldown     •  {cooldown_status}",
        f"Duplicate    •  {duplicate_status}",
    ])

    if has_candidate:
        output.extend([
            f"Giriş güvenliği • {base_entry}",
            f"Maliyet         • {cost}",
            f"Kaldıraç        • {core_leverage} | tavan {contextual_leverage}",
        ])

    if decision in {"LONG", "SHORT"}:
        output.extend(["", "✅ İŞLEM PLANI"])
        for prefix in (
            "Yön:", "Giriş:", "TP1:", "TP2:", "TP3:", "SL:",
            "Stop Mesafesi:", "R/R:", "Çekirdek Kaldıraç:", "Bağlamsal Kaldıraç Tavanı:"
        ):
            value = _first(lines, prefix, "")
            if value:
                label = prefix[:-1]
                output.append(f"{label}  •  {value}")
    else:
        output.extend([
            "",
            "⏳ İŞLEM PLANI",
            "Henüz yok. Entry / TP / SL yalnız Premium kapıları gerçekten geçtiğinde açılır.",
        ])

    technical_bits = []
    for label, technical in (("4H", tech4h), ("1H", tech1h), ("15M", tech15)):
        if technical and technical != "-":
            technical_bits.append(f"{label} {technical}")
    if technical_bits:
        output.extend(["", "🔎 TEKNİK ÖZET", " • ".join(technical_bits)])

    output.extend([
        "",
        f"🛡 Canlı Premium mantığı korunur • {PRESENTATION_VERSION}",
        f"Market: {market}",
    ])
    return "\n".join(output).strip()


def main() -> int:
    try:
        import coin_analyzer as analyzer
    except Exception as exc:
        message = (
            "❌ COIN DETAY ANALİZİ — IMPORT HATASI\n\n"
            f"Hata: {type(exc).__name__}: {exc}\n\n"
            "Coin Analyzer modülleri yüklenemedi."
        )
        print(traceback.format_exc())
        try:
            _send(message)
        finally:
            raise

    _install_gate_compat(analyzer)
    symbol = analyzer.normalize_symbol(RAW_SYMBOL)
    print("Coin Detay Analizi runtime başlatıldı:", symbol)

    try:
        raw_report = analyzer.analyze_coin(symbol)
        report = _modernize_report(raw_report)
    except RuntimeError as exc:
        # Kontrat/veri yetersizliği programlama hatası değildir. Kullanıcıya
        # BEKLE raporu verilir ve workflow gereksiz yere kırmızı olmaz.
        report = (
            "💎 PREMIUM COIN MİKROSKOP\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"{symbol}\n"
            "🟡 KARAR  BEKLE\n\n"
            f"📍 {exc}\n\n"
            "OKX verisi tamamlandığında yeniden değerlendirilebilir."
        )
        print(report)
    except Exception as exc:
        tb = traceback.format_exc()
        print(tb)
        tail = tb[-2200:]
        message = (
            "❌ COIN DETAY ANALİZİ — ÇALIŞMA HATASI\n\n"
            f"Coin: {symbol}\n"
            f"Hata: {type(exc).__name__}: {exc}\n\n"
            f"Traceback sonu:\n{tail}"
        )
        try:
            for part in _split(message):
                _send(part)
        finally:
            raise

    print(report)
    parts = _split(report)
    total = len(parts)
    for index, part in enumerate(parts, start=1):
        if total > 1:
            part = f"💎 PREMIUM COIN MİKROSKOP ({index}/{total})\n\n" + part
        _send(part)

    print("Coin Detay Analizi tamamlandı. Telegram parça:", total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())