"""Big Move shadow-only entry point for Profit Mode V1.

The route engine keeps collecting virtual outcomes, but no Big Move candidate
is sent to Telegram until it has enough closed-route evidence. Real exchange
orders remain disabled.
"""
from __future__ import annotations

import big_move_route as base
import big_move_route_runner as runner


def _shadow_accept(_signal):
    # The live runner opens a virtual route only after send_signal succeeds.
    # Returning True here preserves virtual outcome tracking without Telegram.
    return True


def run() -> None:
    base.send_signal = _shadow_accept
    runner.main()

    state = base.load_state()
    stats = state.setdefault("run_stats", {})
    opened = int(stats.pop("telegram_opened", 0) or 0)
    stats["shadow_opened"] = opened
    state["mode_override"] = "PROFIT_MODE_V1_SHADOW_ONLY_NO_TELEGRAM"
    base.core.atomic_save_json(base.STATE_FILE, state)

    print(
        "PROFIT MODE V1 / BIG MOVE | Telegram KAPALI | "
        f"bu tur sanal açılan rota {opened}"
    )


if __name__ == "__main__":
    run()
