"""Scalp live entry point with active-market countertrend protection."""
from __future__ import annotations

from typing import Any, Callable

import market_impulse_guard as impulse
import scalp_live_runner as live


def make_impulse_reaction_guard(
    original: Callable[..., tuple[Any, Any]],
) -> Callable[..., tuple[Any, Any]]:
    if getattr(original, "_market_impulse_guard_wrapped", False):
        return original

    def wrapped(*args: Any, **kwargs: Any) -> tuple[Any, Any]:
        signal, debug = original(*args, **kwargs)
        if not isinstance(signal, dict):
            return signal, debug

        direction = str(signal.get("direction") or "").upper()
        symbol = str(signal.get("symbol") or "")
        opposing = impulse.recent_opposing_strong_impulse(symbol, direction)
        if opposing:
            print(
                "TEPKİ_SCALP impuls guard:", symbol, direction,
                "engellendi | canlı impuls", opposing.get("direction"),
                "| 5/15/30M", opposing.get("move5_percent"),
                opposing.get("move15_percent"), opposing.get("move30_percent"),
            )
            return None, debug
        return signal, debug

    wrapped._market_impulse_guard_wrapped = True  # type: ignore[attr-defined]
    return wrapped


def run(radar: Any | None = None) -> None:
    if radar is None:
        import scalp_radar as radar  # type: ignore[no-redef]

    radar.analyze_reaction_side = make_impulse_reaction_guard(radar.analyze_reaction_side)
    print("Scalp canlı impuls koruması AKTİF: güçlü ters impuls varken TEPKİ_SCALP yok.")
    live.run(radar)


if __name__ == "__main__":
    run()
