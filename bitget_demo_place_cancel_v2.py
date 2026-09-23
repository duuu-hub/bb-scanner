import base64
import hashlib
import hmac
import json
import os
import sys
import time
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from urllib.parse import urlencode

import requests

BASE_URL = "https://api.bitget.com"
PRODUCT_TYPE = "USDT-FUTURES"
MARGIN_COIN = "USDT"


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


class DemoClassicClient:
    def __init__(self):
        self.api_key = env("BITGET_DEMO_API_KEY")
        self.secret = env("BITGET_DEMO_SECRET_KEY")
        self.passphrase = env("BITGET_DEMO_PASSPHRASE")
        self.session = requests.Session()

    def headers(self, method, path, query="", body=""):
        ts = str(int(time.time() * 1000))
        return {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": sign(self.secret, ts, method, path, query, body),
            "ACCESS-TIMESTAMP": ts,
            "ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
            "locale": "en-US",
            "paptrading": "1",
            "User-Agent": "bb-scanner-demo-classic-test/1.0",
        }

    def public_get(self, path, params):
        r = self.session.get(BASE_URL + path, params=params, timeout=15)
        p = r.json()
        if not r.ok or str(p.get("code")) != "00000":
            raise RuntimeError(f"Public GET failed: HTTP={r.status_code} code={p.get('code')} msg={p.get('msg')}")
        return p.get("data")

    def private_post(self, path, payload):
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        r = self.session.post(
            BASE_URL + path,
            data=body.encode("utf-8"),
            headers=self.headers("POST", path, body=body),
            timeout=15,
        )
        try:
            p = r.json()
        except ValueError:
            raise RuntimeError(f"Non-JSON response from {path}: HTTP {r.status_code}")
        if not r.ok or str(p.get("code")) != "00000":
            raise RuntimeError(f"{path} failed: HTTP={r.status_code} code={p.get('code')} msg={p.get('msg')}")
        return p.get("data") or {}


def q_up(value, step):
    value = Decimal(str(value))
    step = Decimal(str(step))
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_UP) * step


def q_down(value, step):
    value = Decimal(str(value))
    step = Decimal(str(step))
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def main():
    if env("DEMO_CONFIRM") != "DEMO_CLASSIC_PLACE_CANCEL":
        print("[FAIL] Confirmation phrase mismatch. No order sent.")
        sys.exit(3)

    symbol = os.getenv("DEMO_SYMBOL", "BTCUSDT").strip().upper()
    target_notional = Decimal(os.getenv("DEMO_NOTIONAL_USDT", "10").strip())
    client = DemoClassicClient()

    contracts = client.public_get(
        "/api/v2/mix/market/contracts",
        {"productType": "usdt-futures", "symbol": symbol},
    ) or []
    if not contracts:
        raise RuntimeError(f"No classic contract config returned for {symbol}")
    cfg = contracts[0]

    ticker = client.public_get(
        "/api/v2/mix/market/ticker",
        {"productType": "usdt-futures", "symbol": symbol},
    ) or []
    if not ticker:
        raise RuntimeError(f"No classic ticker returned for {symbol}")
    last = Decimal(str(ticker[0]["lastPr"]))

    min_qty = Decimal(str(cfg.get("minTradeNum") or "0"))
    size_step = Decimal(str(cfg.get("sizeMultiplier") or "0"))
    min_usdt = Decimal(str(cfg.get("minTradeUSDT") or "0"))
    volume_place = int(cfg.get("volumePlace") or 8)
    price_place = int(cfg.get("pricePlace") or 8)
    price_end_step = Decimal(str(cfg.get("priceEndStep") or "1"))
    price_step = Decimal(1).scaleb(-price_place) * price_end_step

    notional = max(target_notional, min_usdt)
    qty = max(min_qty, notional / last)
    if size_step > 0:
        qty = q_up(qty, size_step)

    # 1% below market: designed to rest, then be cancelled quickly.
    price = q_down(last * Decimal("0.99"), price_step)
    qty_s = f"{qty:.{volume_place}f}"
    price_s = f"{price:.{price_place}f}"
    client_oid = f"demo_v2_{int(time.time())}"[:32]

    order = {
        "symbol": symbol,
        "productType": PRODUCT_TYPE,
        "marginMode": "crossed",
        "marginCoin": MARGIN_COIN,
        "size": qty_s,
        "price": price_s,
        "side": "buy",
        "tradeSide": "open",
        "orderType": "limit",
        "force": "gtc",
        "clientOid": client_oid,
    }

    print("[INFO] DEMO ONLY: Classic v2 order test with paptrading=1.")
    print(f"[INFO] Hedge open-long test: {symbol} qty={qty_s} limit={price_s} last={last}")
    print("[INFO] The order will be cancelled immediately after Bitget accepts it.")

    placed = client.private_post("/api/v2/mix/order/place-order", order)
    order_id = str(placed.get("orderId") or "")
    if not order_id:
        raise RuntimeError(f"Order accepted but no orderId returned: {placed}")
    print(f"[OK] Demo Classic v2 limit order accepted. orderId={order_id}")

    time.sleep(0.4)
    cancelled = client.private_post(
        "/api/v2/mix/order/cancel-order",
        {
            "symbol": symbol,
            "productType": PRODUCT_TYPE,
            "marginCoin": MARGIN_COIN,
            "orderId": order_id,
        },
    )
    print(f"[OK] Demo Classic v2 order cancelled. orderId={cancelled.get('orderId', order_id)}")
    print("[OK] Classic Demo place + cancel pipeline succeeded.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[FAIL] {exc}")
        sys.exit(1)
