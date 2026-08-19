"""Hızlı Scalp Radar canlı giriş noktası.

Canlı ATAK kapanış gücü eşiklerini tek config kaynağından uygular, 62/38 ->
70/30 farkı yüzünden elenen eski ATAK adaylarını counterfactual gölgede izler
ve GPS benzeri hızlı fırsatları kaçırmamak için PREWATCH/EARLY katmanlarını
Telegram'da görünür yapar. Gerçek emir açmaz.
"""
from __future__ import annotations

from typing import Any, Callable

import opportunity_capture as capture
import scalp_attack_guard_shadow as guard
import scalp_quality_config as cfg


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


def make_visible_early_recorder(
    radar: Any,
    original: Callable[..., Any],
) -> Callable[..., Any]:
    """PREWATCH/EARLY kaydını koru, ardından Telegram'da görünür yap."""
    if getattr(original, "_visible_early_wrapped", False):
        return original

    def wrapped(stage: Any, item: Any, *args: Any, **kwargs: Any) -> Any:
        record_id = original(stage, item, *args, **kwargs)
        stage_name = str(stage or "").upper()
        should_send = (
            (stage_name == "PREWATCH" and bool(radar.SEND_PREWATCH_ALERTS_TO_TELEGRAM))
            or (stage_name == "EARLY" and bool(radar.SEND_EARLY_ALERTS_TO_TELEGRAM))
        )
        if record_id and should_send and isinstance(item, dict):
            message = str(item.get("message") or "").strip()
            if message:
                radar.send_telegram(
                    "🚨 HIZLI FIRSAT YAKALAMA KATMANI\n\n" + message,
                    delivery_key=f"{stage_name}|{record_id}",
                )
        return record_id

    wrapped._visible_early_wrapped = True  # type: ignore[attr-defined]
    return wrapped


def apply_opportunity_capture(radar: Any) -> None:
    # GPS örneğinde olduğu gibi PREWATCH/EARLY artık kullanıcıya görünür.
    radar.SEND_EARLY_ALERTS_TO_TELEGRAM = True
    radar.SEND_PREWATCH_ALERTS_TO_TELEGRAM = True

    # Aynı coinde eski LONG varsa SHORT (ve tersi) yine analiz edilir.
    # Aynı yön duplicate/portföy filtresi daha sonra çalışmaya devam eder.
    radar.has_open_same_symbol = lambda state, symbol: False

    radar.evaluate_portfolio_risk = capture.make_opposite_direction_evaluator(
        radar.evaluate_portfolio_risk
    )
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
    print("Fırsat yakalama: PREWATCH/EARLY Telegram AÇIK | ters-yön fırsatı ENGELLENMEZ")

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
