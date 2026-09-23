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


def private_get(api_key, secret_key, passphrase, path, params=None):
    params = params or {}
    query_string = urlencode(params)
    timestamp = str(int(time.time() * 1000))
    headers = {
        "ACCESS-KEY": api_key,
        "ACCESS-SIGN": sign(secret_key, timestamp, "GET", path, query_string=query_string),
        "ACCESS-TIMESTAMP": timestamp,
        "ACCESS-PASSPHRASE": passphrase,
        "Content-Type": "application/json",
        "locale": "en-US",
        "paptrading": "1",
        "User-Agent": "bb-scanner-demo-check/1.0",
    }
    response = requests.get(BASE_URL + path, params=params, headers=headers, timeout=15)
    payload = response.json()
    return response, payload


def main():
    api_key = required_env("BITGET_DEMO_API_KEY")
    secret_key = required_env("BITGET_DEMO_SECRET_KEY")
    passphrase = required_env("BITGET_DEMO_PASSPHRASE")

    print("[INFO] Testing Bitget Demo API authentication.")
    print("[INFO] Read-only diagnostics only. No order will be created, modified, or cancelled.")

    response, payload = private_get(
        api_key,
        secret_key,
        passphrase,
        REQUEST_PATH,
        {"category": "USDT-FUTURES", "limit": "1"},
    )
    code = str(payload.get("code", ""))
    msg = str(payload.get("msg", ""))
    if not (response.ok and code == "00000"):
        print(f"[FAIL] Order-query auth failed. HTTP={response.status_code}, code={code}, msg={msg}")
        sys.exit(5)

    data = payload.get("data") or {}
    orders = data.get("list") or []
    print("[OK] Bitget Demo API authentication succeeded.")
    print(f"[OK] Demo USDT-FUTURES open-order query succeeded. Returned orders: {len(orders)}")

    # Account API permission check. Do not print UID or other identifying values.
    try:
        r_info, p_info = private_get(api_key, secret_key, passphrase, "/api/v3/account/info")
        if r_info.ok and str(p_info.get("code")) == "00000":
            info = p_info.get("data") or {}
            print(f"[INFO] API permission type: {info.get('permType')}")
            print(f"[INFO] API permissions: {info.get('permissions')}")
        else:
            print(f"[WARN] Account-info query failed: code={p_info.get('code')} msg={p_info.get('msg')}")
    except Exception as exc:
        print(f"[WARN] Account-info query error: {exc}")

    try:
        r_settings, p_settings = private_get(api_key, secret_key, passphrase, "/api/v3/account/settings")
        if r_settings.ok and str(p_settings.get("code")) == "00000":
            s = p_settings.get("data") or {}
            print(f"[INFO] accountMode={s.get('accountMode')} accountLevel={s.get('accountLevel')} holdMode={s.get('holdMode')}")
        else:
            print(f"[WARN] Account-settings query failed: code={p_settings.get('code')} msg={p_settings.get('msg')}")
    except Exception as exc:
        print(f"[WARN] Account-settings query error: {exc}")

    try:
        r_assets, p_assets = private_get(api_key, secret_key, passphrase, "/api/v3/account/assets")
        if r_assets.ok and str(p_assets.get("code")) == "00000":
            data = p_assets.get("data") or {}
            assets = data.get("assets") if isinstance(data, dict) else data
            assets = assets or []
            usdt = next((x for x in assets if str(x.get("coin", "")).upper() == "USDT"), None)
            if usdt:
                keep = {k: usdt.get(k) for k in ("coin", "available", "equity", "balance", "bonus", "positionValue", "leverage") if k in usdt}
                print(f"[INFO] Demo USDT asset snapshot: {keep}")
            else:
                summary = []
                for item in assets:
                    summary.append({k: item.get(k) for k in ("coin", "available", "equity", "balance", "bonus") if k in item})
                print(f"[INFO] Demo asset rows (no USDT row): {summary}")
        else:
            print(f"[WARN] Account-assets query failed: code={p_assets.get('code')} msg={p_assets.get('msg')}")
    except Exception as exc:
        print(f"[WARN] Account-assets query error: {exc}")

    try:
        r_pos, p_pos = private_get(
            api_key,
            secret_key,
            passphrase,
            "/api/v2/mix/position/single-position",
            {
                "symbol": "BTCUSDT",
                "productType": "USDT-FUTURES",
                "marginCoin": "USDT",
            },
        )
        if r_pos.ok and str(p_pos.get("code")) == "00000":
            rows = p_pos.get("data") or []
            active = [
                {
                    "symbol": x.get("symbol"),
                    "holdSide": x.get("holdSide"),
                    "total": x.get("total"),
                }
                for x in rows
                if str(x.get("total") or "0") not in {"0", "0.0", ""}
            ]
            print(f"[OK] Classic futures position query succeeded. Active BTCUSDT rows: {active}")
        else:
            print(
                f"[WARN] Classic futures position query failed: "
                f"HTTP={r_pos.status_code} code={p_pos.get('code')} msg={p_pos.get('msg')}"
            )
    except Exception as exc:
        print(f"[WARN] Classic futures position query error: {exc}")

    print("[OK] Read-only diagnostic completed. paptrading=1 remained active.")


if __name__ == "__main__":
    main()
