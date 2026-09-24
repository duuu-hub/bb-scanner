from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

QUARTER_SECONDS = 15 * 60
DEFAULT_CYCLES = 16
FORWARD_PATHS = (
    "state.json",
    "paper_signals.csv",
    "scan_runtime.json",
    "signals/pending.jsonl",
    "state/trading_state.json",
    "logs/executions.jsonl",
)
TRADING_STATE_PATH = Path("state/trading_state.json")
SCAN_RUNTIME_PATH = Path("scan_runtime.json")


def utc_text(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).isoformat()


def next_quarter_epoch(now_seconds: float) -> int:
    """Return the next future :00/:15/:30/:45 boundary.

    Scheduled watcher handoffs always target a future boundary. This avoids
    re-processing the last boundary and avoids placing already-stale orders
    when a queued GitHub job starts a few minutes after a boundary.
    """
    return (int(now_seconds // QUARTER_SECONDS) + 1) * QUARTER_SECONDS


def scheduled_boundaries(now_seconds: float, cycles: int) -> list[int]:
    first = next_quarter_epoch(now_seconds)
    return [first + index * QUARTER_SECONDS for index in range(cycles)]


def sleep_until(target_epoch: int) -> None:
    while True:
        remaining = target_epoch - time.time()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 30.0))


def run_command(label: str, args: list[str]) -> None:
    print(f"[WATCHER] {label}: {' '.join(args)}", flush=True)
    result = subprocess.run(args, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"{label} failed with exit code {result.returncode}")


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def verify_scanner_boundary(expected_epoch: int) -> None:
    runtime = load_json(SCAN_RUNTIME_PATH)
    actual_ms = int(runtime.get("signal_boundary_ms") or 0)
    expected_ms = int(expected_epoch * 1000)
    if actual_ms != expected_ms:
        raise RuntimeError(
            f"scanner boundary mismatch: expected={expected_ms} actual={actual_ms}"
        )


def pending_expiry_ms() -> int | None:
    state = load_json(TRADING_STATE_PATH)
    values = []
    for item in state.get("pending_entries", []) or []:
        try:
            values.append(int(item.get("expires_at_ms") or 0))
        except (TypeError, ValueError):
            continue
    values = [value for value in values if value > 0]
    return max(values) if values else None


def git_run(args: list[str], *, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        check=False,
        text=True,
        capture_output=capture,
    )


def persist_forward_state(max_attempts: int = 5) -> bool:
    """Commit/push forward state with bounded retry.

    LONG3's concurrency group guarantees only one active watcher. Other
    research should use separate branches, but this retry also tolerates
    unrelated main-branch commits and transient GitHub push failures.
    """
    if os.getenv("GITHUB_ACTIONS", "").lower() != "true":
        print("[WATCHER] local run: git persistence skipped", flush=True)
        return True

    branch = os.getenv("LONG3_STATE_BRANCH", "main")
    git_run(["config", "user.name", "github-actions[bot]"])
    git_run([
        "config",
        "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com",
    ])

    existing = [path for path in FORWARD_PATHS if Path(path).exists()]
    status = git_run(["status", "--porcelain", "--", *FORWARD_PATHS], capture=True)
    if status.returncode != 0:
        print(f"[WATCHER][WARN] git status failed: {status.stderr}", flush=True)
        return False

    if status.stdout.strip() and existing:
        add = git_run(["add", "--", *existing])
        if add.returncode != 0:
            print("[WATCHER][WARN] git add failed", flush=True)
            return False
        staged = git_run(["diff", "--cached", "--quiet"])
        if staged.returncode == 1:
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            commit = git_run(
                ["commit", "-m", f"chore: persist LONG3 forward state {stamp}"]
            )
            if commit.returncode != 0:
                print("[WATCHER][WARN] git commit failed", flush=True)
                return False
        elif staged.returncode not in (0, 1):
            print("[WATCHER][WARN] git diff --cached failed", flush=True)
            return False

    for attempt in range(1, max_attempts + 1):
        pull = git_run(["pull", "--rebase", "origin", branch])
        if pull.returncode != 0:
            git_run(["rebase", "--abort"])
            print(
                f"[WATCHER][WARN] persist pull failed attempt {attempt}/{max_attempts}",
                flush=True,
            )
        else:
            push = git_run(["push", "origin", f"HEAD:{branch}"])
            if push.returncode == 0:
                print("[WATCHER] forward state persisted", flush=True)
                return True
            print(
                f"[WATCHER][WARN] persist push failed attempt {attempt}/{max_attempts}",
                flush=True,
            )

        if attempt < max_attempts:
            time.sleep(min(2 ** attempt, 30))

    print("[WATCHER][ERROR] forward state persistence exhausted retries", flush=True)
    return False


def run_boundary_cycle(target_epoch: int) -> bool:
    """Run one exact-boundary scan -> immediate order -> maker finalizer cycle."""
    lag = time.time() - target_epoch
    if lag > 60:
        print(
            f"[WATCHER][WARN] skipping boundary {utc_text(target_epoch)}; "
            f"watcher is {lag:.1f}s late",
            flush=True,
        )
        return False

    try:
        run_command("scanner", [sys.executable, "scanner.py"])
        verify_scanner_boundary(target_epoch)
        run_command("LONG3 adapter", [sys.executable, "long3_signal_adapter.py"])
        run_command("Demo trader immediate", [sys.executable, "auto_trader.py"])

        expiry_ms = pending_expiry_ms()
        if expiry_ms is not None:
            finalizer_target = expiry_ms / 1000.0 + 5.0
            remaining = max(0.0, finalizer_target - time.time())
            print(
                f"[WATCHER] Maker pending; finalizer in {remaining:.1f}s "
                f"at {utc_text(finalizer_target)}",
                flush=True,
            )
            sleep_until(int(finalizer_target))
            if time.time() < finalizer_target:
                time.sleep(finalizer_target - time.time())
            run_command(
                "Demo trader Maker finalizer",
                [sys.executable, "auto_trader.py"],
            )

        return True
    except Exception as exc:
        print(f"[WATCHER][ERROR] boundary cycle failed: {exc}", flush=True)
        return False
    finally:
        persist_forward_state()


def manual_cycle() -> int:
    """Preserve workflow_dispatch as a one-shot immediate diagnostic run."""
    ok = True
    try:
        run_command("scanner", [sys.executable, "scanner.py"])
        run_command("LONG3 adapter", [sys.executable, "long3_signal_adapter.py"])
        run_command("Demo trader immediate", [sys.executable, "auto_trader.py"])
        expiry_ms = pending_expiry_ms()
        if expiry_ms is not None:
            finalizer_target = expiry_ms / 1000.0 + 5.0
            sleep_until(int(finalizer_target))
            if time.time() < finalizer_target:
                time.sleep(finalizer_target - time.time())
            run_command(
                "Demo trader Maker finalizer",
                [sys.executable, "auto_trader.py"],
            )
    except Exception as exc:
        ok = False
        print(f"[WATCHER][ERROR] manual cycle failed: {exc}", flush=True)
    finally:
        ok = persist_forward_state() and ok
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="LONG3 four-hour boundary watcher")
    parser.add_argument("--cycles", type=int, default=DEFAULT_CYCLES)
    parser.add_argument("--manual-immediate", action="store_true")
    args = parser.parse_args()

    if args.cycles < 1 or args.cycles > 16:
        raise SystemExit("--cycles must be between 1 and 16")

    if args.manual_immediate:
        return manual_cycle()

    boundaries = scheduled_boundaries(time.time(), args.cycles)
    print(
        f"[WATCHER] starting {args.cycles}-boundary session; "
        f"first={utc_text(boundaries[0])} last={utc_text(boundaries[-1])}",
        flush=True,
    )

    failures = 0
    for index, target in enumerate(boundaries, start=1):
        print(
            f"[WATCHER] cycle {index}/{len(boundaries)} waiting for {utc_text(target)}",
            flush=True,
        )
        sleep_until(target)
        if not run_boundary_cycle(target):
            failures += 1

    print(
        f"[WATCHER] session complete cycles={len(boundaries)} failures={failures}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
