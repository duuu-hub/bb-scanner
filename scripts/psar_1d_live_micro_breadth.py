#!/usr/bin/env python3
"""PSAR 1D SHORT micro-risk / high-breadth portfolio study.

Purpose: preserve the frozen signal + 10% protective stop, then test whether
accepting many more simultaneous signals with tiny per-position risk restores
the cross-sectional edge. All configuration selection is TRAIN-only.
"""
import argparse, json
from pathlib import Path
import pandas as pd
import psar_1d_live_portfolio as p
import psar_1d_live_admission as a

FIXED_STOP = "PCT_10"
HASH_SEED = 8
BREADTH_GRID = (20, 30, 50, 100, 200)
RISK_GRID_PCT = (0.01, 0.02, 0.03, 0.05, 0.075, 0.10, 0.125, 0.15)
TOTAL_STOP_RISK_CAP_PCT = 2.0
TOTAL_EXPOSURE_CAP = 0.20  # 20% gross x fixed 10% stop = 2% open stop-risk cap
TRAIN_MDD_CEILING_PCT = 25.0

def save_json(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, allow_nan=False))

def slim(r):
    return {k:v for k,v in r.items() if k not in ("realized_trades","curve")}

def run_cfg(trades, maxpos, risk_pct, rb=40, sb=10, fb=2):
    old_max = p.MAX_POSITIONS
    old_exp = p.TOTAL_EXPOSURE_CAP
    try:
        p.MAX_POSITIONS = int(maxpos)
        p.TOTAL_EXPOSURE_CAP = TOTAL_EXPOSURE_CAP
        remapped = a.remap(trades, "HASH_MEDIAN", HASH_SEED)
        return p.portfolio_sim(
            remapped,
            float(risk_pct)/100.0,
            roundtrip_bps=rb,
            stop_slip_bps=sb,
            funding_bps_day=fb,
        )
    finally:
        p.MAX_POSITIONS = old_max
        p.TOTAL_EXPOSURE_CAP = old_exp

def add_coverage(row, raw_n):
    out = slim(row)
    out["raw_signals"] = int(raw_n)
    out["coverage_pct"] = float(out["accepted"] / raw_n * 100.0) if raw_n else 0.0
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--canonical", required=True)
    ap.add_argument("--out", required=True)
    x = ap.parse_args()

    out = Path(x.out)
    out.mkdir(parents=True, exist_ok=True)

    train, hold, canonical = p.load_canonical(x.canonical)
    idx = p.index_raw_files(x.data)
    spec = [s for s in p.STOP_SPECS if s[0] == FIXED_STOP]

    _, ttr = p.scan_signals(train, idx, spec, keep=FIXED_STOP)
    rows = []
    for maxpos in BREADTH_GRID:
        for risk_pct in RISK_GRID_PCT:
            z = run_cfg(ttr, maxpos, risk_pct)
            rows.append({
                "max_positions": int(maxpos),
                "risk_fraction_pct": float(risk_pct),
                "equivalent_notional_pct_per_position": float(risk_pct * 10.0),
                "total_stop_risk_cap_pct": TOTAL_STOP_RISK_CAP_PCT,
                **add_coverage(z, len(ttr)),
            })
    grid = pd.DataFrame(rows)
    grid.to_csv(out / "train_micro_breadth_grid.csv", index=False)

    eligible = grid[
        (~grid.bankrupt)
        & (grid.ending_equity > 1.0)
        & (grid.mdd_pct <= TRAIN_MDD_CEILING_PCT)
    ]
    if len(eligible):
        ch = eligible.sort_values(
            ["ending_equity", "mdd_pct", "coverage_pct", "risk_fraction_pct", "max_positions"],
            ascending=[False, True, False, True, True],
        ).iloc[0]
        status = "profitable_train_configuration_found"
    else:
        ch = grid.sort_values(
            ["ending_equity", "mdd_pct", "coverage_pct", "risk_fraction_pct", "max_positions"],
            ascending=[False, True, False, True, True],
        ).iloc[0]
        status = "no_profitable_train_configuration"

    chosen_maxpos = int(ch.max_positions)
    chosen_risk_pct = float(ch.risk_fraction_pct)
    freeze = {
        "status": status,
        "fixed_stop": FIXED_STOP,
        "fixed_admission": "HASH_MEDIAN",
        "fixed_hash_seed": HASH_SEED,
        "breadth_grid": list(BREADTH_GRID),
        "risk_grid_pct": list(RISK_GRID_PCT),
        "total_stop_risk_cap_pct": TOTAL_STOP_RISK_CAP_PCT,
        "gross_exposure_cap_pct": TOTAL_EXPOSURE_CAP * 100.0,
        "train_mdd_ceiling_pct": TRAIN_MDD_CEILING_PCT,
        "selection_rule": "TRAIN only: maximize ending equity among non-bankrupt configs with ending_equity>1 and MDD<=25%; then lower MDD, higher signal coverage, lower risk, lower breadth. If none qualify, same ordering without profitability constraint.",
        "chosen_max_positions": chosen_maxpos,
        "chosen_risk_fraction_pct": chosen_risk_pct,
        "chosen_notional_pct_per_position_at_10pct_stop": chosen_risk_pct * 10.0,
    }
    save_json(out / "frozen_micro_breadth_choice.json", freeze)

    train_final = run_cfg(ttr, chosen_maxpos, chosen_risk_pct)

    _, htr = p.scan_signals(hold, idx, spec, keep=FIXED_STOP)
    hold_final = run_cfg(htr, chosen_maxpos, chosen_risk_pct)

    stress = []
    for name, rb, sb, fb in [
        ("BASE", 40, 10, 2),
        ("NO_FUNDING", 40, 10, 0),
        ("MODERATE", 60, 25, 5),
        ("SEVERE", 80, 50, 10),
    ]:
        z = run_cfg(htr, chosen_maxpos, chosen_risk_pct, rb, sb, fb)
        stress.append({
            "scenario": name,
            "roundtrip_bps": rb,
            "stop_slip_bps": sb,
            "funding_bps_per_day": fb,
            **add_coverage(z, len(htr)),
        })
    pd.DataFrame(stress).to_csv(out / "holdout_micro_stress.csv", index=False)

    summary = {
        "study": "PSAR 1D SHORT micro-risk high-breadth rescue",
        "canonical": {
            "selected_max_days": canonical["selected_max_days"],
            "d0_threshold": canonical["d0_threshold"],
        },
        "freeze": freeze,
        "train": add_coverage(train_final, len(ttr)),
        "holdout": add_coverage(hold_final, len(htr)),
        "holdout_stress": stress,
        "honesty": [
            "All breadth/risk selection uses TRAIN only.",
            "HOLDOUT has been observed in earlier retrofit studies, so this remains diagnostic rather than pristine OOS.",
            "Signal rule, 10% stop, and neutral hash admission seed are fixed before this grid.",
            "2% total open stop-risk cap is implemented as a 20% gross notional cap because every selected position uses the same fixed 10% stop.",
        ],
    }
    save_json(out / "summary.json", summary)

    (out / "REPORT.md").write_text(
        f"""# PSAR 1D micro-risk high-breadth rescue

Fixed protective stop: **10%**
Neutral admission: deterministic hash seed **{HASH_SEED}**
Total open stop-risk cap: **{TOTAL_STOP_RISK_CAP_PCT:.2f}%**

Chosen TRAIN configuration:
- max positions: **{chosen_maxpos}**
- per-position stop-risk: **{chosen_risk_pct:.3f}%**
- per-position notional at 10% stop: **{chosen_risk_pct*10:.3f}%**

TRAIN:
- return: **{train_final['total_return_pct']:+.3f}%**
- MDD: **{train_final['mdd_pct']:.3f}%**
- accepted: **{train_final['accepted']} / {len(ttr)} ({train_final['accepted']/len(ttr)*100:.2f}%)**

HOLDOUT:
- return: **{hold_final['total_return_pct']:+.3f}%**
- MDD: **{hold_final['mdd_pct']:.3f}%**
- accepted: **{hold_final['accepted']} / {len(htr)} ({hold_final['accepted']/len(htr)*100:.2f}%)**

Selection was TRAIN-only. HOLDOUT is diagnostic because earlier studies already exposed this period.
"""
    )
    print(json.dumps(summary, indent=2), flush=True)

if __name__ == "__main__":
    main()
