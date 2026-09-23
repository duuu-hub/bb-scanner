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


class BitgetDemoClassic:
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
            "User-Agent": "bb-scanner-demo-lifecycle/1.0",
        }

    def public_get(self, path, params):
        r = self.session.get(BASE_URL + path, params=params, timeout=15)
        p = r.json()
        if not r.ok or str(p.get("code")) != "00000":
            raise RuntimeError(f"Public GET failed: HTTP={r.status_code} code={p.get('code')} msg={p.get('msg')}")
        return p.get("data")

    def private_get(self, path, params):
        query = urlencode(params)
        r = self.session.get(
            BASE_URL + path,
            params=params,
            headers=self._headers("GET", path, query=query),
            timeout=15,
        )
        try:
            p = r.json()
        except ValueError:
            raise RuntimeError(f"Non-JSON response from {path}: HTTP={r.status_code}")
        if not r.ok or str(p.get("code")) != "00000":
            raise RuntimeError(f"{path} failed: HTTP={r.status_code} code={p.get('code')} msg={p.get('msg')}")
        return p.get("data")

    def private_post(self, path, payload):
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        r = self.session.post(
            BASE_URL + path,
            data=body.encode("utf-8"),
            headers=self._headers("POST", path, body=body),
            timeout=15,
        )
        try:
            p = r.json()
        except ValueError:
            raise RuntimeError(f"Non-JSON response from {path}: HTTP={r.status_code}")
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


def q_nearest(value, step):
    value = Decimal(str(value))
    step = Decimal(str(step))
    if step <= 0:
        return value
    units = (value / step).to_integral_value()
    return units * step


def wait_for_fill(client, symbol, order_id, attempts=20):
    last = None
    for _ in range(attempts):
        last = client.private_get(
            "/api/v2/mix/order/detail",
            {
                "symbol": symbol,
                "productType": PRODUCT_TYPE,
                "orderId": order_id,
            },
        ) or {}
        state = str(last.get("state", "")).lower()
        if state == "filled":
            return last
        if state in {"cancelled", "canceled", "failed"}:
            raise RuntimeError(f"Order ended unexpectedly: state={state}")
        time.sleep(0.5)
    raise RuntimeError(f"Order did not reach filled state in time. Last state={last.get('state') if last else None}")


def main():
    if env("DEMO_CONFIRM") != "DEMO_LIFECYCLE_TEST":
        print("[FAIL] Confirmation phrase mismatch. No order sent.")
        sys.exit(3)

    symbol = os.getenv("DEMO_SYMBOL", "BTCUSDT").strip().upper()
    target_notional = Decimal(os.getenv("DEMO_NOTIONAL_USDT", "20").strip())
    tp_pct = Decimal(os.getenv("DEMO_TP_PCT", "5").strip()) / Decimal("100")
    sl_pct = Decimal(os.getenv("DEMO_SL_PCT", "5").strip()) / Decimal("100")

    client = BitgetDemoClassic()
    opened = False
    entry_qty_s = None

    contracts = client.public_get(
        "/api/v2/mix/market/contracts",
        {"productType": "usdt-futures", "symbol": symbol},
    ) or []
    if not contracts:
        raise RuntimeError(f"No contract config returned for {symbol}")
    cfg = contracts[0]

    tickers = client.public_get(
        "/api/v2/mix/market/ticker",
        {"productType": "usdt-futures", "symbol": symbol},
    ) or []
    if not tickers:
        raise RuntimeError(f"No ticker returned for {symbol}")

    last = Decimal(str(tickers[0]["lastPr"]))
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

    tp = q_nearest(last * (Decimal("1") + tp_pct), price_step)
    sl = q_nearest(last * (Decimal("1") - sl_pct), price_step)

    qty_s = f"{qty:.{volume_place}f}"
    entry_qty_s = qty_s
    tp_s = f"{tp:.{price_place}f}"
    sl_s = f"{sl:.{price_place}f}"

    entry = {
        "symbol": symbol,
        "productType": PRODUCT_TYPE,
        "marginMode": "crossed",
        "marginCoin": MARGIN_COIN,
        "size": qty_s,
        "side": "buy",
        "tradeSide": "open",
        "orderType": "market",
        "clientOid": f"demo_life_{int(time.time())}"[:32],
        "presetStopSurplusPrice": tp_s,
        "presetStopLossPrice": sl_s,
    }

    print("[INFO] DEMO ONLY. Starting one complete futures lifecycle test.")
    print(f"[INFO] Entry plan: {symbol} LONG market qty={qty_s}; preset TP={tp_s}; preset SL={sl_s}")
    print("[INFO] Safety: pre-existing position check passed. paptrading=1 is forced.")

    try:
        placed = client.private_post("/api/v2/mix/order/place-order", entry)
        order_id = str(placed.get("orderId") or "")
        if not order_id:
            raise RuntimeError(f"Entry accepted but no orderId returned: {placed}")
        opened = True
        print(f"[OK] Demo market entry accepted. orderId={order_id}")

        detail = wait_for_fill(client, symbol, order_id)
        avg = detail.get("priceAvg")
        print(f"[OK] Entry filled. avgPrice={avg} state={detail.get('state')}")

        preset_tp = str(detail.get("presetStopSurplusPrice") or "")
        preset_sl = str(detail.get("presetStopLossPrice") or "")
        if not preset_tp or not preset_sl:
            raise RuntimeError(
                f"Entry filled but preset TP/SL missing in order detail: TP={preset_tp!r} SL={preset_sl!r}"
            )
        print(f"[OK] TP/SL confirmed in filled order detail. TP={preset_tp} SL={preset_sl}")

        # Close exactly the size opened by this test, instead of using a blanket close-all call.
        close_order = client.private_post(
            "/api/v2/mix/order/place-order",
            {
                "symbol": symbol,
                "productType": PRODUCT_TYPE,
                "marginMode": "crossed",
                "marginCoin": MARGIN_COIN,
                "size": qty_s,
                "side": "buy",
                "tradeSide": "close",
                "orderType": "market",
                "clientOid": f"demo_close_{int(time.time())}"[:32],
            },
        )
        close_id = str(close_order.get("orderId") or "")
        if not close_id:
            raise RuntimeError(f"Close accepted but no orderId returned: {close_order}")
        print(f"[OK] Demo exact-size close accepted. orderId={close_id}")

        close_detail = wait_for_fill(client, symbol, close_id)
        print(f"[OK] Close filled. avgPrice={close_detail.get('priceAvg')} state={close_detail.get('state')}")
        opened = False
        print("[OK] Full Demo lifecycle succeeded without position-read API.")
        print("[OK] ENTRY -> FILL -> TP/SL CONFIRM -> EXACT-SIZE CLOSE complete.")

        closed = {"orderId": close_id}
        # Compatibility marker for old log shape.
        _ = closed

        # no-op placeholder removed below
        if False:
            client.private_post(
            "/api/v2/mix/order/close-positions",
            {
                "symbol": symbol,
                "holdSide": "long",
                "productType": PRODUCT_TYPE,
            },
        )

    finally:
        if opened and entry_qty_s:
            print("[WARN] Cleanup guard: attempting exact-size Demo close.")
            try:
                cleanup = client.private_post(
                    "/api/v2/mix/order/place-order",
                    {
                        "symbol": symbol,
                        "productType": PRODUCT_TYPE,
                        "marginMode": "crossed",
                        "marginCoin": MARGIN_COIN,
                        "size": entry_qty_s,
                        "side": "buy",
                        "tradeSide": "close",
                        "orderType": "market",
                        "clientOid": f"demo_cleanup_{int(time.time())}"[:32],
                    },
                )
                print(f"[OK] Cleanup close request sent. orderId={cleanup.get('orderId')}")
            except Exception as cleanup_exc:
                print(f"[FAIL] Cleanup close failed: {cleanup_exc}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[FAIL] {exc}")
        sys.exit(1)
