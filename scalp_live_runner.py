"""Hızlı Scalp Radar canlı giriş noktası.

Canlı ATAK kalite eşiklerini korur. PREWATCH/EARLY adaylarının tamamı arka
planda performans için kaydedilir; Telegram'a yalnız gerçekten erken ve güçlü
olanlar çıkar. Gerçek Scalp sinyalleri ayrıca açıkça "İŞLEM GİRİŞİ" olarak
etiketlenir. Aynı coindeki eski ters-yön sinyal yeni fırsatı susturmaz.
Gerçek emir açmaz.
"""
from __future__ import annotations

from typing import Any, Callable

import opportunity_capture as capture
import scalp_attack_guard_shadow as guard
import scalp_quality_config as cfg

# Telegram yalnız en güçlü erken adayları görür. Arka plan kayıt mantığı değişmez.
VISIBLE_PREWATCH_MIN_SCORE = 90
VISIBLE_PREWATCH_MAX_BREAKOUT_DISTANCE_PERCENT = 0.10
VISIBLE_PREWATCH_MIN_VOLUME_SUPPORT = 1.10

VISIBLE_EARLY_MIN_SCORE = 85
VISIBLE_EARLY_MIN_VOLUME_SUPPORT = 1.25
VISIBLE_EARLY_MIN_15M_MOVE = 1.00

MAX_VISIBLE_PREWATCH_PER_RUN = 1
MAX_VISIBLE_EARLY_PER_RUN = 1


def apply_live_thresholds(radar: Any) -> None:
    radar.ATTACK_LONG_MIN_CLOSE_POWER = cfg.LIVE_ATTACK_LONG_MIN_CLOSE_POWER
    radar.ATTACK_SHORT_MAX_CLOSE_POWER = cfg.LIVE_ATTACK_SHORT_MAX_CLOSE_POWER


def apply_legacy_thresholds(radar: Any) -> None:
    radar.ATTACK_LONG_MIN_CLOSE_POWER = cfg.LEGACY_ATTACK_LONG_MIN_CLOSE_POWER
    radar.ATTACK_SHORT_MAX_CLOSE_POWER = cfg.LEGACY_ATTACK_SHORT_MAX_CLOSE_POWER


def compare_attack(
    radar: Any,
    original: Callable[..., tuple[Any, Any]],
    *args: Any,
    **kwargs: Any,
) -> tuple[Any, Any, Any, Any]:
    """Aynı market snapshotını önce legacy, sonra live eşikle değerlendirir."""
    apply_legacy_thresholds(radar)
    legacy_signal, legacy_debug = original(*args, **kwargs)
    apply_live_thresholds(radar)
    live_signal, live_debug = original(*args, **kwargs)
    apply_live_thresholds(radar)
    return legacy_signal, legacy_debug, live_signal, live_debug


def make_attack_wrapper(
    radar: Any,
    original: Callable[..., tuple[Any, Any]],
    recorder: Callable[..., Any] = guard.record_candidate,
) -> Callable[..., tuple[Any, Any]]:
    def wrapped(*args: Any, **kwargs: Any) -> tuple[Any, Any]:
        legacy_signal, legacy_debug, live_signal, live_debug = compare_attack(
            radar, original, *args, **kwargs
        )
        if legacy_signal is not None and live_signal is None:
            try:
                recorder(legacy_signal, legacy_debug, live_debug)
            except Exception as exc:
                print("ATAK guard shadow aday kayıt hatası:", type(exc).__name__)
        return live_signal, live_debug

    return wrapped


def safe_number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def should_surface_early(stage: str, item: Any) -> bool:
    """Arka plandaki adayı Telegram'a çıkaracak kadar güçlü mü?"""
    if not isinstance(item, dict):
        return False

    score = safe_number(item.get("score"))
    breakout = bool(item.get("breakout"))

    if stage == "PREWATCH":
        distance = safe_number(item.get("breakout_distance"), 999.0)
        volume_support = max(
            safe_number(item.get("vol1")),
            safe_number(item.get("rolling_vol3")),
        )
        return (
            score >= VISIBLE_PREWATCH_MIN_SCORE
            and volume_support >= VISIBLE_PREWATCH_MIN_VOLUME_SUPPORT
            and (
                breakout
                or distance <= VISIBLE_PREWATCH_MAX_BREAKOUT_DISTANCE_PERCENT
            )
        )

    if stage == "EARLY":
        volume_support = max(
            safe_number(item.get("vol1")),
            safe_number(item.get("rolling_vol5")),
        )
        move15 = abs(safe_number(item.get("live_move15")))
        return (
            score >= VISIBLE_EARLY_MIN_SCORE
            and volume_support >= VISIBLE_EARLY_MIN_VOLUME_SUPPORT
            and (
                breakout
                or move15 >= 1.20
            )
            and move15 >= VISIBLE_EARLY_MIN_15M_MOVE
        )

    return False


def make_clear_signal_sender(
    original: Callable[..., Any],
) -> Callable[..., Any]:
    """Gerçek Scalp girişini erken uyarılardan görsel olarak ayır."""
    if getattr(original, "_clear_scalp_entry_wrapped", False):
        return original

    def wrapped(message: Any, *args: Any, **kwargs: Any) -> Any:
        text = str(message or "")
        if text.startswith("🚀 SCALP SİNYALİ"):
            text = (
                "✅ İŞLEM GİRİŞİ — SCALP\n"
                "Giriş + TP + SL hazır. Bu, erken izleme mesajı değildir.\n\n"
                + text
            )
        return original(text, *args, **kwargs)

    wrapped._clear_scalp_entry_wrapped = True  # type: ignore[attr-defined]
    return wrapped


def make_visible_early_recorder(
    radar: Any,
    original: Callable[..., Any],
) -> Callable[..., Any]:
    """Tüm adayları kaydet; yalnız seçilmiş güçlü adayları Telegram'a çıkar."""
    if getattr(original, "_visible_early_wrapped", False):
        return original

    visible_count = {"PREWATCH": 0, "EARLY": 0}

    def wrapped(stage: Any, item: Any, *args: Any, **kwargs: Any) -> Any:
        record_id = original(stage, item, *args, **kwargs)
        stage_name = str(stage or "").upper()

        limit = (
            MAX_VISIBLE_PREWATCH_PER_RUN
            if stage_name == "PREWATCH"
            else MAX_VISIBLE_EARLY_PER_RUN
        )

        if (
            record_id
            and stage_name in visible_count
            and visible_count[stage_name] < limit
            and should_surface_early(stage_name, item)
        ):
            message = str((item or {}).get("message") or "").strip()
            if message:
                visible_count[stage_name] += 1
                radar.send_telegram(
                    "⚠️ SADECE TAKİP — HENÜZ İŞLEM AÇMA\n"
                    "Gerçek işlem girişi ayrıca Giriş + TP + SL ile gelecek.\n\n"
                    "🚨 HIZLI FIRSAT YAKALAMA KATMANI\n\n"
                    + message,
                    delivery_key=f"VISIBLE_{stage_name}|{record_id}",
                )
        return record_id

    wrapped._visible_early_wrapped = True  # type: ignore[attr-defined]
    return wrapped


def apply_opportunity_capture(radar: Any) -> None:
    # Motor PREWATCH/EARLY adaylarını üretmeye ve arka planda kaydetmeye devam eder.
    radar.SEND_EARLY_ALERTS_TO_TELEGRAM = True
    radar.SEND_PREWATCH_ALERTS_TO_TELEGRAM = True

    # Eski ters-yön açık sinyal yeni fırsatın analizini durdurmaz.
    # Aynı-yön duplicate ve portföy limitleri aşağı akışta korunur.
    radar.has_open_same_symbol = lambda state, symbol: False
    radar.evaluate_portfolio_risk = capture.make_opposite_direction_evaluator(
        radar.evaluate_portfolio_risk
    )

    radar.send_telegram = make_clear_signal_sender(radar.send_telegram)
    radar.record_scalp_performance = make_visible_early_recorder(
        radar,
        radar.record_scalp_performance,
    )


def run(radar: Any | None = None) -> None:
    if radar is None:
        import scalp_radar as radar  # type: ignore[no-redef]

    apply_live_thresholds(radar)
    apply_opportunity_capture(radar)
    print(
        "ATAK_SCALP canlı kalite guardı:",
        f"LONG close_power >= {cfg.LIVE_ATTACK_LONG_MIN_CLOSE_POWER:g}",
        f"| SHORT close_power <= {cfg.LIVE_ATTACK_SHORT_MAX_CLOSE_POWER:g}",
    )
    print(
        "Fırsat yakalama: tüm PREWATCH/EARLY arka planda | "
        "Telegram yalnız güçlü erken aday | gerçek giriş ayrı etiketli"
    )

    try:
        guard.update_shadow(radar.get_exchange())
    except Exception as exc:
        print("ATAK guard shadow takip atlandı:", type(exc).__name__)

    original = radar.analyze_attack_side
    radar.analyze_attack_side = make_attack_wrapper(radar, original)
    apply_live_thresholds(radar)
    radar.main()


if __name__ == "__main__":
    run()
