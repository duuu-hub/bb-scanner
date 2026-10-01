#!/usr/bin/env python3
"""
Recollect Binance 1h candles for published token-unlock events and measure ONLY
the scheduled UTC unlock-day move (00:00 UTC open -> each hourly close, through 24h).

Important: the public event CSV exposes unlock_date, not the original on-chain
unlock_timestamp_utc claimed by the paper documentation. Therefore this study
does NOT pretend 00:00 UTC is the exact unlock transaction time.
"""
from __future__ import annotations

import io, json, math, time, zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests

EVENTS_URL = (
    "https://raw.githubusercontent.com/gameworkerkim/vibe-investing/main/"
    "01.Trading%20Strategy/Token%20unlock%2072h%20shock%20analysis%20/"
    "data/01_binance_token_unlock_events_2023_2025.csv"
)
OUT = Path("token_unlock_day0_result")
OUT.mkdir(parents=True, exist_ok=True)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "bb-scanner-token-unlock-research/1.0"})

def get_bytes(url: str, retries: int = 4) -> bytes | None:
    for i in range(retries):
        try:
            r = SESSION.get(url, timeout=30)
            if r.status_code == 200:
                return r.content
            if r.status_code == 404:
                return None
            print(f"HTTP {r.status_code} {url}")
        except Exception as e:
            print(f"GET error {i+1}/{retries}: {e} {url}")
        time.sleep(1.5 * (i + 1))
    return None

def parse_zip_1h(raw: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        name = z.namelist()[0]
        body = z.read(name)
    df = pd.read_csv(io.BytesIO(body), header=None)
    # Newer archives may carry a textual header.
    first = str(df.iloc[0, 0]).lower()
    if "open" in first or not first.replace(".", "", 1).isdigit():
        df = df.iloc[1:].reset_index(drop=True)
    if df.shape[1] < 6:
        raise ValueError(f"unexpected kline columns={df.shape[1]}")
    df = df.iloc[:, :12].copy()
    names = ["open_time","open","high","low","close","volume",
             "close_time","quote_volume","trades","taker_base","taker_quote","ignore"]
    df.columns = names[:df.shape[1]]
    for c in ["open_time","open","high","low","close","volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open_time","open","high","low","close"]).sort_values("open_time")
    return df

_cache: dict[tuple[str,str], tuple[pd.DataFrame | None, str | None]] = {}

def fetch_day(symbol: str, date: str) -> tuple[pd.DataFrame | None, str | None]:
    key = (symbol, date)
    if key in _cache:
        return _cache[key]
    urls = [
        ("spot", f"https://data.binance.vision/data/spot/daily/klines/{symbol}/1h/{symbol}-1h-{date}.zip"),
        ("futures_um", f"https://data.binance.vision/data/futures/um/daily/klines/{symbol}/1h/{symbol}-1h-{date}.zip"),
    ]
    for market, url in urls:
        raw = get_bytes(url)
        if raw is None:
            continue
        try:
            df = parse_zip_1h(raw)
            if len(df) >= 24:
                df = df.iloc[:24].reset_index(drop=True)
                _cache[key] = (df, market)
                return df, market
        except Exception as e:
            print(f"parse failed {symbol} {date} {market}: {e}")
    _cache[key] = (None, None)
    return None, None

def cumret(df: pd.DataFrame, hours: int) -> float:
    entry = float(df.iloc[0]["open"])
    exitp = float(df.iloc[hours-1]["close"])
    return exitp / entry - 1.0

def pf(vals: pd.Series) -> float:
    a = pd.to_numeric(vals, errors="coerce").dropna()
    gains = a[a > 0].sum()
    losses = -a[a < 0].sum()
    if losses <= 0:
        return float("inf") if gains > 0 else float("nan")
    return float(gains / losses)

print("Downloading event metadata...")
events = pd.read_csv(EVENTS_URL)
events.columns = [c.replace("\ufeff", "") for c in events.columns]
events["unlock_date"] = events["unlock_date"].astype(str)
events["unlock_pct_of_supply"] = pd.to_numeric(events["unlock_pct_of_supply"], errors="coerce")
events["unlock_value_usd_m"] = pd.to_numeric(events["unlock_value_usd_m"], errors="coerce")
events["days_from_listing"] = pd.to_numeric(events["days_from_listing"], errors="coerce")

# Enforce the source paper's stated inclusion rules that can be verified from the CSV.
events["strict_eligible"] = (
    (events["unlock_pct_of_supply"] >= 1.0)
    & (events["unlock_value_usd_m"] > 0)
    & (events["days_from_listing"] >= 14)
)

rows = []
hour_rows = []
misses = []
unique = list(events.iterrows())
for idx, (_, e) in enumerate(unique, 1):
    token = str(e["token_symbol"]).strip().upper()
    sym = token + "USDT"
    date = str(e["unlock_date"])
    print(f"[{idx:02d}/{len(unique)}] {token} {date}")
    bars, market = fetch_day(sym, date)
    btc, btc_market = fetch_day("BTCUSDT", date)
    if bars is None or btc is None:
        misses.append({
            "token_symbol": token, "unlock_date": date,
            "token_data": bars is not None, "btc_data": btc is not None,
            "strict_eligible": bool(e["strict_eligible"])
        })
        continue
    entry = float(bars.iloc[0]["open"])
    btc_entry = float(btc.iloc[0]["open"])
    rec = e.to_dict()
    rec.update({
        "market_source": market,
        "bars_n": int(len(bars)),
        "day_open_utc": entry,
        "ret_1h": cumret(bars, 1),
        "ret_3h": cumret(bars, 3),
        "ret_6h": cumret(bars, 6),
        "ret_12h": cumret(bars, 12),
        "ret_24h": cumret(bars, 24),
        "btc_ret_24h": cumret(btc, 24),
        "relative_vs_btc_24h": cumret(bars, 24) - cumret(btc, 24),
        "intraday_low_from_open": float(bars["low"].min()) / entry - 1.0,
        "intraday_high_from_open": float(bars["high"].max()) / entry - 1.0,
    })
    rows.append(rec)
    for h in range(1, 25):
        tr = cumret(bars, h)
        br = cumret(btc, h)
        hour_rows.append({
            "token_symbol": token, "unlock_date": date, "hour": h,
            "ret": tr, "btc_ret": br, "relative": tr-br,
            "strict_eligible": bool(e["strict_eligible"]),
            "unlock_pct": float(e["unlock_pct_of_supply"]),
            "recipient_category": str(e["recipient_category"]),
            "unlock_type": str(e["unlock_type"]),
        })

res = pd.DataFrame(rows)
hourly = pd.DataFrame(hour_rows)
miss = pd.DataFrame(misses)

if res.empty:
    raise SystemExit("No Binance rows collected")

strict = res[res["strict_eligible"] == True].copy()
if len(strict) < 20:
    raise SystemExit(f"Too few strict events with data: {len(strict)}")

def summarize(name: str, d: pd.DataFrame) -> dict:
    if d.empty:
        return {"group": name, "n": 0}
    out = {
        "group": name,
        "n": int(len(d)),
        "negative_day_n": int((d["ret_24h"] < 0).sum()),
        "negative_day_rate": float((d["ret_24h"] < 0).mean()),
        "mean_ret_24h": float(d["ret_24h"].mean()),
        "median_ret_24h": float(d["ret_24h"].median()),
        "mean_btc_adjusted_24h": float(d["relative_vs_btc_24h"].mean()),
        "btc_underperform_rate": float((d["relative_vs_btc_24h"] < 0).mean()),
        "mean_intraday_low": float(d["intraday_low_from_open"].mean()),
        "mean_intraday_high": float(d["intraday_high_from_open"].mean()),
    }
    for bp in (0, 20, 40):
        cost = bp / 10000.0
        s = -d["ret_24h"] - cost
        out[f"short_win_rate_{bp}bp"] = float((s > 0).mean())
        out[f"short_mean_{bp}bp"] = float(s.mean())
        out[f"short_median_{bp}bp"] = float(s.median())
        out[f"short_pf_{bp}bp"] = pf(s)
    return out

rc = strict["recipient_category"].astype(str).str.lower()
groups = [
    ("strict_all", strict),
    (">=3pct", strict[strict["unlock_pct_of_supply"] >= 3]),
    (">=5pct", strict[strict["unlock_pct_of_supply"] >= 5]),
    (">=10pct", strict[strict["unlock_pct_of_supply"] >= 10]),
    ("team_or_investor", strict[rc.str.contains("team|investor", regex=True)]),
    ("team_or_investor_>=5pct", strict[rc.str.contains("team|investor", regex=True) & (strict["unlock_pct_of_supply"] >= 5)]),
    ("cliff", strict[strict["unlock_type"].astype(str).str.lower() == "cliff"]),
    ("linear", strict[strict["unlock_type"].astype(str).str.lower() == "linear"]),
]
summary = pd.DataFrame([summarize(n, d) for n, d in groups])

hs = hourly[hourly["strict_eligible"] == True].copy()
profile = hs.groupby("hour").agg(
    n=("ret","count"),
    mean_ret=("ret","mean"),
    median_ret=("ret","median"),
    negative_rate=("ret", lambda x: float((x<0).mean())),
    mean_btc_adjusted=("relative","mean"),
    btc_underperform_rate=("relative", lambda x: float((x<0).mean())),
).reset_index()

# Sanity/integrity checks.
checks = {
    "source_rows": int(len(events)),
    "source_strict_rows": int(events["strict_eligible"].sum()),
    "collected_rows": int(len(res)),
    "collected_strict_rows": int(len(strict)),
    "missing_rows": int(len(miss)),
    "all_collected_have_24_bars": bool((res["bars_n"] == 24).all()),
    "hour_profile_24_rows": bool(len(profile) == 24),
    "no_duplicate_token_date": bool(~res.duplicated(["token_symbol","unlock_date"]).any()),
}
if not checks["all_collected_have_24_bars"] or not checks["hour_profile_24_rows"]:
    raise AssertionError(checks)

res.to_csv(OUT / "events_day0.csv", index=False)
hourly.to_csv(OUT / "event_hourly_day0.csv", index=False)
summary.to_csv(OUT / "group_summary.csv", index=False)
profile.to_csv(OUT / "hourly_profile.csv", index=False)
miss.to_csv(OUT / "missing_events.csv", index=False)
(OUT / "integrity.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")

def pct(x):
    if x is None or (isinstance(x,float) and math.isnan(x)): return "NA"
    return f"{100*x:.2f}%"

a = summary.iloc[0].to_dict()
g5 = summary[summary["group"]=="team_or_investor_>=5pct"].iloc[0].to_dict()
report = f"""# Token unlock scheduled-day intraday replication

## Scope
- Source event list: published 52-event Binance unlock dataset (2023-2025).
- Price source: Binance Vision raw 1h archives, spot first and USD-M futures fallback.
- This run measures **scheduled UTC calendar day**, from 00:00 UTC open to 23:59:59 UTC close.
- It is **not exact on-chain T0**, because the public event CSV only exposes `unlock_date`, despite documentation claiming an exact timestamp field.
- Strict sample enforces verifiable stated rules: unlock >=1% supply, positive unlock value, >=14 days since Binance listing.

## Strict sample
- N: {int(a['n'])}
- Negative scheduled-day close: {int(a['negative_day_n'])}/{int(a['n'])} = {pct(a['negative_day_rate'])}
- Mean event-day return: {pct(a['mean_ret_24h'])}
- Median event-day return: {pct(a['median_ret_24h'])}
- Mean BTC-adjusted return: {pct(a['mean_btc_adjusted_24h'])}
- BTC underperformance rate: {pct(a['btc_underperform_rate'])}
- Short win rate after 20bp total cost: {pct(a['short_win_rate_20bp'])}
- Short PF after 20bp total cost: {a['short_pf_20bp']:.3f}
- Short win rate after 40bp total cost: {pct(a['short_win_rate_40bp'])}
- Short PF after 40bp total cost: {a['short_pf_40bp']:.3f}

## Team/Investor + >=5% unlock
- N: {int(g5['n'])}
- Negative scheduled-day close: {pct(g5['negative_day_rate'])}
- Mean event-day return: {pct(g5['mean_ret_24h'])}
- Median event-day return: {pct(g5['median_ret_24h'])}
- Short win rate 20bp: {pct(g5['short_win_rate_20bp'])}
- Short PF 20bp: {g5['short_pf_20bp']:.3f}

## Integrity
```json
{json.dumps(checks, indent=2)}
```

See `hourly_profile.csv` for the average/median path at +1h ... +24h and
`events_day0.csv` for event-level raw measurements.
"""
(OUT / "REPORT.md").write_text(report, encoding="utf-8")
print(report)
print(summary.to_string(index=False))
print(profile.to_string(index=False))
