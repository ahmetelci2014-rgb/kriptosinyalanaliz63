"""Shared safety text for real Telegram entry messages."""
from __future__ import annotations
from typing import Any, Callable

NOTICE = (
    "\n\n🛡️ İŞLEM DİSİPLİNİ\n"
    "• SL tetiklenirse işlem tezi biter; stop genişletilmez.\n"
    "• Kontrolsüz maliyet düşürme yok; yalnız sistem ayrıca SMART RECOVERY DCA1 UYGUN mesajı verirse tek planlı DCA1 değerlendirilebilir.\n"
    "• Fiyat mesajdaki girişten belirgin uzaklaştıysa peşinden koşma.\n"
    "• Kaldıraç büyütmek sinyal kalitesini artırmaz; risk küçük tutulmalı."
)


def make_entry_safety_sender(original: Callable[..., Any]) -> Callable[..., Any]:
    if getattr(original, "_entry_safety_wrapped", False):
        return original

    def wrapped(message: Any, *args: Any, **kwargs: Any) -> Any:
        text = str(message or "")
        is_real_entry = (
            text.startswith("✅ İŞLEM GİRİŞİ")
            or text.startswith("✅ GİRİŞ ONAYLANDI")
            or "🚀 SCALP SİNYALİ" in text
            or "🚀 PUMP/DUMP SİNYALİ" in text
            or "🚀 TREND DEVAM SİNYALİ" in text
            or "MTF FUTURES SİNYALİ" in text
        )
        if is_real_entry and "🛡️ İŞLEM DİSİPLİNİ" not in text:
            text += NOTICE
        return original(text, *args, **kwargs)

    wrapped._entry_safety_wrapped = True  # type: ignore[attr-defined]
    return wrapped
