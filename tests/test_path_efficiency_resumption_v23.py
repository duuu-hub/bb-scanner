import copy

import numpy as np

from scripts import day_edge_lab as base
from scripts import path_efficiency_resumption_v23 as v23


def fixture(side=1, flow=.60):
    n, i, w = 140, 110, 8
    t = base.START + np.arange(n, dtype=np.int64) * base.BAR
    c = np.full(n, 100.0)
    formation = np.exp(np.linspace(np.log(100), np.log(102), w + 1))
    if side == -1:
        formation = np.exp(np.linspace(np.log(102), np.log(100), w + 1))
    c[i-w-2:i-1] = formation
    move = abs(np.log(formation[-1] / formation[0]))
    c[i-1] = formation[-1] * np.exp(-side * .20 * move)
    c[i] = c[i-1] * np.exp(side * .006)
    c[i+1:] = c[i]
    o = c.copy()
    o[i-1] = formation[-1]
    o[i] = c[i-1]
    h, l = np.maximum(o, c) * 1.0005, np.minimum(o, c) * .9995
    q = np.full(n, 1_000_000.0)
    q[i-1], q[i] = 900_000.0, 1_500_000.0
    f = {
        "_quote": q,
        "eligible": np.ones(n, dtype=bool),
        "prior_atr": np.full(n, .50),
        "prior_quote_mean": np.full(n, 1_000_000.0),
        "buy_share": np.full(n, flow if side == 1 else 1-flow),
        "volume_multiple": q / 1_000_000.0,
        "r1": np.zeros(n),
        "clv": np.full(n, .5),
    }
    raw = (t, o, h, l, c)
    cfg = next(x for x in v23.configurations()
               if x["side"] == side and x["window"] == 8
               and x["efficiency"] == .55 and x["confirmation"] == "FLOW55")
    return i, cfg, raw, f


def test_frozen_grid_is_16_entries_and_96_policies():
    assert len(v23.configurations()) == 16
    assert len(v23.policies()) == 96
    assert len({x["key"] for x in v23.configurations()}) == 16
    assert {x["exit_type"] for x in v23.policies()} == {"R15", "R25", "TRAIL"}


def test_smooth_long_path_pause_and_resumption_qualifies():
    i, cfg, raw, f = fixture()
    result = v23.setup_at(i, cfg, raw, f)
    assert result is not None
    assert result["formation_efficiency"] > .99
    assert .05 <= result["pause_retrace"] <= .35


def test_symmetric_short_setup_qualifies():
    i, cfg, raw, f = fixture(side=-1)
    assert v23.setup_at(i, cfg, raw, f) is not None


def test_flow55_is_directional_and_price_only_is_not():
    i, cfg, raw, f = fixture(flow=.40)
    assert v23.setup_at(i, cfg, raw, f) is None
    price_cfg = dict(cfg, confirmation="PRICE_ONLY")
    assert v23.setup_at(i, price_cfg, raw, f) is not None


def test_one_bar_dominated_formation_is_rejected():
    i, cfg, raw, f = fixture()
    c = raw[4].copy()
    start = i - cfg["window"] - 2
    c[start + 1:i-1] = c[start + 1]
    bad = (raw[0], raw[1], np.maximum(raw[1], c)*1.0005,
           np.minimum(raw[1], c)*.9995, c)
    assert v23.setup_at(i, cfg, bad, f) is None


def test_deep_pause_is_rejected():
    i, cfg, raw, f = fixture()
    c = raw[4].copy(); o = raw[1].copy()
    finish = i - 2
    move = abs(np.log(c[finish] / c[i-cfg["window"]-2]))
    c[i-1] = c[finish] * np.exp(-cfg["side"] * .50 * move)
    o[i] = c[i-1]
    bad = (raw[0], o, np.maximum(o, c)*1.0005, np.minimum(o, c)*.9995, c)
    assert v23.setup_at(i, cfg, bad, f) is None


def test_future_prices_do_not_change_signal_or_entry_seed():
    i, cfg, raw, f = fixture()
    btc = {"r1": np.zeros(len(raw[0]))}
    end = int(raw[0][-1] + base.BAR)
    before, _ = v23.intents("XUSDT", cfg, raw, f, btc, base.START, end)
    changed = tuple(x.copy() for x in raw)
    changed[1][i+2:] *= 4; changed[2][i+2:] *= 4
    changed[3][i+2:] *= .25; changed[4][i+2:] *= 2
    after, _ = v23.intents("XUSDT", cfg, changed, f, btc, base.START, end)
    assert before == after and len(before) == 1


def test_scan_always_forwards_v23_context(monkeypatch):
    seen = {}
    def fake_scan(*args, **kwargs):
        seen.update(kwargs)
        return "ok"
    monkeypatch.setattr(v23.engine, "scan", fake_scan)
    assert v23.scan(1, 2, 3, 4, 5, 6) == "ok"
    assert seen["context_path"] == v23.CONTEXT

