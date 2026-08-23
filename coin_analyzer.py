# coin_analyzer.py
# Coin Detay Analizi V2.1 — Premium Mikroskop / Live-Guard Sync
#
# Amaç:
# - Ayrı bir skor/karar motoru çalıştırmaz.
# - Canlı Premium sistemin mevcut karar bileşenlerini doğrudan çağırır.
# - Ana taramadaki stop/kapanış cooldown, duplicate, market guard, giriş/maliyet,
#   açık risk kapasitesi ve Portfolio Risk kapılarını tek coin için görünür kılar.
# - 1D, Market Outlook, Funding/OI ve V3 order-flow'u teşhis bağlamı olarak gösterir.
# - Reversal Capture / Trend Continuation / young-new coin yollarını canlı Premium sırasıyla kullanır.
# - Gerçek işlem planını yalnız canlı Premium kapıları geçildiğinde, sinyalin kendi Entry/TP/SL alanlarından gösterir.
#
# Emir açmaz. Yalnızca analiz raporu üretir ve TOKEN / CHAT_ID varsa Telegram'a gönderir.

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from typing import Any, Dict, Optional, Tuple

import requests

import main as bot
import market_outlook_engine as outlook_engine
import movement_start_v2_shadow as movement_v2
import movement_start_v3_orderflow_shadow as movement_v3
import opportunity_capture
import portfolio_risk
import premium_all_coins as allcoins
import premium_confirmation as confirmation
import premium_continuation as continuation
import premium_profit_runner as premium_runner
import premium_reversal_capture as reversal
import profitability_engine as profit
import strategy


VERSION = "COIN_ANALYZER_V2_1_PREMIUM_MICROSCOPE_GUARD_SYNC_2026_08_23"

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
SYMBOL = os.getenv("SYMBOL") or (sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT")

# premium_profit_runner.run() canlıda bu iki runtime ayarını uygular.
PREMIUM_ENABLE_5M_EARLY_TRADE = False
PREMIUM_MAX_LATE_ENTRY_DISTANCE_PERCENT = 0.35
TARGET_OI_HISTORY_TIMEFRAME = "5m"
TARGET_OI_HISTORY_LIMIT = 3


def normalize_symbol(symbol: Any) -> str:
    value = (
        str(symbol or "")
        .upper()
        .strip()
        .replace("/USDT:USDT", "USDT")
        .replace(":USDT", "")
        .replace("/", "")
        .replace("-", "")
        .replace("_", "")
        .replace(":", "")
    )
    if value and not value.endswith("USDT"):
        value += "USDT"
    return value


def safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value in (None, "", "-"):
            return default
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            return default
        return number
    except Exception:
        return default


def send_telegram(message: str) -> bool:
    if not TOKEN or not CHAT_ID:
        print("TOKEN / CHAT_ID yok. Telegram gönderilmedi.")
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": message},
            timeout=25,
        )
        print("Telegram cevap:", response.status_code)
        return response.status_code == 200
    except Exception as exc:
        print("Telegram hatası:", type(exc).__name__, exc)
        return False


def _copy_if_exists(source: str, destination: str) -> None:
    try:
        if os.path.exists(source):
            shutil.copyfile(source, destination)
    except Exception:
        pass


def _latest_snapshot(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    if isinstance(value.get("outlook"), dict):
        return value
    if isinstance(value.get("snapshot"), dict):
        return value["snapshot"]
    rows = value.get("snapshots")
    if isinstance(rows, list):
        for row in reversed(rows):
            if isinstance(row, dict):
                return row
    return {}


def _fresh_market_outlook(exchange: Any, temp_dir: str) -> Dict[str, Any]:
    """Aynı Market Outlook motorunu Telegram kapalı ve geçici state ile çalıştır."""
    temp_state = os.path.join(temp_dir, "market_outlook_state.json")
    _copy_if_exists(outlook_engine.STATE_FILE, temp_state)
    try:
        result = outlook_engine.run(
            exchange,
            state_file=temp_state,
            token=None,
            chat_id=None,
            allow_telegram=False,
        )
        snapshot = _latest_snapshot(result)
        if snapshot:
            return snapshot
    except Exception as exc:
        print("Market Outlook canlı hesaplanamadı:", type(exc).__name__, exc)

    # Canlı hesap hata verirse son kayıt yalnız teşhis için kullanılır.
    try:
        return _latest_snapshot(outlook_engine.load_state(outlook_engine.STATE_FILE))
    except Exception:
        return {}


def _outlook_text(snapshot: Dict[str, Any], direction: Optional[str] = None) -> Dict[str, Any]:
    outlook = snapshot.get("outlook") if isinstance(snapshot, dict) else {}
    outlook = outlook if isinstance(outlook, dict) else {}
    dir6 = str(outlook.get("direction_6h") or "").upper()
    wanted = str(direction or "").upper()
    aligned: Optional[bool] = None
    if wanted == "LONG" and dir6:
        aligned = dir6 == "UP"
    elif wanted == "SHORT" and dir6:
        aligned = dir6 == "DOWN"
    flags = outlook.get("risk_flags") if isinstance(outlook.get("risk_flags"), list) else []
    return {
        "bias_6h": str(outlook.get("bias_6h") or "VERİ YOK"),
        "bias_24h": str(outlook.get("bias_24h") or "VERİ YOK"),
        "direction_6h": dir6,
        "direction_24h": str(outlook.get("direction_24h") or "").upper(),
        "confidence_6h": int(safe_float(outlook.get("confidence_6h"), 0) or 0),
        "confidence_24h": int(safe_float(outlook.get("confidence_24h"), 0) or 0),
        "long_suitability": safe_float(outlook.get("long_suitability")),
        "short_suitability": safe_float(outlook.get("short_suitability")),
        "risk_flags": flags,
        "aligned_with_signal": aligned,
    }


def _target_derivatives(exchange: Any, symbol: str) -> Dict[str, Any]:
    market_symbol = bot.to_okx_symbol(symbol)
    funding: Optional[float] = None
    oi: Optional[float] = None
    oi_change: Optional[float] = None
    try:
        item = exchange.fetch_funding_rate(market_symbol) or {}
        funding = safe_float(
            item.get("fundingRate")
            if item.get("fundingRate") is not None
            else (item.get("info") or {}).get("fundingRate")
        )
    except Exception:
        pass
    try:
        item = exchange.fetch_open_interest(market_symbol) or {}
        oi = safe_float(
            item.get("openInterestValue")
            if item.get("openInterestValue") is not None
            else item.get("openInterestAmount")
        )
        if oi is None:
            info = item.get("info") or {}
            oi = safe_float(info.get("oiUsd") or info.get("oiCcy") or info.get("oi"))
    except Exception:
        pass
    try:
        history_fn = getattr(exchange, "fetch_open_interest_history", None)
        if callable(history_fn):
            rows = history_fn(
                market_symbol,
                timeframe=TARGET_OI_HISTORY_TIMEFRAME,
                limit=TARGET_OI_HISTORY_LIMIT,
            )
            values = []
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                value = safe_float(
                    row.get("openInterestValue")
                    if row.get("openInterestValue") is not None
                    else row.get("openInterestAmount")
                )
                if value is None:
                    info = row.get("info") or {}
                    value = safe_float(info.get("oiUsd") or info.get("oiCcy") or info.get("oi"))
                if value and value > 0:
                    values.append(value)
            if len(values) >= 2 and values[0] > 0:
                oi_change = (values[-1] - values[0]) / values[0] * 100.0
    except Exception:
        pass

    threshold = float(getattr(outlook_engine, "FUNDING_CROWDING", 0.0005))
    if funding is None:
        funding_state = "veri yok"
    elif abs(funding) < threshold:
        funding_state = "sağlıklı / aşırı kalabalık değil"
    elif funding >= threshold:
        funding_state = "LONG tarafı kalabalık"
    else:
        funding_state = "SHORT tarafı kalabalık"

    if oi_change is None:
        oi_state = "OI değişimi ölçülemedi"
    elif oi_change >= 3.0:
        oi_state = f"OI hızlı artıyor (%{oi_change:+.2f})"
    elif oi_change <= -3.0:
        oi_state = f"OI hızlı azalıyor (%{oi_change:+.2f})"
    else:
        oi_state = f"OI dengeli (%{oi_change:+.2f})"

    return {
        "funding": funding,
        "funding_threshold": threshold,
        "funding_state": funding_state,
        "open_interest": oi,
        "oi_change_percent": oi_change,
        "oi_state": oi_state,
    }


def _one_day_context(exchange: Any, symbol: str) -> Dict[str, Any]:
    try:
        frame = outlook_engine.fetch_frame(exchange, symbol, "1d", 220)
        if frame is None or frame.empty:
            return {"direction": "NEUTRAL", "score": None, "text": "1D: veri yetersiz"}
        score = float(outlook_engine.frame_trend_score(frame))
        if score >= 20:
            return {"direction": "LONG", "score": round(score, 2), "text": "1D: Yukarı"}
        if score <= -20:
            return {"direction": "SHORT", "score": round(score, 2), "text": "1D: Aşağı"}
        return {"direction": "NEUTRAL", "score": round(score, 2), "text": "1D: Kararsız / yatay"}
    except Exception as exc:
        return {"direction": "NEUTRAL", "score": None, "text": f"1D: veri alınamadı ({type(exc).__name__})"}


def _frame_context(df: Any, label: str) -> Dict[str, Any]:
    """Yalnız raporlama özeti; Premium karar formülü değildir."""
    try:
        frame = strategy.add_indicators(df)
        if frame is None or len(frame) < 3:
            return {"direction": "NEUTRAL", "text": f"{label}: veri/indikatör yetersiz"}
        row = frame.iloc[-2]
        close = float(row["close"])
        ema20 = float(row["ema20"])
        ema50 = float(row["ema50"])
        slope = float(row["ema20_slope"])
        if close > ema20 >= ema50 and slope > 0:
            text = f"{label}: Yukarı / toparlanma"
            direction = "LONG"
        elif close < ema20 <= ema50 and slope < 0:
            text = f"{label}: Aşağı / zayıflama"
            direction = "SHORT"
        else:
            text = f"{label}: Kararsız / geçiş"
            direction = "NEUTRAL"
        return {
            "direction": direction,
            "text": text,
            "rsi": round(float(row["rsi"]), 2),
            "adx": round(float(row["adx"]), 2),
            "volume_ratio": round(float(row["volume_ratio"]), 2),
        }
    except Exception:
        return {"direction": "NEUTRAL", "text": f"{label}: özet üretilemedi"}


def _movement_v2_context(symbol: str, df5m: Any, df15m: Any, df1h: Any, df4h: Any, current_price: Any) -> Optional[Dict[str, Any]]:
    try:
        return movement_v2.analyze(symbol, df5m, df15m, df1h, df4h, current_price)
    except Exception as exc:
        print("Movement V2 analiz hatası:", type(exc).__name__, exc)
        return None


def _orderflow_context(symbol: str, base_result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """V3 canlı runner'daki sorgulama eşiğini kullanır; hard-gate değildir."""
    if not movement_v3.should_query(base_result):
        return {"queried": False, "reason": "V2 PREP/ARMED/TRIGGER adayı yok; canlı V3 de order-flow sorgulamaz."}
    direction = str((base_result or {}).get("direction") or "").upper()
    try:
        flow = movement_v3.fetch_order_flow(symbol)
    except Exception:
        flow = None
    if not isinstance(flow, dict):
        return {"queried": True, "available": False, "direction": direction, "reason": "OKX order-flow snapshot alınamadı."}

    long_score, long_conditions, _ = movement_v3.score_order_flow(flow, "LONG")
    short_score, short_conditions, _ = movement_v3.score_order_flow(flow, "SHORT")
    selected_score = long_score if direction == "LONG" else short_score
    conditions = long_conditions if direction == "LONG" else short_conditions
    confirm_score = int(getattr(movement_v3, "CONFIRM_SCORE", 65))
    pressure = (
        "Alıcı baskısı"
        if long_score >= short_score + 10
        else "Satıcı baskısı"
        if short_score >= long_score + 10
        else "Dengeli / karışık"
    )
    return {
        "queried": True,
        "available": True,
        "direction": direction,
        "pressure": pressure,
        "long_score": int(long_score),
        "short_score": int(short_score),
        "selected_score": int(selected_score),
        "confirmed": bool(selected_score >= confirm_score),
        "confirm_score": confirm_score,
        "conditions": conditions,
        "spread_bps": safe_float(flow.get("spread_bps")),
        "book_imbalance": safe_float(flow.get("book_imbalance")),
        "trade_imbalance": safe_float(flow.get("trade_imbalance")),
        "recent_trade_imbalance": safe_float(flow.get("recent_trade_imbalance")),
    }


def _route_name(signal: Optional[Dict[str, Any]]) -> str:
    if not isinstance(signal, dict):
        return "YOK"
    source = str(signal.get("source") or "15M_ENTRY").upper()
    return {
        "15M_ENTRY": "Klasik Premium MTF",
        continuation.SOURCE: "Trend Continuation",
        reversal.SOURCE: "Reversal Capture",
        "YOUNG_COIN_ENTRY": "Young Coin",
        "NEW_COIN_ENTRY": "New Coin",
    }.get(source, source)


def _copy_pending_state(temp_state: str) -> None:
    source = getattr(confirmation, "DEFAULT_STATE_FILE", "premium_pending_candidates.json")
    _copy_if_exists(source, temp_state)


def _build_preview_gates(temp_dir: str):
    """Canlı Premium gate'lerini geçici state ile çalıştır; repo state'ini kirletme."""
    reject_file = os.path.join(temp_dir, "profit_mode_rejections.json")
    pending_file = os.path.join(temp_dir, "premium_pending_candidates.json")
    _copy_pending_state(pending_file)
    gate = profit.PremiumGate(bot.TRADE_LEDGER_FILE, rejects=reject_file)
    pending_gate = confirmation.PendingConfirmationGate(gate, state_file=pending_file)

    class RunnerProxy:
        pass

    proxy = RunnerProxy()
    proxy.bot = bot
    proxy.profit = profit
    gate_factory = reversal.make_profit_gate_factory(proxy, premium_runner._make_profit_gate)
    entry_gate = gate_factory(bot.is_entry_still_valid, gate, pending_gate)
    return gate, pending_gate, entry_gate


def _live_prefilters(symbol: str) -> Dict[str, Any]:
    """main.py tarama başındaki hard prefilter sırasını read-only olarak yansıtır."""
    try:
        stopped = bool(bot.has_recent_stop(symbol))
    except Exception:
        return {
            "recent_stop_blocked": True,
            "recent_closed_blocked": False,
            "reason": "Yakın stop kontrolü çalışmadı; güvenli tarafta BEKLE.",
        }
    if stopped:
        return {
            "recent_stop_blocked": True,
            "recent_closed_blocked": False,
            "reason": "Yakın zamanda stop olduğu için canlı Premium bu coini taramada atlar.",
        }
    try:
        recent_filter = reversal.make_recent_closed_prefilter(bot, bot.has_recent_closed_signal)
        recent_closed = bool(recent_filter(symbol))
    except Exception:
        return {
            "recent_stop_blocked": False,
            "recent_closed_blocked": True,
            "reason": "Yakın kapanış/Reversal prefilter çalışmadı; güvenli tarafta BEKLE.",
        }
    return {
        "recent_stop_blocked": False,
        "recent_closed_blocked": recent_closed,
        "reason": (
            "Yakın kapanış cooldown aktif."
            if recent_closed
            else "Stop/kapanış prefilter uygun veya Reversal istisnası doğrulandı."
        ),
    }


def _premium_base_candidate(exchange: Any, symbol: str, df15m: Any, df1h: Any, df4h: Any, current_price: Any, pending_gate: Any) -> Optional[Dict[str, Any]]:
    """premium_profit_runner._make_pending_analyzer ile aynı aday sırası."""
    try:
        fresh = bot.analyze_mtf_trade(symbol, df15m, df1h, df4h, current_price)
    except Exception as exc:
        print("Klasik Premium analiz hatası:", type(exc).__name__, exc)
        fresh = None
    if isinstance(fresh, dict):
        return fresh

    try:
        trend_continue = continuation.analyze_continuation(symbol, df15m, df1h, df4h, current_price)
    except Exception as exc:
        print("Trend Continuation analiz hatası:", type(exc).__name__, exc)
        trend_continue = None
    if isinstance(trend_continue, dict):
        return trend_continue

    try:
        allcoins._EXCHANGE = exchange
        young = allcoins.analyze_young_coin(symbol, df15m, df1h, df4h, current_price)
    except Exception as exc:
        print("Young/New coin analiz hatası:", type(exc).__name__, exc)
        young = None
    if isinstance(young, dict):
        return young

    try:
        return pending_gate.fallback_signal(
            symbol=symbol,
            df15m=df15m,
            df1h=df1h,
            df4h=df4h,
            strategy_module=strategy,
        )
    except Exception:
        return None


def _premium_candidate(exchange: Any, symbol: str, df15m: Any, df1h: Any, df4h: Any, current_price: Any, pending_gate: Any) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """Canlı Reversal wrapper ile aynı yön-duyarlı aday seçimi.

    Bu fonksiyon yalnız _live_prefilters geçildikten sonra çağrılır. Böylece
    TP3 cooldown istisnası, canlıdaki should_probe_reversal şartı olmadan açılamaz.
    """
    try:
        context = reversal.recent_tp3_context(bot, symbol)
    except Exception:
        context = None

    base_signal = _premium_base_candidate(exchange, symbol, df15m, df1h, df4h, current_price, pending_gate)
    if context is None:
        return base_signal, {"eligible": False, "status": "Yok"}

    try:
        promoted = reversal._promote_existing_reversal(base_signal, context)
    except Exception:
        promoted = None
    if isinstance(promoted, dict):
        return promoted, {
            "eligible": True,
            "status": "Mevcut Premium kurulumu TP3 sonrası ters yön olarak yeniden doğrulandı",
            "previous_direction": context.get("direction"),
            "direction": promoted.get("direction"),
        }

    try:
        captured = reversal.analyze_reversal(bot, symbol, df15m, df1h, df4h, current_price)
    except Exception as exc:
        print("Reversal Capture analiz hatası:", type(exc).__name__, exc)
        captured = None
    if isinstance(captured, dict):
        return captured, {
            "eligible": True,
            "status": "TP3 sonrası güçlü ters yön yakalandı",
            "previous_direction": context.get("direction"),
            "direction": captured.get("direction"),
        }
    return None, {
        "eligible": True,
        "status": "TP3 sonrası ters yön probe açıldı fakat canlı Reversal şartları tamamlanmadı",
        "previous_direction": context.get("direction"),
        "direction": context.get("opposite_direction"),
    }


def _portfolio_context(symbol: str, direction: str) -> Dict[str, Any]:
    try:
        result = portfolio_risk.evaluate_portfolio_risk(
            symbol,
            direction,
            "MAIN_MTF",
            record_shadow=False,
        )
        return opportunity_capture.allow_opposite_direction_result(result)
    except Exception as exc:
        return {
            "hard_block": True,
            "block_code": "PORTFOLIO_CHECK_ERROR",
            "block_reason": f"Portfolio risk kontrolü çalışmadı: {type(exc).__name__}",
            "warnings": [],
        }


def _contextual_leverage(core_leverage: Any, *, portfolio: Dict[str, Any], derivatives: Dict[str, Any], orderflow: Dict[str, Any], direction: str) -> str:
    """Çekirdek kaldıraç önerisini yükseltmez; ek risk varsa yalnız tavanı düşürür."""
    core = str(core_leverage or "1x").lower().replace(" ", "")
    cap = 3 if "3x" in core else 2 if "2x" in core else 1
    if portfolio.get("hard_block") or portfolio.get("has_soft_warning"):
        cap = 1
    funding = safe_float(derivatives.get("funding"))
    threshold = safe_float(derivatives.get("funding_threshold"), 0.0005) or 0.0005
    wanted = str(direction or "").upper()
    if funding is not None:
        crowded = (wanted == "LONG" and funding >= threshold) or (wanted == "SHORT" and funding <= -threshold)
        if crowded:
            cap = 1
    if orderflow.get("queried") and orderflow.get("available") and not orderflow.get("confirmed"):
        cap = 1
    return f"{cap}x"


def _decision(
    signal: Optional[Dict[str, Any]],
    *,
    recent_stop_blocked: bool,
    recent_closed_blocked: bool,
    direction_allowed: bool,
    entry_ok: bool,
    entry_reason: str,
    portfolio: Dict[str, Any],
    duplicate: bool,
    open_capacity_blocked: bool,
) -> Tuple[str, str]:
    if recent_stop_blocked:
        return "BEKLE", "Yakın zamanda stop olduğu için canlı Premium cooldown koruması aktif."
    if recent_closed_blocked:
        return "BEKLE", "Yakın zamanda kapanan işlem cooldown koruması aktif; Reversal istisnası oluşmadı."
    if not isinstance(signal, dict):
        return "BEKLE", "Canlı Premium karar yollarının hiçbiri işlem adayı üretmedi."

    direction = str(signal.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return "BEKLE", "Premium adayı geçerli LONG/SHORT yönü üretmedi."
    if str(signal.get("signal_class") or "TRADE").upper() != "TRADE":
        return "BEKLE", "Premium adayı işlem sınıfında değil; radar/bekleme durumunda."
    if duplicate:
        return "BEKLE", "Aynı coin + aynı yön için canlı duplicate koruması aktif."
    if open_capacity_blocked:
        return "BEKLE", "Riskli açık Premium sinyal limiti dolu."
    if not direction_allowed:
        return "BEKLE", "Canlı Premium market guard bu yönü şu anda onaylamıyor."
    if not entry_ok:
        return "BEKLE", entry_reason or "Premium giriş / maliyet / teyit kapısı geçilmedi."
    if bool(portfolio.get("hard_block")):
        return "BEKLE", str(portfolio.get("block_reason") or portfolio.get("block_code") or "Portfolio Risk adayı engelledi.")
    return direction, entry_reason or "Canlı Premium kapıları geçti."


def _format_price(value: Any) -> str:
    number = safe_float(value)
    return "-" if number is None else strategy.format_price(number)


def _plan_text(signal: Dict[str, Any], contextual_leverage: str) -> str:
    return (
        "\n\n✅ PREMIUM ONAYLI İŞLEM PLANI\n"
        f"Yön: {signal.get('direction', '-')}\n"
        f"Giriş: {_format_price(signal.get('entry'))}\n"
        f"TP1: {_format_price(signal.get('tp1'))}\n"
        f"TP2: {_format_price(signal.get('tp2'))}\n"
        f"TP3: {_format_price(signal.get('tp3'))}\n"
        f"SL: {_format_price(signal.get('sl'))}\n"
        f"Stop Mesafesi: %{float(safe_float(signal.get('risk_percent'), 0.0) or 0.0):.2f}\n"
        f"R/R: {signal.get('rr_tp1', '-')} / {signal.get('rr_tp2', '-')} / {signal.get('rr_tp3', '-')}\n"
        f"Çekirdek Kaldıraç: {signal.get('leverage', '-')}\n"
        f"Bağlamsal Kaldıraç Tavanı: {contextual_leverage}"
    )


def _short_status(value: Any, default: str = "-") -> str:
    text = str(value or "").strip()
    return text if text else default


def _funding_text(derivatives: Dict[str, Any]) -> str:
    funding = safe_float(derivatives.get("funding"))
    value = "-" if funding is None else f"{funding:+.6f}"
    return f"{derivatives.get('funding_state', 'veri yok')} | funding {value}"


def _oi_text(derivatives: Dict[str, Any]) -> str:
    oi = safe_float(derivatives.get("open_interest"))
    oi_value = "-" if oi is None else f"{oi:,.0f}"
    return f"{derivatives.get('oi_state', 'veri yok')} | OI {oi_value}"


def _orderflow_text(orderflow: Dict[str, Any]) -> str:
    if not orderflow.get("queried"):
        return str(orderflow.get("reason") or "Sorgulanmadı")
    if not orderflow.get("available"):
        return str(orderflow.get("reason") or "Veri alınamadı")
    return (
        f"{orderflow.get('pressure')} | LONG {orderflow.get('long_score')}/100 "
        f"| SHORT {orderflow.get('short_score')}/100 "
        f"| seçili yön teyidi: {'EVET' if orderflow.get('confirmed') else 'HAYIR'} "
        f"| spread {float(safe_float(orderflow.get('spread_bps'), 0.0) or 0.0):.2f} bps"
    )


def _market_outlook_line(outlook: Dict[str, Any]) -> str:
    fit_long = outlook.get("long_suitability")
    fit_short = outlook.get("short_suitability")
    fit = ""
    if fit_long is not None or fit_short is not None:
        fit = f" | LONG uygunluk {fit_long if fit_long is not None else '-'} / SHORT {fit_short if fit_short is not None else '-'}"
    return (
        f"6s: {outlook.get('bias_6h')} (%{outlook.get('confidence_6h')}) "
        f"| 24s: {outlook.get('bias_24h')} (%{outlook.get('confidence_24h')})" + fit
    )


def analyze_coin(symbol: str) -> str:
    normalized = normalize_symbol(symbol)
    if not normalized or len(normalized) <= 4:
        raise RuntimeError("Geçerli bir USDT coin sembolü girilmedi.")

    strategy.ENABLE_5M_EARLY_TRADE = PREMIUM_ENABLE_5M_EARLY_TRADE
    strategy.MAX_LATE_ENTRY_DISTANCE_PERCENT = PREMIUM_MAX_LATE_ENTRY_DISTANCE_PERCENT

    exchange = bot.get_exchange()
    market_symbol = bot.to_okx_symbol(normalized)
    markets = exchange.load_markets()
    market = markets.get(market_symbol)
    if not isinstance(market, dict) or not market.get("active", True) or not market.get("swap", False):
        raise RuntimeError(f"{normalized} için aktif OKX USDT perpetual futures kontratı bulunamadı.")

    adaptive_fetch = allcoins.make_adaptive_fetcher(bot.fetch_df)
    df5m = adaptive_fetch(exchange, normalized, getattr(bot, "RADAR_TIMEFRAME", "5m"), int(getattr(bot, "RADAR_LIMIT", 300)), min_len=120)
    df15m = adaptive_fetch(exchange, normalized, getattr(bot, "ENTRY_TIMEFRAME", "15m"), int(getattr(bot, "ENTRY_LIMIT", 300)), min_len=120)
    df1h = adaptive_fetch(exchange, normalized, getattr(bot, "CONFIRM_TIMEFRAME", "1h"), int(getattr(bot, "CONFIRM_LIMIT", 300)), min_len=120)
    df4h = adaptive_fetch(exchange, normalized, getattr(bot, "TREND_TIMEFRAME", "4h"), int(getattr(bot, "TREND_LIMIT", 300)), min_len=120)

    if df15m is None:
        raise RuntimeError(f"{normalized} için 15M futures verisi yetersiz.")
    if df1h is None and len(df15m) >= allcoins.MATURE_MIN_CANDLES:
        raise RuntimeError(f"{normalized} için 1H futures verisi yetersiz.")
    if df4h is None and len(df15m) >= allcoins.MATURE_MIN_CANDLES:
        raise RuntimeError(f"{normalized} için 4H futures verisi yetersiz.")

    current_price = bot.get_current_price(exchange, normalized)
    if not current_price or current_price <= 0:
        raise RuntimeError(f"{normalized} güncel futures fiyatı alınamadı.")

    one_day = _one_day_context(exchange, normalized)
    _, trend4_reason, trend4_info = strategy.get_4h_trend(df4h)
    _, confirm1_reason, confirm1_info = strategy.get_1h_confirm(df1h)
    context15 = _frame_context(df15m, "15M")
    context5 = _frame_context(df5m, "5M")
    movement = _movement_v2_context(normalized, df5m, df15m, df1h, df4h, current_price)
    orderflow = _orderflow_context(normalized, movement)

    prefilters = _live_prefilters(normalized)

    with tempfile.TemporaryDirectory(prefix="coin_analyzer_v2_") as temp_dir:
        outlook_snapshot = _fresh_market_outlook(exchange, temp_dir)
        _, pending_gate, entry_gate = _build_preview_gates(temp_dir)

        if prefilters.get("recent_stop_blocked") or prefilters.get("recent_closed_blocked"):
            signal = None
            reversal_context = {"status": prefilters.get("reason")}
        else:
            signal, reversal_context = _premium_candidate(
                exchange, normalized, df15m, df1h, df4h, current_price, pending_gate
            )

        signal_direction = str((signal or {}).get("direction") or "").upper()
        outlook = _outlook_text(outlook_snapshot, signal_direction or None)
        derivatives = _target_derivatives(exchange, normalized)

        try:
            market_guard = bot.get_market_direction_status(exchange)
        except Exception as exc:
            market_guard = {"LONG": False, "SHORT": False, "reason": f"Market guard çalışmadı: {type(exc).__name__}"}

        direction_allowed = bool(market_guard.get(signal_direction, False)) if signal_direction in {"LONG", "SHORT"} else False
        if signal_direction == "LONG" and not bool(getattr(bot, "ALLOW_LONG", True)):
            direction_allowed = False
            market_guard["reason"] = "Config LONG işlemleri kapalı."
        if signal_direction == "SHORT" and not bool(getattr(bot, "ALLOW_SHORT", True)):
            direction_allowed = False
            market_guard["reason"] = "Config SHORT işlemleri kapalı."

        entry_ok = False
        entry_reason = "Premium adayı yok."
        base_entry_ok = False
        base_entry_reason = "Premium adayı yok."
        cost_result: Dict[str, Any] = {"ok": False, "reason": "SIGNAL_MISSING"}
        if isinstance(signal, dict):
            try:
                base_entry_ok, base_entry_reason = bot.is_entry_still_valid(signal, current_price)
            except Exception as exc:
                base_entry_reason = f"Giriş doğrulama hatası: {type(exc).__name__}"
            try:
                cost_result = profit.cost_viability(signal)
            except Exception:
                cost_result = {"ok": False, "reason": "COST_CHECK_ERROR"}

            signal_for_gate = dict(signal)
            if (
                str(signal_for_gate.get("signal_class") or "TRADE").upper() == "TRADE"
                and signal_direction in {"LONG", "SHORT"}
                and not direction_allowed
            ):
                signal_for_gate["signal_class"] = "RADAR"
            try:
                entry_ok, entry_reason, _ = entry_gate(signal_for_gate, current_price)
                if entry_ok:
                    signal = signal_for_gate
                    signal_direction = str(signal.get("direction") or "").upper()
            except Exception as exc:
                entry_ok = False
                entry_reason = f"Premium gate hatası: {type(exc).__name__}"

        portfolio = (
            _portfolio_context(normalized, signal_direction)
            if signal_direction in {"LONG", "SHORT"}
            else {"hard_block": False, "warnings": [], "open_signal_count": len(portfolio_risk.collect_open_portfolio())}
        )

        duplicate = False
        if isinstance(signal, dict):
            try:
                duplicate = bool(bot.is_duplicate(signal, radar=False))
            except Exception:
                duplicate = True

        risky_open = reduced_open = total_open = 0
        open_capacity_blocked = False
        try:
            risky_open, reduced_open, total_open = bot.count_open_signal_risk()
            open_capacity_blocked = risky_open >= int(getattr(bot, "MAX_OPEN_SIGNALS", 6))
        except Exception:
            open_capacity_blocked = True

        final_decision, final_reason = _decision(
            signal,
            recent_stop_blocked=bool(prefilters.get("recent_stop_blocked")),
            recent_closed_blocked=bool(prefilters.get("recent_closed_blocked")),
            direction_allowed=direction_allowed,
            entry_ok=entry_ok,
            entry_reason=entry_reason,
            portfolio=portfolio,
            duplicate=duplicate,
            open_capacity_blocked=open_capacity_blocked,
        )

        contextual_leverage = _contextual_leverage(
            (signal or {}).get("leverage"),
            portfolio=portfolio,
            derivatives=derivatives,
            orderflow=orderflow,
            direction=signal_direction,
        )

        movement_text = "V2 5M adayı yok"
        if isinstance(movement, dict):
            movement_text = f"{movement.get('direction', '-')} {movement.get('stage', '-')} | skor {movement.get('score', '-')}/100"

        continuation_text = (
            "UYGUN — canlı Trend Continuation adayı"
            if isinstance(signal, dict) and str(signal.get("source") or "").upper() == continuation.SOURCE
            else "Aktif canlı aday yok"
        )
        score_text = f"{int(safe_float((signal or {}).get('score'), 0) or 0)}/100" if isinstance(signal, dict) else "Aday oluşmadı"
        source_text = _route_name(signal)

        portfolio_status = "UYGUN"
        if portfolio.get("hard_block"):
            portfolio_status = f"ENGEL — {portfolio.get('block_reason') or portfolio.get('block_code')}"
        elif portfolio.get("warnings"):
            portfolio_status = "UYARI — " + " | ".join(str(x) for x in portfolio.get("warnings")[:2])

        cost_text = f"{'UYGUN' if cost_result.get('ok') else 'UYGUN DEĞİL'} | neden {cost_result.get('reason')}"
        if cost_result.get("estimated_cost_r") is not None:
            cost_text += f" | tahmini maliyet {cost_result.get('estimated_cost_r')}R | TP1/BE net {cost_result.get('tp1_be_net_r')}R"

        risk_flags = outlook.get("risk_flags") or []
        risk_flags_text = "Yok" if not risk_flags else "; ".join(str(x) for x in risk_flags[:3])
        report = f"""
🔬 COIN DETAY ANALİZİ V2 — PREMIUM MİKROSKOP

Coin: {normalized}
Market: {market_symbol}
Fiyat: {_format_price(current_price)}
Sürüm: {VERSION}

🧭 ÇOKLU ZAMAN DİLİMİ
• {one_day.get('text')} | skor: {one_day.get('score') if one_day.get('score') is not None else '-'}
• 4H: {trend4_reason} | ADX: {trend4_info.get('adx_4h', '-') if isinstance(trend4_info, dict) else '-'} | RSI: {trend4_info.get('rsi_4h', '-') if isinstance(trend4_info, dict) else '-'}
• 1H: {confirm1_reason} | ADX: {confirm1_info.get('adx_1h', '-') if isinstance(confirm1_info, dict) else '-'} | RSI: {confirm1_info.get('rsi_1h', '-') if isinstance(confirm1_info, dict) else '-'}
• {context15.get('text')} | RSI: {context15.get('rsi', '-')} | ADX: {context15.get('adx', '-')} | Hacim: {context15.get('volume_ratio', '-')}x
• {context5.get('text')} | Movement Start V2: {movement_text}

🌍 GENEL PİYASA / MARKET OUTLOOK
• {_market_outlook_line(outlook)}
• Risk bayrakları: {risk_flags_text}
• Canlı legacy market guard: LONG={'AÇIK' if market_guard.get('LONG') else 'KAPALI'} | SHORT={'AÇIK' if market_guard.get('SHORT') else 'KAPALI'}
• Market guard nedeni: {_short_status(market_guard.get('reason'))}
Not: Market Outlook V1 şu an teşhis katmanıdır; kendi başına Premium hard-gate değildir.

📈 FUNDING / OPEN INTEREST — {normalized}
• Funding: {_funding_text(derivatives)}
• Open Interest: {_oi_text(derivatives)}
Not: Coin özel Funding/OI şu an teşhistir; canlı Premium hard-gate kuralları değiştirilmez.

🧬 ORDER-FLOW V3
• {_orderflow_text(orderflow)}
Not: V3 şu an gölge/öğrenme katmanıdır; tek başına canlı Premium kararını değiştirmez.

🔄 REVERSAL CAPTURE
• {_short_status(reversal_context.get('status'), 'Yok')}

🚀 TREND CONTINUATION
• {continuation_text}

💎 PREMIUM KARAR
• Kaynak: {source_text}
• Premium skor: {score_text}
• Yakın stop cooldown: {'AKTİF' if prefilters.get('recent_stop_blocked') else 'YOK'}
• Yakın kapanış cooldown: {'AKTİF' if prefilters.get('recent_closed_blocked') else 'YOK / Reversal istisnası uygun olabilir'}
• Base giriş güvenliği: {'UYGUN' if base_entry_ok else 'UYGUN DEĞİL'} — {base_entry_reason}
• Maliyet kontrolü: {cost_text}
• Portfolio Risk: {portfolio_status}
• Açık Premium risk: {risky_open}/{getattr(bot, 'MAX_OPEN_SIGNALS', 6)} riskli | {reduced_open} TP1 azaltılmış | toplam {total_open}
• Duplicate: {'VAR' if duplicate else 'YOK'}
• Çekirdek kaldıraç: {(signal or {}).get('leverage', '-')}
• Bağlamsal kaldıraç tavanı: {contextual_leverage}

📌 KARAR: {final_decision}
Neden: {final_reason}
""".strip()

        if final_decision in {"LONG", "SHORT"} and isinstance(signal, dict):
            report += _plan_text(signal, contextual_leverage)
        else:
            report += (
                "\n\n⏳ İŞLEM PLANI ÜRETİLMEDİ\n"
                "Entry–TP–SL yalnız canlı Premium karar kapıları gerçekten geçtiğinde gösterilir."
            )

        report += (
            "\n\n🛡️ NOT\n"
            "Bu ekran ayrı bir sinyal motoru değildir. Klasik Premium MTF, Trend Continuation, "
            "Reversal Capture, Premium teyit/maliyet, cooldown/duplicate ve Portfolio Risk bileşenlerini doğrudan kullanır. "
            "1D, Market Outlook, Funding/OI ve V3 order-flow mevcut canlı sistemde hard-gate olmayan ek teşhis bağlamıdır.\n"
            "Tek coin mikroskop, tüm piyasa adaylarının aynı turdaki sıralamasını simüle etmez; coin bazlı uygunluğu gösterir.\n"
            "Emir açılmaz; rapor yalnız analiz amaçlıdır."
        )
        return report


def main() -> None:
    symbol = normalize_symbol(SYMBOL)
    print("Coin Detay Analizi V2 çalışıyor:", symbol)
    try:
        report = analyze_coin(symbol)
        print(report)
        send_telegram(report)
    except Exception as exc:
        message = (
            "❌ Coin Detay Analizi V2 hatası\n\n"
            f"Coin: {symbol}\n"
            f"Hata: {type(exc).__name__}: {exc}"
        )
        print(message)
        send_telegram(message)
        raise


if __name__ == "__main__":
    main()
