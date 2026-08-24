"""Shared Telegram safety/compact policy for Premium entry messages."""
from __future__ import annotations

import re
import sys
from typing import Any, Callable

NOTICE = (
    "\n\n🛡️ İŞLEM DİSİPLİNİ\n"
    "• SL tetiklenirse işlem tezi biter; stop genişletilmez.\n"
    "• Kontrolsüz maliyet düşürme yok; yalnız sistem ayrıca SMART RECOVERY DCA1 UYGUN mesajı verirse tek planlı DCA1 değerlendirilebilir.\n"
    "• Fiyat mesajdaki girişten belirgin uzaklaştıysa peşinden koşma.\n"
    "• Kaldıraç büyütmek sinyal kalitesini artırmaz; risk küçük tutulmalı."
)


PREMIUM_ENTRY_PREFIX = "✅ İŞLEM GİRİŞİ — PREMIUM"
EARLY_ENTRY_PREFIX = "✅ İŞLEM GİRİŞİ — PREMIUM ERKEN HAREKET"


def _is_premium_entry(text: str) -> bool:
    return bool(
        text.startswith(PREMIUM_ENTRY_PREFIX)
        or text.startswith("🚀 PREMIUM FUTURES")
    )


def _compact_premium_entry(text: str) -> str:
    """Keep Telegram focused on title, side/symbol, Entry, TP1/2/3 and SL.

    Full score, flow, volume, portfolio, leverage and diagnosis metadata remain
    on the signal/ledger object; only the Telegram presentation is shortened.
    """
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if not lines:
        return text

    kept = []
    for line in lines:
        folded = line.casefold()
        if line.startswith(PREMIUM_ENTRY_PREFIX) or line.startswith("🚀 PREMIUM FUTURES"):
            kept.append(line)
            continue

        if (
            ("long" in folded or "short" in folded)
            and (
                "|" in line
                or "yön:" in folded
                or "yon:" in folded
                or line.startswith(("🟢", "🔴"))
            )
        ):
            kept.append(line)
            continue

        if any(
            token in folded
            for token in (
                "giriş:",
                "giris:",
                "entry:",
                "tp1:",
                "tp2:",
                "tp3:",
                "sl:",
            )
        ):
            kept.append(line)
            continue

        if any(
            folded.startswith(prefix)
            for prefix in (
                "coin:",
                "parite:",
                "sembol:",
                "symbol:",
            )
        ):
            kept.append(line)

    # If an unforeseen legacy formatter does not expose enough structured
    # lines, do not risk sending a mutilated message.
    has_entry = any(
        any(token in line.casefold() for token in ("giriş:", "giris:", "entry:"))
        for line in kept
    )
    has_tp = any("tp1:" in line.casefold() for line in kept)
    has_sl = any("sl:" in line.casefold() for line in kept)
    if not (has_entry and has_tp and has_sl):
        return text

    return "\n".join(kept)


def _early_fast_metrics(text: str) -> dict[str, Any]:
    stage = None
    base_score = 0
    live_score = 0
    flow_score = 0
    flow_confirmed = False

    match = re.search(
        r"Yapı:\s*(PREP|ARMED|TRIGGER)\s*[•|\-]?\s*V2\s*(\d+)\s*/\s*100",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        stage = str(match.group(1) or "").upper()
        base_score = int(match.group(2) or 0)

    match = re.search(r"Premium skor:\s*(\d+)\s*/\s*100", text, flags=re.IGNORECASE)
    if match:
        live_score = int(match.group(1) or 0)

    match = re.search(
        r"Order Flow:\s*(✅\s*)?(\d+)\s*/\s*100",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        flow_confirmed = bool(match.group(1))
        flow_score = int(match.group(2) or 0)

    return {
        "stage": stage,
        "base_score": base_score,
        "live_score": live_score,
        "flow_score": flow_score,
        "flow_confirmed": flow_confirmed,
    }


def _elite_early_fast_send(text: str) -> bool:
    """Reserve immediate per-run slots for genuinely top early candidates.

    Lower-tier but still valid Early Breakout candidates are not rejected; they
    simply fall back to the normal end-of-scan quality ranking. This prevents an
    early ARMED/weak-flow candidate from consuming both live slots before a later
    ONT-like TRIGGER reaches much stronger evidence in the same scan.
    """
    metrics = _early_fast_metrics(text)
    stage = metrics["stage"]
    base_score = int(metrics["base_score"] or 0)
    live_score = int(metrics["live_score"] or 0)
    flow_score = int(metrics["flow_score"] or 0)
    flow_confirmed = bool(metrics["flow_confirmed"])

    trigger_elite = bool(
        stage == "TRIGGER"
        and base_score >= 94
        and live_score >= 99
    )
    flow_elite = bool(
        flow_confirmed
        and flow_score >= 80
        and live_score >= 98
    )
    return trigger_elite or flow_elite


def _called_from_early_fast_send(max_depth: int = 10) -> bool:
    """Detect the first intra-scan fast-send attempt without affecting batch send."""
    frame = sys._getframe(1)
    depth = 0
    try:
        while frame is not None and depth < max_depth:
            if frame.f_code.co_name == "_try_fast_send":
                return True
            frame = frame.f_back
            depth += 1
    finally:
        del frame
    return False


def make_entry_safety_sender(original: Callable[..., Any]) -> Callable[..., Any]:
    if getattr(original, "_entry_safety_wrapped", False):
        return original

    def wrapped(message: Any, *args: Any, **kwargs: Any) -> Any:
        text = str(message or "")

        # Quality-ranked fast-send policy: only elite Early Breakout candidates
        # consume the immediate slots. A non-elite candidate returns False only
        # during _try_fast_send; the caller then keeps it for the normal batch
        # ranking, where this wrapper will allow it if selected.
        if (
            text.startswith(EARLY_ENTRY_PREFIX)
            and _called_from_early_fast_send()
            and not _elite_early_fast_send(text)
        ):
            metrics = _early_fast_metrics(text)
            print(
                "EARLY QUALITY DEFER | normal kalite sıralamasına bırakıldı:",
                metrics,
            )
            return False

        # Premium Telegram is deliberately compact. Everything removed here is
        # still present in open_signals/trade_ledger and diagnostic state.
        if _is_premium_entry(text):
            text = _compact_premium_entry(text)
            return original(text, *args, **kwargs)

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
