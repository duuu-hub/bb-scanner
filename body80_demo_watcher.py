from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone

import body80_demo

BAR_SEC = 15 * 60
BAR_MS = BAR_SEC * 1000


def utc_text(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def next_boundary(now: float) -> int:
    return (int(now // BAR_SEC) + 1) * BAR_SEC


def sleep_until(target: float) -> None:
    while True:
        rem = target - time.time()
        if rem <= 0:
            return
        time.sleep(min(rem, 20.0))


def eligible_entry_boundary(epoch_sec: int) -> bool:
    # Signal candle open hour must be 12..17 UTC.
    signal_open = epoch_sec - BAR_SEC
    hour = datetime.fromtimestamp(signal_open, tz=timezone.utc).hour
    return 12 <= hour <= 17


def first_eligible_boundary(now: float) -> int:
    t = next_boundary(now)
    # At most 24h search.
    for _ in range(96):
        if eligible_entry_boundary(t):
            return t
        t += BAR_SEC
    raise RuntimeError("no eligible BODY80 entry boundary found within 24h")


def run_cycles(first_epoch: int, cycles: int, manage_only: bool) -> int:
    failures = 0
    for i in range(cycles):
        target = first_epoch + i * BAR_SEC
        # Give the exchange a small publication buffer while staying far inside 55s TTL.
        invoke_at = target + 2.0
        print(
            f"[BODY80-WATCHER] cycle {i+1}/{cycles} "
            f"target={utc_text(target)} manage_only={manage_only}",
            flush=True,
        )
        sleep_until(invoke_at)
        lag = time.time() - target
        if not manage_only and lag > 50:
            print(
                f"[BODY80-WATCHER][WARN] skip stale entry boundary lag={lag:.1f}s",
                flush=True,
            )
            # Still manage existing trades at this boundary.
            try:
                r = body80_demo.run_boundary(target * 1000, manage_only=True)
                print("[BODY80-WATCHER] " + json.dumps(r, sort_keys=True), flush=True)
            except Exception as exc:
                failures += 1
                print(f"[BODY80-WATCHER][ERROR] manage on stale boundary: {exc}", flush=True)
            continue

        try:
            r = body80_demo.run_boundary(target * 1000, manage_only=manage_only)
            print("[BODY80-WATCHER] " + json.dumps(r, sort_keys=True), flush=True)
        except BaseException as exc:
            failures += 1
            print(f"[BODY80-WATCHER][ERROR] boundary failed: {exc}", flush=True)

    print(f"[BODY80-WATCHER] done cycles={cycles} failures={failures}", flush=True)
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("entry-window", "manage-tail"), required=True)
    ap.add_argument("--cycles", type=int, default=24)
    ap.add_argument("--first-boundary-epoch", type=int)
    a = ap.parse_args()

    if a.cycles < 1 or a.cycles > 24:
        raise SystemExit("--cycles must be 1..24")

    if a.first_boundary_epoch is not None:
        first = int(a.first_boundary_epoch)
    elif a.mode == "entry-window":
        first = first_eligible_boundary(time.time())
    else:
        first = next_boundary(time.time())

    if first % BAR_SEC != 0:
        raise SystemExit("first boundary must be exact 15m UTC boundary")
    if a.mode == "entry-window" and not eligible_entry_boundary(first):
        raise SystemExit("entry-window first boundary is not BODY80 eligible")

    print(
        f"[BODY80-WATCHER] start mode={a.mode} first={utc_text(first)} cycles={a.cycles}",
        flush=True,
    )
    return run_cycles(first, a.cycles, manage_only=(a.mode == "manage-tail"))


if __name__ == "__main__":
    raise SystemExit(main())
