# V13 implementation contract

PLAN preregister commit1859c6096948fc851bca09d267ce3642028da838 predates all V13 code/tests.
16 entry settings/96 policies only. Channel/baseline/ATR cutoffs exclude breakout.
Snapshot algorithm processes closed breakout indices chronologically; pending state
blocks later breakouts until first retest,failed close,path gap or expiry.
Level,ATR,volume baseline remain frozen. Retest can signal only age2..wait.
Low-volume mean includes b+1..r and excludes b. First event consumes state before
known next-open validation;48bar accepted-intent cooldown is independent of exits.
Entry open only is observed after r; entry high/low/close do not define signal/SL.
Stop floor .5%,ceiling6%; far retest wick on loss side of actual fill; TP actual
entry/SL. Shared canonical chronology/account code unchanged. Original invalid
official bytes retained through the previously validated helper. All96 failed
cells,source hashes,counts,raw ledger metadata and exact minute evidence persist.
No post-DEV or 2024 selection tuning.

Meaningful V13 new31 tests cover independent prior ATR/channel/volume,feature
prefix perturbations,gaps,exact BTC,mirrored snapshot/retest,first/last wait,
frozen level across intervening new breakout,close invalidation,quote cutoff,
consume invalid known fill,cooldown,entry stops and both-side actual1m chronology,
missing/mismatch exclusions,856/256 source catalogue,tampered selection and
complete8-shard synthetic pipeline. All previous307 tests also remain required.

Local first31test validation failure was terminal-newline corruption of local
immutable text copies; Git prereg hashes were exact and unaffected. Copying local
source bytes fixed the mismatch without changing economic rules. Full failure
record remains LOCAL_VALIDATION_FAILURES.json; initial stdout was truncated, so
complete first stdout is not claimed. Local numba unavailable as documented by
V12; actual pinned CI canonical chronology/invariants must pass before markets.
