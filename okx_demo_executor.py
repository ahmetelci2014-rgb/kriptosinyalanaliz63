from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any

import requests

BASE_URL = "https://www.okx.com"
LIVE_MIN_SCORE = 91
LIVE_MAX_ENTRY_DISTANCE_PERCENT = 0.25


def load_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default


def okx_inst_id(symbol: str) -> str:
    normalized = str(symbol or "").upper().replace("/", "").replace("-", "")
    if not normalized.endswith("USDT"):
        raise ValueError(f"Yalnız USDT sözleşmeleri destekleniyor: {symbol}")
    base = normalized[:-4]
    if not base:
        raise ValueError(f"Geçersiz sembol: {symbol}")
    return f"{base}-USDT-SWAP"


def pct_distance(a: float, b: float) -> float:
    if b <= 0:
        return 999.0
    return abs(a - b) / b * 100.0


def floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    units = (value / step).to_integral_value(rounding=ROUND_DOWN)
    return units * step


def fmt_decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")


def compute_contracts(
    margin_usdt: float,
    leverage: int,
    last: float,
    ct_val: str,
    lot_sz: str,
    min_sz: str,
) -> dict[str, Any]:
    target_notional = Decimal(str(margin_usdt)) * Decimal(str(leverage))
    last_d = Decimal(str(last))
    ct = Decimal(str(ct_val))
    lot = Decimal(str(lot_sz))
    minq = Decimal(str(min_sz))

    if min(target_notional, last_d, ct, lot, minq) <= 0:
        raise ValueError("Geçersiz demo pozisyon boyutlandırma girdisi.")

    raw = target_notional / (last_d * ct)
    qty = floor_to_step(raw, lot)
    min_forced = False
    if qty < minq:
        qty = minq
        min_forced = True

    actual_notional = qty * ct * last_d
    return {
        "contracts": fmt_decimal(qty),
        "target_notional_usdt": float(target_notional),
        "estimated_notional_usdt": float(actual_notional),
        "min_forced": min_forced,
    }


def round_price_down(value: float, tick_sz: str) -> str:
    return fmt_decimal(
        floor_to_step(Decimal(str(value)), Decimal(str(tick_sz)))
    )


def select_signal(
    open_signals: dict[str, Any],
    trade_id: str | None,
    now_ts: int,
    min_score: int,
    max_age_minutes: int,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []

    for signal in (open_signals or {}).values():
        if not isinstance(signal, dict):
            continue
        if signal.get("closed") or signal.get("tp1_hit"):
            continue
        if float(signal.get("score") or 0) < min_score:
            continue
        if (
            float(signal.get("entry_distance_at_send_percent") or 999.0)
            > LIVE_MAX_ENTRY_DISTANCE_PERCENT
        ):
            continue

        candidate_trade_id = str(signal.get("trade_id") or "")
        if trade_id and candidate_trade_id != trade_id:
            continue

        opened_at = int(signal.get("opened_at") or 0)
        if opened_at <= 0:
            continue
        if now_ts - opened_at > max_age_minutes * 60:
            continue

        candidates.append(signal)

    if not candidates:
        raise RuntimeError(
            "Uygun yeni Premium demo sinyali bulunamadı. "
            "TP1 görmüş, eski, düşük skorlu veya girişten uzak sinyal açılmaz."
        )

    candidates.sort(
        key=lambda item: int(item.get("opened_at") or 0),
        reverse=True,
    )
    return candidates[0]


class OKXDemoClient:
    def __init__(
        self,
        api_key: str = "",
        secret_key: str = "",
        passphrase: str = "",
        timeout: int = 15,
    ) -> None:
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self.timeout = timeout

    def _timestamp(self) -> str:
        return (
            datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )

    def _sign(
        self,
        timestamp: str,
        method: str,
        request_path: str,
        body: str,
    ) -> str:
        prehash = (
            f"{timestamp}{method.upper()}{request_path}{body}"
        ).encode("utf-8")
        digest = hmac.new(
            self.secret_key.encode("utf-8"),
            prehash,
            hashlib.sha256,
        ).digest()
        return base64.b64encode(digest).decode("utf-8")

    def public_get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = requests.get(
            BASE_URL + path,
            params=params or {},
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        if str(data.get("code", "0")) != "0":
            raise RuntimeError(
                f"OKX public hata: {data.get('code')} {data.get('msg')}"
            )
        return data

    def private(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        query: str = "",
    ) -> dict[str, Any]:
        if not (self.api_key and self.secret_key and self.passphrase):
            raise RuntimeError("OKX demo API bilgileri eksik.")

        method = method.upper()
        body = (
            ""
            if method == "GET"
            else json.dumps(payload or {}, separators=(",", ":"))
        )
        request_path = path + (f"?{query}" if query else "")
        timestamp = self._timestamp()

        headers = {
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": self._sign(
                timestamp,
                method,
                request_path,
                body,
            ),
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
            "x-simulated-trading": "1",
        }

        response = requests.request(
            method,
            BASE_URL + request_path,
            headers=headers,
            data=body if body else None,
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()

        if str(data.get("code", "")) != "0":
            raise RuntimeError(
                f"OKX demo hata: {data.get('code')} {data.get('msg')}"
            )
        return data


def get_instrument(
    client: OKXDemoClient,
    inst_id: str,
) -> dict[str, Any]:
    data = client.public_get(
        "/api/v5/public/instruments",
        {"instType": "SWAP", "instId": inst_id},
    )
    rows = data.get("data") or []
    if not rows:
        raise RuntimeError(f"OKX SWAP bulunamadı: {inst_id}")

    instrument = rows[0]
    if instrument.get("state") != "live":
        raise RuntimeError(
            f"OKX enstrümanı market emrine açık değil: "
            f"{inst_id} state={instrument.get('state')}"
        )
    return instrument


def get_last(
    client: OKXDemoClient,
    inst_id: str,
) -> float:
    data = client.public_get(
        "/api/v5/market/ticker",
        {"instId": inst_id},
    )
    rows = data.get("data") or []
    if not rows:
        raise RuntimeError(f"OKX ticker bulunamadı: {inst_id}")
    return float(rows[0]["last"])


def make_plan(
    signal: dict[str, Any],
    instrument: dict[str, Any],
    last: float,
    margin_usdt: float,
    leverage: int,
    max_drift_percent: float,
) -> dict[str, Any]:
    entry = float(signal["entry"])
    drift = pct_distance(last, entry)
    if drift > max_drift_percent:
        raise RuntimeError(
            f"Fiyat sinyal girişinden %{drift:.3f} uzak; "
            "demo giriş engellendi."
        )

    sizing = compute_contracts(
        margin_usdt=margin_usdt,
        leverage=leverage,
        last=last,
        ct_val=instrument["ctVal"],
        lot_sz=instrument["lotSz"],
        min_sz=instrument["minSz"],
    )

    if (
        sizing["estimated_notional_usdt"]
        > sizing["target_notional_usdt"] * 1.5
    ):
        raise RuntimeError(
            "OKX minimum kontrat boyutu hedef demo notionalini "
            "%50'den fazla aşıyor; işlem açılmadı."
        )

    direction = str(signal["direction"]).upper()
    if direction not in {"LONG", "SHORT"}:
        raise RuntimeError(f"Geçersiz yön: {direction}")

    return {
        "trade_id": signal["trade_id"],
        "instId": okx_inst_id(signal["symbol"]),
        "direction": direction,
        "side": "buy" if direction == "LONG" else "sell",
        "posSide": "long" if direction == "LONG" else "short",
        "tdMode": "isolated",
        "leverage": leverage,
        "signal_entry": entry,
        "market_last": last,
        "entry_drift_percent": round(drift, 4),
        "contracts": sizing["contracts"],
        "estimated_notional_usdt": round(
            sizing["estimated_notional_usdt"],
            4,
        ),
        "sl": round_price_down(
            float(signal["sl"]),
            instrument["tickSz"],
        ),
        "tp3": round_price_down(
            float(signal["tp3"]),
            instrument["tickSz"],
        ),
        "score": signal.get("score"),
        "source": signal.get("source"),
    }


def account_position_mode(client: OKXDemoClient) -> str:
    data = client.private("GET", "/api/v5/account/config")
    rows = data.get("data") or []
    return (
        str(rows[0].get("posMode") or "net_mode")
        if rows
        else "net_mode"
    )


def has_any_open_position(client: OKXDemoClient) -> bool:
    data = client.private("GET", "/api/v5/account/positions")
    for row in data.get("data") or []:
        try:
            if abs(float(row.get("pos") or 0)) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def set_leverage(
    client: OKXDemoClient,
    plan: dict[str, Any],
    position_mode: str,
) -> None:
    payload: dict[str, Any] = {
        "instId": plan["instId"],
        "lever": str(plan["leverage"]),
        "mgnMode": "isolated",
    }
    if position_mode == "long_short_mode":
        payload["posSide"] = plan["posSide"]

    client.private(
        "POST",
        "/api/v5/account/set-leverage",
        payload,
    )


def deterministic_client_ids(trade_id: str) -> tuple[str, str]:
    digest = hashlib.sha256(
        trade_id.encode("utf-8")
    ).hexdigest()
    return (
        ("dm" + digest[:28])[:32],
        ("da" + digest[28:56])[:32],
    )


def place_protected_market_order(
    client: OKXDemoClient,
    plan: dict[str, Any],
    position_mode: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    cl_ord_id, algo_cl_ord_id = deterministic_client_ids(
        plan["trade_id"]
    )

    payload: dict[str, Any] = {
        "instId": plan["instId"],
        "tdMode": "isolated",
        "side": plan["side"],
        "ordType": "market",
        "sz": plan["contracts"],
        "clOrdId": cl_ord_id,
        "attachAlgoOrds": [
            {
                "attachAlgoClOrdId": algo_cl_ord_id,
                "tpTriggerPx": plan["tp3"],
                "tpOrdPx": "-1",
                "tpTriggerPxType": "mark",
                "slTriggerPx": plan["sl"],
                "slOrdPx": "-1",
                "slTriggerPxType": "mark",
            }
        ],
    }
    if position_mode == "long_short_mode":
        payload["posSide"] = plan["posSide"]

    data = client.private(
        "POST",
        "/api/v5/trade/order",
        payload,
    )
    rows = data.get("data") or []
    if not rows or str(rows[0].get("sCode", "0")) != "0":
        raise RuntimeError(
            f"OKX demo emri reddedildi: "
            f"{rows[0] if rows else data}"
        )
    return rows[0], payload


def order_detail(
    client: OKXDemoClient,
    inst_id: str,
    ord_id: str,
) -> dict[str, Any]:
    return client.private(
        "GET",
        "/api/v5/trade/order",
        query=f"instId={inst_id}&ordId={ord_id}",
    )


def emergency_close(
    client: OKXDemoClient,
    plan: dict[str, Any],
    position_mode: str,
) -> None:
    payload: dict[str, Any] = {
        "instId": plan["instId"],
        "mgnMode": "isolated",
        "autoCxl": "true",
    }
    if position_mode == "long_short_mode":
        payload["posSide"] = plan["posSide"]

    client.private(
        "POST",
        "/api/v5/trade/close-position",
        payload,
    )


def execute_demo(
    client: OKXDemoClient,
    plan: dict[str, Any],
) -> dict[str, Any]:
    if has_any_open_position(client):
        raise RuntimeError(
            "Demo hesabında zaten açık pozisyon var. "
            "İlk pilotta ikinci pozisyon açılmaz."
        )

    position_mode = account_position_mode(client)
    set_leverage(client, plan, position_mode)

    order, request_payload = place_protected_market_order(
        client,
        plan,
        position_mode,
    )
    ord_id = str(order.get("ordId") or "")
    if not ord_id:
        raise RuntimeError("OKX demo order ID dönmedi.")

    last_detail: dict[str, Any] = {}
    filled = False
    protection_verified = False

    for _ in range(10):
        time.sleep(1)
        last_detail = order_detail(
            client,
            plan["instId"],
            ord_id,
        )
        row = (last_detail.get("data") or [{}])[0]
        if row.get("state") == "filled":
            filled = True
            if row.get("attachAlgoOrds"):
                protection_verified = True
                break

    if filled and not protection_verified:
        emergency_close(
            client,
            plan,
            position_mode,
        )
        raise RuntimeError(
            "Demo emir doldu fakat attached TP/SL doğrulanamadı; "
            "pozisyon güvenlik için kapatıldı."
        )

    if not filled:
        try:
            emergency_close(
                client,
                plan,
                position_mode,
            )
        except Exception:
            pass
        raise RuntimeError(
            "Demo market emrinin dolumu 10 saniyede doğrulanamadı."
        )

    return {
        "status": "SUCCESS",
        "position_mode": position_mode,
        "order": order,
        "order_detail": last_detail,
        "request": request_payload,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Premium sinyalini OKX demo hesabında güvenli pilot emir planına "
            "çevirir. Varsayılan mod yalnız plan üretir."
        )
    )
    parser.add_argument(
        "--mode",
        choices=["plan", "execute"],
        default="plan",
    )
    parser.add_argument(
        "--trade-id",
        default="",
        help="Boşsa en yeni uygun Premium sinyal seçilir.",
    )
    parser.add_argument(
        "--signals",
        default="open_signals.json",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    now_ts = int(time.time())

    min_score = int(
        os.getenv("OKX_DEMO_MIN_SCORE", str(LIVE_MIN_SCORE))
    )
    max_age_minutes = int(
        os.getenv("OKX_DEMO_MAX_SIGNAL_AGE_MINUTES", "30")
    )
    max_drift_percent = float(
        os.getenv(
            "OKX_DEMO_MAX_ENTRY_DRIFT_PERCENT",
            str(LIVE_MAX_ENTRY_DISTANCE_PERCENT),
        )
    )
    margin_usdt = float(
        os.getenv("OKX_DEMO_MARGIN_USDT", "5")
    )
    leverage = int(
        os.getenv("OKX_DEMO_LEVERAGE", "2")
    )

    signals = load_json(args.signals, {})
    signal = select_signal(
        open_signals=signals,
        trade_id=args.trade_id or None,
        now_ts=now_ts,
        min_score=min_score,
        max_age_minutes=max_age_minutes,
    )

    client = OKXDemoClient(
        api_key=os.getenv("OKX_DEMO_API_KEY", ""),
        secret_key=os.getenv("OKX_DEMO_SECRET_KEY", ""),
        passphrase=os.getenv("OKX_DEMO_PASSPHRASE", ""),
    )

    inst_id = okx_inst_id(signal["symbol"])
    instrument = get_instrument(client, inst_id)
    market_last = get_last(client, inst_id)

    plan = make_plan(
        signal=signal,
        instrument=instrument,
        last=market_last,
        margin_usdt=margin_usdt,
        leverage=leverage,
        max_drift_percent=max_drift_percent,
    )

    print(
        json.dumps(
            {"mode": args.mode, "plan": plan},
            indent=2,
            ensure_ascii=False,
        )
    )

    if args.mode == "execute":
        enabled = (
            os.getenv("OKX_DEMO_ENABLED", "")
            .strip()
            .lower()
        )
        if enabled not in {"1", "true", "yes", "on"}:
            raise RuntimeError(
                "OKX_DEMO_ENABLED=true olmadan demo emir gönderilmez."
            )

        result = execute_demo(client, plan)
        print(
            json.dumps(
                {"demo_execution": result},
                indent=2,
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
