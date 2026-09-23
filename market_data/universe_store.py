from __future__ import annotations

import gzip
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

DEFAULT_ROOT = Path("market_data_store/bitget/universe_15m")


def _days(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def _path(root: Path, day: date) -> Path:
    return root / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.isoformat()}.csv.gz"


def load_symbol(
    symbol: str,
    start: str,
    end: str,
    root: str | Path = DEFAULT_ROOT,
) -> pd.DataFrame:
    """Load one symbol from immutable daily universe partitions."""
    root = Path(root)
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    frames = []

    for day in _days(start_ts.date(), end_ts.date()):
        path = _path(root, day)
        if not path.exists():
            continue
        chunk = pd.read_csv(
            path,
            compression="gzip",
            usecols=[
                "symbol",
                "timestamp_ms",
                "open",
                "high",
                "low",
                "close",
                "base_volume",
                "quote_volume",
            ],
        )
        chunk = chunk.loc[chunk["symbol"] == symbol]
        if not chunk.empty:
            frames.append(chunk)

    if not frames:
        return pd.DataFrame(
            columns=[
                "symbol",
                "timestamp_ms",
                "datetime_utc",
                "open",
                "high",
                "low",
                "close",
                "base_volume",
                "quote_volume",
            ]
        )

    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates("timestamp_ms").sort_values("timestamp_ms")
    df["datetime_utc"] = pd.to_datetime(df["timestamp_ms"], unit="ms", utc=True)
    df = df.loc[
        (df["datetime_utc"] >= start_ts) & (df["datetime_utc"] <= end_ts)
    ].reset_index(drop=True)
    return df


def resample_ohlcv(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Resample stored 15m market data causally to a higher timeframe."""
    rules = {
        "30m": "30min",
        "1h": "1h",
        "4h": "4h",
        "12h": "12h",
        "1d": "1D",
    }
    key = timeframe.lower()
    if key == "15m":
        return df.copy()
    if key not in rules:
        raise ValueError(f"Unsupported timeframe: {timeframe}")

    if df.empty:
        return df.copy()

    x = df.set_index("datetime_utc")
    out = x.resample(rules[key], label="left", closed="left").agg(
        symbol=("symbol", "first"),
        timestamp_ms=("timestamp_ms", "first"),
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        base_volume=("base_volume", "sum"),
        quote_volume=("quote_volume", "sum"),
    )
    return out.dropna(subset=["open", "high", "low", "close"]).reset_index()



def load_range(
    start: str,
    end: str,
    *,
    symbols: list[str] | tuple[str, ...] | set[str] | None = None,
    root: str | Path = DEFAULT_ROOT,
) -> pd.DataFrame:
    """Load a date range while reading each daily partition only once.

    This is the preferred path for multi-symbol research. Calling load_symbol()
    repeatedly would re-read the same compressed daily files for every symbol.
    """
    root = Path(root)
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    wanted = set(symbols or [])
    frames = []

    for day in _days(start_ts.date(), end_ts.date()):
        path = _path(root, day)
        if not path.exists():
            continue
        chunk = pd.read_csv(
            path,
            compression="gzip",
            usecols=[
                "symbol",
                "timestamp_ms",
                "open",
                "high",
                "low",
                "close",
                "base_volume",
                "quote_volume",
            ],
        )
        if wanted:
            chunk = chunk.loc[chunk["symbol"].isin(wanted)]
        if not chunk.empty:
            frames.append(chunk)

    if not frames:
        return pd.DataFrame(
            columns=[
                "symbol",
                "timestamp_ms",
                "datetime_utc",
                "open",
                "high",
                "low",
                "close",
                "base_volume",
                "quote_volume",
            ]
        )

    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(["symbol", "timestamp_ms"]).sort_values(
        ["symbol", "timestamp_ms"]
    )
    df["datetime_utc"] = pd.to_datetime(df["timestamp_ms"], unit="ms", utc=True)
    df = df.loc[
        (df["datetime_utc"] >= start_ts) & (df["datetime_utc"] <= end_ts)
    ].reset_index(drop=True)
    return df


def resample_universe(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Resample a multi-symbol 15m frame without mixing symbols."""
    if timeframe.lower() == "15m":
        return df.copy()
    if df.empty:
        return df.copy()

    parts = []
    for symbol, group in df.groupby("symbol", sort=False):
        out = resample_ohlcv(group.copy(), timeframe)
        if not out.empty:
            out["symbol"] = symbol
            parts.append(out)
    if not parts:
        return df.iloc[0:0].copy()
    return pd.concat(parts, ignore_index=True).sort_values(
        ["symbol", "datetime_utc"]
    ).reset_index(drop=True)
