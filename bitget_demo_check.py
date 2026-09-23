import base64
import hashlib
import hmac
import os
import sys
import time
from urllib.parse import urlencode

import requests

BASE_URL = "https://api.bitget.com"
REQUEST_PATH = "/api/v3/trade/unfilled-orders"


def required_env(name):
    value = os.getenv(name, "").strip()
    if not value:
        print(f"[FAIL] Missing GitHub secret: {name}")
        sys.exit(2)
    return value


def sign(secret, timestamp, method, request_path, query_string="", body=""):
    prehash = f"{timestamp}{method.upper()}{request_path}"
    if query_string:
        prehash += f"?{query_string}"
    prehash += body
    digest = hmac.new(
        secret.encode("utf-8"),
        prehash.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def main():
    api_key = required_env("BITGET_DEMO_API_KEY")
    secret_key = required_env("BITGET_DEMO_SECRET_KEY")
    passphrase = required_env("BITGET_DEMO_PASSPHRASE")

    params = {"category": "USDT-FUTURES", "limit": "1"}
    query_string = urlencode(params)
    timestamp = str(int(time.time() * 1000))

    headers = {
        "ACCESS-KEY": api_key,
        "ACCESS-SIGN": sign(
            secret_key,
            timestamp,
            "GET",
            REQUEST_PATH,
            query_string=query_string,
        ),
        "ACCESS-TIMESTAMP": timestamp,
        "ACCESS-PASSPHRASE": passphrase,
        "Content-Type": "application/json",
        "locale": "en-US",
        "paptrading": "1",
        "User-Agent": "bb-scanner-demo-check/1.0",
    }

    print("[INFO] Testing Bitget Demo API authentication.")
    print("[INFO] Read-only request only. No order will be created, modified, or cancelled.")

    try:
        response = requests.get(
            BASE_URL + REQUEST_PATH,
            params=params,
            headers=headers,
            timeout=15,
        )
    except requests.RequestException as exc:
        print(f"[FAIL] Network request failed: {exc}")
        sys.exit(3)

    try:
        payload = response.json()
    except ValueError:
        print(f"[FAIL] Non-JSON response. HTTP {response.status_code}")
        sys.exit(4)

    code = str(payload.get("code", ""))
    msg = str(payload.get("msg", ""))

    if response.ok and code == "00000":
        data = payload.get("data") or {}
        orders = data.get("list") or []
        print("[OK] Bitget Demo API authentication succeeded.")
        print(f"[OK] Demo USDT-FUTURES open-order query succeeded. Returned orders: {len(orders)}")
        print("[OK] paptrading=1 is active. This test did NOT place an order.")
        return

    print(f"[FAIL] Bitget API rejected the request. HTTP={response.status_code}, code={code}, msg={msg}")
    print("[HINT] Check the demo API key, secret, passphrase, and Futures Orders permission.")
    sys.exit(5)


if __name__ == "__main__":
    main()
