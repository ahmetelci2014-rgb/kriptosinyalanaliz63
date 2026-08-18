"""Hızlı Scalp Radar canlı giriş noktası.

Canlı ATAK kapanış gücü eşiklerini tek config kaynağından uygular ve yalnız
62/38 -> 70/30 farkı yüzünden elenen eski ATAK adaylarını counterfactual
gölgede kaydeder. Radar motorunun gerçek Telegram/TP/SL/BE davranışını değiştirmez.
"""
from __future__ import annotations

from typing import Any, Callable

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


def run(radar: Any | None = None) -> None:
    if radar is None:
        import scalp_radar as radar  # type: ignore[no-redef]

    apply_live_thresholds(radar)
    print(
        "ATAK_SCALP canlı kalite guardı:",
        f"LONG close_power >= {cfg.LIVE_ATTACK_LONG_MIN_CLOSE_POWER:g}",
        f"| SHORT close_power <= {cfg.LIVE_ATTACK_SHORT_MAX_CLOSE_POWER:g}",
    )

    # Önce daha önce elenen sanal adayların sonuçlarını sessizce güncelle.
    try:
        guard.update_shadow(radar.get_exchange())
    except Exception as exc:
        # Gölge ölçümünün arızası canlı Scalp akışını durduramaz.
        print("ATAK guard shadow takip atlandı:", type(exc).__name__)

    original = radar.analyze_attack_side
    radar.analyze_attack_side = make_attack_wrapper(radar, original)
    apply_live_thresholds(radar)
    radar.main()


if __name__ == "__main__":
    run()
