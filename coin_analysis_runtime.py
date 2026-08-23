from __future__ import annotations

import os
import sys
import traceback
from typing import Any

import requests


TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
RAW_SYMBOL = os.getenv("SYMBOL") or (sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT")
TELEGRAM_LIMIT = 3800


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
        report = analyzer.analyze_coin(symbol)
    except RuntimeError as exc:
        # Kontrat/veri yetersizliği programlama hatası değildir. Kullanıcıya
        # BEKLE raporu verilir ve workflow gereksiz yere kırmızı olmaz.
        report = (
            "⏳ COIN DETAY ANALİZİ — BEKLE\n\n"
            f"Coin: {symbol}\n"
            f"Neden: {exc}\n\n"
            "Analiz motoru çalıştı ancak güvenli karar için gerekli OKX verisi tamamlanmadı."
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

    parts = _split(report)
    total = len(parts)
    for index, part in enumerate(parts, start=1):
        header = f"📊 COIN DETAY ANALİZİ — {symbol}"
        if total > 1:
            header += f" ({index}/{total})"
        _send(header + "\n\n" + part)

    print("Coin Detay Analizi tamamlandı. Telegram parça:", total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
