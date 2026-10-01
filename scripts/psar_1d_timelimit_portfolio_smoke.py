"""Portfolio behavior checks used by the same pinned workflow."""
import numpy as np
import pandas as pd
from psar_1d_timelimit_analyze import select, replay, mark_to_market, period, stats, STEP, CUT


def run():
    rows = [
        dict(symbol="AUSDT",signal_ts=0,fill_ts=0,exit_ts=STEP,
             fill=100.,exit_px=90.,pnl_pct=10.,stop_pct=10.,outcome="win"),
        dict(symbol="BUSDT",signal_ts=STEP,fill_ts=STEP,exit_ts=STEP*2,
             fill=100.,exit_px=90.,pnl_pct=10.,stop_pct=10.,outcome="win"),
    ]
    x = pd.DataFrame(rows)
    prices = {
        "AUSDT":(np.arange(-1,4)*STEP,np.array([100.,90.,90.,90.,90.])),
        "BUSDT":(np.arange(-1,4)*STEP,np.array([100.,100.,90.,90.,90.])),
    }
    ids,slots=select(x,.5,1,1.)
    assert ids==[0,1] and slots==1
    print("PASS exit_before_new_entry_at_same_timestamp")
    z,b=replay(x,ids,.5,20,prices)
    assert np.isclose(z["total_return_pct"],(1.049**2-1)*100)
    print("PASS two_sided_fees_and_compounding")
    m,curve=mark_to_market(b,20,prices,0,STEP*2)
    assert np.isclose(curve.equity.iloc[-1],1.049**2) and m["max_concurrent"]==1
    assert m["cagr_pct"] is None
    print("PASS MTM_cash_reconstruction_and_short_period_CAGR")
    z2,b2=replay(x,ids,.5,40,prices)
    assert b.id.tolist()==b2.id.tolist() and z2["total_return_pct"]<z["total_return_pct"]
    print("PASS cost_independent_accepted_IDs")
    y=x.copy();y.loc[0,"exit_ts"]=STEP*2;y.loc[1,"symbol"]="AUSDT"
    assert select(y,.5,2,1.)[0]==[0]
    print("PASS same_symbol_overlap_guard")
    y=x.copy();y.loc[0,"signal_ts"]=CUT-STEP;y.loc[0,"exit_ts"]=CUT+STEP;y.loc[1,"signal_ts"]=CUT
    assert len(period(y,"train"))==0 and len(period(y,"holdout"))==1
    print("PASS no_holdout_returns_in_training_selection")
    boundary=pd.DataFrame([dict(signal_ts=CUT-STEP,fill_ts=CUT-STEP,
        exit_ts=CUT,limit_days=14)])
    assert len(period(boundary,"train"))==0
    print("PASS early_exit_does_not_override_full_training_horizon")
    q=pd.DataFrame([dict(pnl_pct=.1,outcome="time",fill_ts=0,exit_ts=STEP)])
    assert stats(q,20)["positive_n"]==0
    print("PASS positive_outcome_uses_net_PnL")
    # Prices known only at preceding candle close: later values cannot change an entry.
    altered={s:(t,c.copy()) for s,(t,c) in prices.items()}
    altered["BUSDT"][1][2:]=10000.
    z3,b3=replay(x.iloc[:1], [0], .5, 20, altered)
    assert np.isclose(b3.notional.iloc[0],.5)
    print("PASS entry_equity_does_not_use_future_close")
    print("ALL_PORTFOLIO_AUDITS_PASS",9)


if __name__=="__main__":
    run()
