from __future__ import annotations

import re

import coin_analysis_runtime as base


PRESENTATION_VERSION = "COIN_DETAIL_PREMIUM_UI_V3_EARLY_BREAKOUT_2026_08_24"
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
    rendered = rendered.replace(
        "🛡 Canlı Premium mantığı korunur • COIN_DETAIL_PREMIUM_UI_V2_2026_08_23",
        f"🛡 Canlı Premium mantığı korunur • {PRESENTATION_VERSION}",
    )
    return rendered


def _install_early_breakout_preview() -> None:
    """Keep Coin Microscope candidate order synchronized with the live runner."""
    import coin_analyzer as analyzer
    import premium_early_breakout as early

    original_candidate = analyzer._premium_candidate
    original_route_name = analyzer._route_name
    original_profit_factory = analyzer.premium_runner._make_profit_gate

    def candidate_with_early(
        exchange,
        symbol,
        df15m,
        df1h,
        df4h,
        current_price,
        pending_gate,
    ):
        signal, context = original_candidate(
            exchange,
            symbol,
            df15m,
            df1h,
            df4h,
            current_price,
            pending_gate,
        )
        if isinstance(signal, dict):
            return signal, context

        try:
            adaptive_fetch = analyzer.allcoins.make_adaptive_fetcher(analyzer.bot.fetch_df)
            df5m = adaptive_fetch(
                exchange,
                symbol,
                getattr(analyzer.bot, "RADAR_TIMEFRAME", "5m"),
                int(getattr(analyzer.bot, "RADAR_LIMIT", 240)),
                min_len=60,
            )
            base_result = analyzer.movement_v2.analyze(
                symbol,
                df5m,
                df15m,
                df1h,
                df4h,
                current_price,
            )
            promoted = early.analyze_live_candidate(
                symbol,
                base_result,
                current_price,
                allow_extra_flow=True,
            )
        except Exception as exc:
            print("Coin Analyzer Early Breakout önizleme hatası:", type(exc).__name__, exc)
            promoted = None

        if isinstance(promoted, dict):
            return promoted, {
                "eligible": True,
                "status": "Movement Start V2/V3 üzerinden Premium Early Breakout adayı oluştu",
                "direction": promoted.get("direction"),
            }
        return signal, context

    def route_name(signal):
        if isinstance(signal, dict) and str(signal.get("source") or "").upper() == early.SOURCE:
            return "Early Breakout"
        return original_route_name(signal)

    def profit_factory(original, gate, pending_gate):
        legacy = original_profit_factory(original, gate, pending_gate)

        def wrapped(signal, current_price):
            if early.strong_direct_allowed(
                signal,
                current_price,
                original,
                analyzer.profit,
            ):
                signal["premium_confirmation"] = {
                    "version": early.VERSION,
                    "status": "EARLY_BREAKOUT_DIRECT",
                    "confirmed_at": analyzer.bot.now_ts(),
                }
                return True, "Premium Early Breakout güçlü direkt giriş"
            return legacy(signal, current_price)

        return wrapped

    analyzer._premium_candidate = candidate_with_early
    analyzer._route_name = route_name
    analyzer.premium_runner._make_profit_gate = profit_factory


base._modernize_report = _modernize_report
base.PRESENTATION_VERSION = PRESENTATION_VERSION
_install_early_breakout_preview()


if __name__ == "__main__":
    raise SystemExit(base.main())
