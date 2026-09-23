import base64
import hashlib
import hmac
import json
import os
import sys
import time
from decimal import Decimal, ROUND_DOWN, ROUND_UP

import requests

BASE_URL = "https://api.bitget.com"
CATEGORY = "USDT-FUTURES"


def env(name):
    value = os.getenv(name, "").strip()
    if not value:
        print(f"[FAIL] Missing environment value: {name}")
        sys.exit(2)
    return value


def sign(secret, timestamp, method, path, query="", body=""):
    prehash = f"{timestamp}{method.upper()}{path}"
    if query:
        prehash += f"?{query}"
    prehash += body
    digest = hmac.new(
        secret.encode("utf-8"),
        prehash.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


class DemoClient:
    def __init__(self):
        self.api_key = env("BITGET_DEMO_API_KEY")
        self.secret = env("BITGET_DEMO_SECRET_KEY")
        self.passphrase = env("BITGET_DEMO_PASSPHRASE")
        self.session = requests.Session()

    def _headers(self, method, path, query="", body=""):
        ts = str(int(time.time() * 1000))
        return {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": sign(self.secret, ts, method, path, query, body),
            "ACCESS-TIMESTAMP": ts,
            "ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
            "locale": "en-US",
            "paptrading": "1",
            "User-Agent": "bb-scanner-demo-order-test/1.0",
        }

    def public_get(self, path, params):
        r = self.session.get(BASE_URL + path, params=params, timeout=15)
        payload = r.json()
        if not r.ok or str(payload.get("code")) != "00000":
            raise RuntimeError(f"Public GET failed: HTTP={r.status_code} code={payload.get('code')} msg={payload.get('msg')}")
        return payload.get("data")

    def private_post(self, path, payload):
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        headers = self._headers("POST", path, body=body)
        r = self.session.post(BASE_URL + path, data=body.encode("utf-8"), headers=headers, timeout=15)
        try:
            data = r.json()
        except ValueError:
            raise RuntimeError(f"Non-JSON response from {path}: HTTP {r.status_code}")
        if not r.ok or str(data.get("code")) != "00000":
            raise RuntimeError(f"{path} failed: HTTP={r.status_code} code={data.get('code')} msg={data.get('msg')}")
        return data.get("data") or {}


def quantize_down(value, step):
    value = Decimal(str(value))
    step = Decimal(str(step))
    if step <= 0:
        return value
    units = (value / step).to_integral_value(rounding=ROUND_DOWN)
    return units * step


def quantize_up(value, step):
    value = Decimal(str(value))
    step = Decimal(str(step))
    if step <= 0:
        return value
    units = (value / step).to_integral_value(rounding=ROUND_UP)
    return units * step


def fmt_decimal(value, places):
    places = int(places)
    quantum = Decimal("1") if places == 0 else Decimal("1").scaleb(-places)
    return format(Decimal(value).quantize(quantum), "f")


def main():
    if env("DEMO_CONFIRM") != "DEMO_PLACE_CANCEL":
        print("[FAIL] Confirmation phrase mismatch. No order sent.")
        sys.exit(3)

    symbol = os.getenv("DEMO_SYMBOL", "BTCUSDT").strip().upper()
    side = os.getenv("DEMO_SIDE", "buy").strip().lower()
    position_mode = "hedge"  # Demo account confirmed by user to be Hedge Mode
    target_notional = Decimal(os.getenv("DEMO_NOTIONAL_USDT", "10").strip())

    if side not in {"buy", "sell"}:
        raise RuntimeError("DEMO_SIDE must be buy or sell")
    if position_mode not in {"one_way", "hedge"}:
        raise RuntimeError("DEMO_POSITION_MODE must be one_way or hedge")

    client = DemoClient()

    instruments = client.public_get(
        "/api/v3/market/instruments",
        {"category": CATEGORY, "symbol": symbol},
    ) or []
    if not instruments:
        raise RuntimeError(f"No instrument metadata returned for {symbol}")

    inst = instruments[0]
    if str(inst.get("status", "")).lower() != "online":
        raise RuntimeError(f"{symbol} is not online: status={inst.get('status')}")

    tickers = client.public_get(
        "/api/v3/market/tickers",
        {"category": CATEGORY, "symbol": symbol},
    ) or []
    if not tickers:
        raise RuntimeError(f"No ticker returned for {symbol}")

    last = Decimal(str(tickers[0]["lastPrice"]))
    min_qty = Decimal(str(inst.get("minOrderQty") or "0"))
    min_amount = Decimal(str(inst.get("minOrderAmount") or "0"))
    qty_step = Decimal(str(inst.get("quantityMultiplier") or "0"))
    price_step = Decimal(str(inst.get("priceMultiplier") or "0"))
    qty_precision = int(inst.get("quantityPrecision") or 8)
    price_precision = int(inst.get("pricePrecision") or 8)

    # Keep the test tiny but above the exchange minimum.
    required_notional = max(target_notional, min_amount)
    raw_qty = required_notional / last
    qty = max(min_qty, raw_qty)
    if qty_step > 0:
        qty = quantize_up(qty, qty_step)

    # Put the limit 1% away from market so it should rest long enough to cancel.
    raw_price = last * (Decimal("0.99") if side == "buy" else Decimal("1.01"))
    if price_step > 0:
        price = quantize_down(raw_price, price_step) if side == "buy" else quantize_up(raw_price, price_step)
    else:
        price = raw_price

    qty_s = fmt_decimal(qty, qty_precision)
    price_s = fmt_decimal(price, price_precision)
    client_oid = f"demo_pc_{int(time.time())}"[:32]

    order = {
        "category": CATEGORY,
        "symbol": symbol,
        "qty": qty_s,
        "price": price_s,
        "side": side,
        "orderType": "limit",
        "timeInForce": "gtc",
        "clientOid": client_oid,
        "reduceOnly": "no",
        "pxAmendType": "no",
    }
    if position_mode == "hedge":
        order["posSide"] = "long" if side == "buy" else "short"

    print("[INFO] DEMO ONLY. paptrading=1 is forced in every private request.")
    print(f"[INFO] Test order: {symbol} {side.upper()} qty={qty_s} limit={price_s} last={last}")
    print("[INFO] The script will immediately cancel the order after Bitget accepts it.")

    placed = client.private_post("/api/v3/trade/place-order", order)
    order_id = str(placed.get("orderId") or "")
    if not order_id:
        raise RuntimeError(f"Order accepted but no orderId returned: {placed}")

    print(f"[OK] Demo limit order accepted. orderId={order_id}")

    cancel = {
        "category": CATEGORY,
        "orderId": order_id,
    }

    # Give the exchange a brief moment to register the order.
    time.sleep(0.4)
    cancelled = client.private_post("/api/v3/trade/cancel-order", cancel)
    print(f"[OK] Demo order cancelled. orderId={cancelled.get('orderId', order_id)}")
    print("[OK] Place + cancel pipeline succeeded. No live-account credentials are used by this workflow.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[FAIL] {exc}")
        sys.exit(1)
