from __future__ import annotations
import numpy as np

BAR_MS=900_000

def next_bar_entry_index(g, signal_i:int):
    ei=signal_i+1
    if ei>=len(g): return None
    signal_ts=int(g.iloc[signal_i].timestamp_ms)
    if int(g.iloc[ei].timestamp_ms)!=signal_ts+BAR_MS: return None
    return ei

def replay_trade(g, signal_i:int, side:str, tp_pct:float, sl_pct:float, horizon_bars:int):
    """Single source of truth for continuation fills.
    Signal is known only after signal candle close. Entry is exactly next 15m open.
    Entry candle IS eligible for exits. Horizon is wall-clock from entry; candles with
    open timestamp < deadline are eligible. Same-candle TP+SL is conservative SL.
    """
    ei=next_bar_entry_index(g,signal_i)
    if ei is None: return None
    ep=float(g.iloc[ei].open)
    entry_ts=int(g.iloc[ei].timestamp_ms)
    deadline=entry_ts+int(horizon_bars)*BAR_MS
    ts=g.timestamp_ms.to_numpy()
    end=int(np.searchsorted(ts,deadline,side="left"))-1
    end=max(ei,min(len(g)-1,end))
    for j in range(ei,end+1):
        hi=float(g.iloc[j].high); lo=float(g.iloc[j].low)
        if side=="LONG":
            hit_tp=hi>=ep*(1+tp_pct/100); hit_sl=lo<=ep*(1-sl_pct/100)
        else:
            hit_tp=lo<=ep*(1-tp_pct/100); hit_sl=hi>=ep*(1+sl_pct/100)
        if hit_tp and hit_sl: return dict(entry_i=ei,exit_i=j,entry_px=ep,gross_ret_pct=-sl_pct,exit_reason="BOTH_SL")
        if hit_tp: return dict(entry_i=ei,exit_i=j,entry_px=ep,gross_ret_pct=tp_pct,exit_reason="TP")
        if hit_sl: return dict(entry_i=ei,exit_i=j,entry_px=ep,gross_ret_pct=-sl_pct,exit_reason="SL")
    px=float(g.iloc[end].close)
    ret=(px/ep-1)*100*(1 if side=="LONG" else -1)
    return dict(entry_i=ei,exit_i=end,entry_px=ep,gross_ret_pct=ret,exit_reason="TIME")

def barrier_label(g,signal_i,direction,target,stop,bars):
    r=replay_trade(g,signal_i,direction,target,stop,bars)
    if r is None:return "NO_ENTRY"
    return {"TP":"WIN","SL":"LOSS","BOTH_SL":"AMBIG","TIME":"TIMEOUT"}[r["exit_reason"]]
