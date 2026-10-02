# V16 Implementation Contract

Frozen before first V16 execution.

- Economic mechanism: breadth impulse identifies a market shock; entry is forbidden until a later first controlled pullback and a still-later closed-bar reclaim.
- Grid: side {long,short} × horizon {16,96} × breadth crossing {10%,20%} × pullback depth {0.5,1.0 ATR} = 16 entries; holds {16,48} × exits {TP2,TP3,TRAIL} = 96 policies.
- Event shock thresholds remain 2%/5%; global denominators >=30 both bars; expansion >=5pp.
- Pullback touch is limited to event+1..event+10; reclaim event+2..event+12 and strictly later than touch. Same-bar touch/reclaim is impossible by construction.
- Invalidation, next-open entry, gap rule, structural stop, ranking formula, costs, chronology, selection and account constraints are exactly PLAN.md.
- Source run 36095439671 and frozen BTC artifact; DEV selection precedes any 2024 processing.
