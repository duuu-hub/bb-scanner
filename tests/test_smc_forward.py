from __future__ import annotations

import pandas as pd
import pytest
from unittest.mock import patch

from smc_demo_forward import (
    HOUR_MS,
    fetch_closed_1h,
    manage_pending_orders,
    order_size,
    setup_age_hours,
    setup_expired_by_clock,
)
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

def test_wall_clock_expiry_blocks_old_setup():
    now = 100 * HOUR_MS
    cfg = {"expiry_bars": 24}
    fresh = {"created_time_ms": now - 23 * HOUR_MS}
    expired = {"created_time_ms": now - 25 * HOUR_MS}
    assert not setup_expired_by_clock(fresh, cfg, now)
    assert setup_expired_by_clock(expired, cfg, now)
    assert setup_age_hours(expired, now) == 25.0


def test_fetch_closed_1h_requires_latest_completed_hour():
    boundary = 100 * HOUR_MS

    class FakeClient:
        def __init__(self, rows):
            self.rows = rows
            self.calls = []

        def public_get(self, path, params):
            self.calls.append((path, params))
            return self.rows

    fresh_rows = [
        [str(boundary - 2 * HOUR_MS), "100", "102", "99", "101", "1"],
        [str(boundary - HOUR_MS), "101", "103", "100", "102", "1"],
        [str(boundary), "102", "104", "101", "103", "1"],
    ]
    client = FakeClient(fresh_rows)
    with patch("smc_demo_forward.now_ms", return_value=boundary + 20 * 60 * 1000):
        frame = fetch_closed_1h(client, "ETHUSDT", 180)
    assert frame["Timestamp"].iloc[-1] == pd.Timestamp(
        boundary - HOUR_MS, unit="ms", tz="UTC"
    )
    assert client.calls[0][0] == "/api/v2/mix/market/candles"

    stale_rows = [
        [str(boundary - 3 * HOUR_MS), "100", "102", "99", "101", "1"],
        [str(boundary - 2 * HOUR_MS), "101", "103", "100", "102", "1"],
    ]
    stale_client = FakeClient(stale_rows)
    with patch("smc_demo_forward.now_ms", return_value=boundary + 20 * 60 * 1000):
        with pytest.raises(RuntimeError, match="STALE_1H_DATA"):
            fetch_closed_1h(stale_client, "ETHUSDT", 180)


def test_pending_order_is_cancelled_by_wall_clock_expiry():
    now = 100 * HOUR_MS
    pending = {
        "setup_id": "old",
        "strategy": "ETH_D3",
        "symbol": "ETHUSDT",
        "side": "short",
        "created_time_ms": now - 25 * HOUR_MS,
        "order_id": "123",
    }
    state = {
        "pending_orders": [pending],
        "open_trades": [],
        "closed_trades": [],
        "processed_setup_ids": ["old"],
        "skipped_events": [],
    }
    cfg = {"expiry_bars": 24, "telegram_trade_notifications": False}

    class FakeClient:
        pass

    with patch("smc_demo_forward.order_detail", return_value={"state": "live", "baseVolume": "0"}), \
         patch("smc_demo_forward.cancel_pending") as cancel:
        manage_pending_orders(FakeClient(), cfg, state, {"old"}, now)

    cancel.assert_called_once()
    assert state["pending_orders"] == []

