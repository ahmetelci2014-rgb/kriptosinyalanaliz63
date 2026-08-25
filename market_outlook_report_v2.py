"""Compatibility helper retained for Market Outlook V3.

V3 imports ``scenario_weights`` from the former V2 formatter. The full V2
presentation was removed during cleanup, but this shared calculation is still
required by ``market_outlook_report_v3.py``. Keeping the helper here restores
that dependency without changing Market Outlook scoring or live trade rules.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Tuple

VERSION = "MARKET_OUTLOOK_REPORT_V2_COMPAT_2026_08_25"


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def scenario_weights(outlook: Dict[str, Any]) -> Tuple[int, int, int]:
    """Return model scenario weights; these are not calibrated probabilities."""
    score = max(-100.0, min(100.0, _sf(outlook.get("score_24h"))))
    confidence = max(
        45.0,
        min(90.0, _sf(outlook.get("confidence_24h"), 50.0)),
    )

    directional = score * (0.20 + (confidence - 45.0) / 450.0)
    up = 34.0 + directional
    down = 34.0 - directional
    flat = max(18.0, 32.0 - abs(score) * 0.08)

    up = max(5.0, up)
    down = max(5.0, down)
    flat = max(12.0, flat)
    total = up + down + flat
    values = [
        int(round(up / total * 100)),
        int(round(flat / total * 100)),
        int(round(down / total * 100)),
    ]
    values[0] += 100 - sum(values)
    return values[0], values[1], values[2]
