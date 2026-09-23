from __future__ import annotations

import pandas as pd

from smc_demo_forward import order_size
from smc_forward_engine import (
    SMCConfig,
    detect_setup_at,
    replay_active_setups,
    setup_id,
)


def long_fixture(extra_rows: int = 0, touch: bool = False) -> pd.DataFrame:
    rows = []
    start = pd.Timestamp("2026-01-01T00:00:00Z")
    for i in range(14):
        rows.append(
            {
                "Timestamp": start + pd.Timedelta(hours=i),
                "Open": 100.0,
                "High": 102.0,
                "Low": 99.0,
                "Close": 101.0,
                "Volume": 1.0,
            }
        )

    # Confirmed unique swing low.
    rows[5].update(Open=96.0, High=98.0, Low=90.0, Close=97.0)

    # Sweep candle doubles as the bearish order block.
    rows[10].update(Open=95.0, High=96.0, Low=89.0, Close=92.0)

    # Exactly three bullish displacement bars. Final close breaks OB high.
    rows[11].update(Open=92.0, High=95.0, Low=91.0, Close=94.0)
    rows[12].update(Open=94.0, High=97.0, Low=93.0, Close=96.0)
    rows[13].update(Open=96.0, High=99.0, Low=95.0, Close=98.0)

    for j in range(extra_rows):
        i = 14 + j
        low = 95.0 if touch and j == 0 else 97.0
        rows.append(
            {
                "Timestamp": start + pd.Timedelta(hours=i),
                "Open": 99.0,
                "High": 101.0,
                "Low": low,
                "Close": 100.0,
                "Volume": 1.0,
            }
        )
    return pd.DataFrame(rows)


def test_causal_long_setup_detected():
    df = long_fixture()
    cfg = SMCConfig(displacement_bars=3)
    setup = detect_setup_at(df, 13, cfg)
    assert setup is not None
    assert setup.side == "long"
    assert setup.entry == 96.0
    assert setup.stop < setup.entry
    assert setup.target > setup.entry
    assert len(replay_active_setups(df, cfg)) == 1


def test_future_candle_does_not_change_created_setup():
    prefix = long_fixture()
    future = long_fixture(extra_rows=1)
    cfg = SMCConfig(displacement_bars=3)

    a = detect_setup_at(prefix, 13, cfg)
    b = detect_setup_at(future, 13, cfg)
    assert a is not None and b is not None
    assert a == b
    assert setup_id("ETH_D3", a) == setup_id("ETH_D3", b)


def test_touch_removes_pending_setup():
    cfg = SMCConfig(displacement_bars=3)
    df = long_fixture(extra_rows=1, touch=True)
    assert replay_active_setups(df, cfg) == []


def test_expiry_removes_untouched_setup():
    cfg = SMCConfig(displacement_bars=3, expiry_bars=24)
    # 24 bars after creation is the expiry bar when no touch occurs.
    df = long_fixture(extra_rows=24, touch=False)
    assert replay_active_setups(df, cfg) == []


def test_order_size_respects_notional_cap():
    contract = {
        "minTradeNum": "0.001",
        "sizeMultiplier": "0.001",
        "minTradeUSDT": "5",
        "volumePlace": "3",
    }
    cfg = {
        "risk_pct_per_trade": 0.5,
        "max_notional_pct_per_trade": 20.0,
    }
    qty, notional, risk = order_size(
        contract=contract,
        entry=__import__("decimal").Decimal("100"),
        stop=__import__("decimal").Decimal("95"),
        equity=__import__("decimal").Decimal("1000"),
        remaining_notional=__import__("decimal").Decimal("1000"),
        cfg=cfg,
    )
    assert qty == "1.000"
    assert float(notional) == 100.0
    assert float(risk) == 5.0
