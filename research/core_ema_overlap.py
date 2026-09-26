from pathlib import Path
import io, requests, pandas as pd

CORE=Path("research_output/btc_eth_regime_stage8_year_anatomy/episodes.csv")
OUT=Path("research_output/core_ema_overlap")
EMA_URL="https://raw.githubusercontent.com/duuu-hub/bb-scanner/research-btc-strategy-lab-replication/research/btc_strategy_lab_replication/out/funding_trades.csv"

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    core=pd.read_csv(CORE)
    core["entry"]=pd.to_datetime(core["entry"],utc=True)
    core["exit"]=pd.to_datetime(core["exit"],utc=True)
    ema=pd.read_csv(io.StringIO(requests.get(EMA_URL,timeout=30).text))
    ema["entry_time"]=pd.to_datetime(ema["entry_time"],utc=True)
    ema["exit_time"]=pd.to_datetime(ema["exit_time"],utc=True)

    start=max(core["entry"].min(),ema["entry_time"].min())
    end=min(core["exit"].max(),ema["exit_time"].max())
    days=pd.date_range(start.normalize(),end.normalize(),freq="D",tz="UTC")
    rows=[]
    for d in days:
        d1=d+pd.Timedelta(days=1)
        c=((core["entry"]<d1)&(core["exit"]>=d)).any()
        e=((ema["entry_time"]<d1)&(ema["exit_time"]>=d)).any()
        rows.append((d,c,e,c and e))
    x=pd.DataFrame(rows,columns=["date","core_active","ema_active","overlap"])
    x.to_csv(OUT/"daily_overlap.csv",index=False)
    c=int(x.core_active.sum()); e=int(x.ema_active.sum()); o=int(x.overlap.sum())
    summary=pd.DataFrame([{
        "common_start":start,"common_end":end,"common_calendar_days":len(x),
        "core_active_days":c,"ema_active_days":e,"overlap_days":o,
        "overlap_pct_of_core":100*o/c if c else 0,
        "overlap_pct_of_ema":100*o/e if e else 0,
        "jaccard_pct":100*o/(c+e-o) if c+e-o else 0
    }])
    summary.to_csv(OUT/"summary.csv",index=False)
    print(summary.to_string(index=False))

if __name__=="__main__": main()
