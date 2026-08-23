from __future__ import annotations

import re

import coin_analysis_runtime as base


PRESENTATION_VERSION = "COIN_DETAIL_PREMIUM_UI_V2_2026_08_23"
_original_modernize = base._modernize_report


def _movement_prep_context(raw_report: str):
    text = str(raw_report or "")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("• 5M:"):
            continue
        upper = stripped.upper()
        if " PREP" not in upper:
            return None
        direction = "LONG" if "LONG PREP" in upper else "SHORT" if "SHORT PREP" in upper else "PREP"
        match = re.search(r"skor\s+(\d+)\s*/\s*100", stripped, flags=re.IGNORECASE)
        score = int(match.group(1)) if match else None
        return {"direction": direction, "score": score}
    return None


def _modernize_report(raw_report: str) -> str:
    rendered = _original_modernize(raw_report)
    prep = _movement_prep_context(raw_report)

    if prep:
        direction = prep["direction"]
        score = prep.get("score")
        score_text = f" {score}/100" if score is not None else ""

        rendered = rendered.replace(
            f"• {direction} PREP",
            f"• {direction} PREP{score_text} • erken yapı",
        )

        if "🧬 Flow  Sorgulanmadı • 5M yapı adayı yok" in rendered:
            if score is not None:
                flow_text = (
                    f"🧬 Flow  PREP {score}/100 • V3 eşiği 72 • henüz sorgulanmadı"
                )
            else:
                flow_text = "🧬 Flow  PREP var • V3 sorgu eşiği altında"
            rendered = rendered.replace(
                "🧬 Flow  Sorgulanmadı • 5M yapı adayı yok",
                flow_text,
            )

    rendered = rendered.replace(
        "🛡 Canlı Premium mantığı korunur • COIN_DETAIL_PREMIUM_UI_V1_2026_08_23",
        f"🛡 Canlı Premium mantığı korunur • {PRESENTATION_VERSION}",
    )
    return rendered


base._modernize_report = _modernize_report
base.PRESENTATION_VERSION = PRESENTATION_VERSION


if __name__ == "__main__":
    raise SystemExit(base.main())
