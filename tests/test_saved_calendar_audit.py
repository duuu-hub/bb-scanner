"""Saved-curve reconstruction matches the actual reporting option."""
import unittest
import pandas as pd
import numpy as np
from scripts import audit_saved_calendar as a
from scripts import relative_pullback_portfolio as p

class SavedCalendarTests(unittest.TestCase):
    def test_reconstruction_keeps_entries_on_both_partial_days_and_midnight(self):
        end=3*a.DAY;n=end//900000+1;t=np.arange(n,dtype=np.int64)*900000
        c=np.full(n,100.);raw=(t,c,c+.1,c-.1,c)
        rows=[]
        for i,entry in enumerate((0,a.DAY-a.OFFSET,end-1800000)):
            rows.append(dict(symbol='X'+str(i),entry_time=entry,exit_time=entry+900000,
                             entry=100.,exit=103.,sl=99.5,reason='TP',side=1,score=1.,max_hold_bars=24))
        r,tr,day,curve=p.simulate(pd.DataFrame(rows),p.Market({x['symbol']:raw for x in rows}),0,end,all_kst_days=True)
        rebuilt=a.rebuild(curve,tr,0,end)
        pd.testing.assert_frame_equal(day,rebuilt)
        self.assertAlmostEqual(np.prod(1+rebuilt.return_pct/100),1+r['net_return_pct']/100)

    def test_incomplete_curve_cannot_be_silently_filled(self):
        curve=pd.DataFrame(dict(time=[0,1800000],equity=[1.,1.],equity_pre_entry=[1.,1.],positions=[0,0]))
        with self.assertRaises(AssertionError):a.rebuild(curve,pd.DataFrame(),0,1800000)

if __name__=='__main__':unittest.main()
