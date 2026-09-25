import unittest
import pandas as pd
import sys
sys.path.insert(0,"research")
import continuation_execution_core as e

def bars(rows):
 return pd.DataFrame(rows,columns=["timestamp_ms","open","high","low","close"])

class T(unittest.TestCase):
 def test_entry_is_next_open_and_entry_bar_tp(self):
  g=bars([(0,100,101,99,100),(900000,110,116,109,112),(1800000,112,113,111,112)])
  r=e.replay_trade(g,0,"LONG",5,3,4)
  self.assertEqual(r["entry_i"],1); self.assertEqual(r["entry_px"],110); self.assertEqual(r["exit_i"],1); self.assertEqual(r["exit_reason"],"TP")
 def test_does_not_use_signal_candle_extremes(self):
  g=bars([(0,100,200,1,100),(900000,100,101,99,100),(1800000,100,106,99,105)])
  r=e.replay_trade(g,0,"LONG",5,3,4); self.assertEqual(r["exit_i"],2); self.assertEqual(r["exit_reason"],"TP")
 def test_both_is_sl(self):
  g=bars([(0,100,100,100,100),(900000,100,106,96,100)])
  r=e.replay_trade(g,0,"LONG",5,3,4); self.assertEqual(r["exit_reason"],"BOTH_SL"); self.assertEqual(r["gross_ret_pct"],-3)
 def test_gap_rejects_entry(self):
  g=bars([(0,100,100,100,100),(1800000,100,106,96,100)])
  self.assertIsNone(e.replay_trade(g,0,"LONG",5,3,4))
 def test_horizon_excludes_deadline_bar(self):
  g=bars([(0,100,100,100,100),(900000,100,101,99,100),(1800000,100,101,99,100),(2700000,100,101,99,100),(3600000,100,101,99,100),(4500000,100,106,99,105)])
  r=e.replay_trade(g,0,"LONG",5,3,4); self.assertEqual(r["exit_reason"],"TIME"); self.assertEqual(r["exit_i"],4)
 def test_short_direction(self):
  g=bars([(0,100,100,100,100),(900000,100,101,94,95)])
  r=e.replay_trade(g,0,"SHORT",5,3,4); self.assertEqual(r["exit_reason"],"TP")
if __name__=="__main__": unittest.main()
