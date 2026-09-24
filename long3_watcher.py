from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
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


def git_run(
    args: list[str],
    *,
    cwd: str | Path | None = None,
    capture: bool = False,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd is not None else None,
        check=False,
        text=True,
        capture_output=capture,
    )


def current_code_sha(workspace: str | Path = ".") -> str:
    result = git_run(["rev-parse", "HEAD"], cwd=workspace, capture=True)
    if result.returncode != 0:
        return "UNKNOWN"
    return result.stdout.strip() or "UNKNOWN"


def copy_forward_snapshot(
    workspace: str | Path,
    state_worktree: str | Path,
    forward_paths: tuple[str, ...] = FORWARD_PATHS,
) -> list[str]:
    """Copy only mutable forward-state files into an isolated worktree."""
    workspace = Path(workspace)
    state_worktree = Path(state_worktree)
    copied = []
    for relative in forward_paths:
        source = workspace / relative
        if not source.exists() or not source.is_file():
            continue
        destination = state_worktree / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.append(relative)
    return copied


def _persist_snapshot_to_branch(
    workspace: str | Path,
    branch: str,
    forward_paths: tuple[str, ...] = FORWARD_PATHS,
    *,
    remote: str = "origin",
    max_attempts: int = 5,
) -> bool:
    """Persist state without ever updating the active watcher's checkout.

    Every attempt creates a disposable worktree from the latest remote branch,
    overlays only the mutable forward-state files, commits them there, and
    pushes that temporary commit. If research/code was merged meanwhile, a
    non-fast-forward push simply retries from the newer remote branch.

    The running watcher's own HEAD and source files therefore remain pinned to
    the commit checked out when this 4-hour session started.
    """
    workspace = Path(workspace).resolve()

    for attempt in range(1, max_attempts + 1):
        fetch = git_run(["fetch", remote, branch], cwd=workspace, capture=True)
        if fetch.returncode != 0:
            print(
                f"[WATCHER][WARN] state fetch failed attempt {attempt}/{max_attempts}: "
                f"{fetch.stderr.strip()}",
                flush=True,
            )
            if attempt < max_attempts:
                time.sleep(min(2 ** attempt, 30))
            continue

        temp_path = Path(tempfile.mkdtemp(prefix="long3-state-"))
        # git worktree add expects to create the target path itself.
        temp_path.rmdir()
        worktree_added = False
        try:
            add_worktree = git_run(
                ["worktree", "add", "--detach", str(temp_path), f"{remote}/{branch}"],
                cwd=workspace,
                capture=True,
            )
            if add_worktree.returncode != 0:
                print(
                    f"[WATCHER][WARN] state worktree failed attempt "
                    f"{attempt}/{max_attempts}: {add_worktree.stderr.strip()}",
                    flush=True,
                )
                continue
            worktree_added = True

            copied = copy_forward_snapshot(workspace, temp_path, forward_paths)
            if not copied:
                print("[WATCHER] no forward-state files to persist", flush=True)
                return True

            git_run(
                ["config", "user.name", "github-actions[bot]"],
                cwd=temp_path,
            )
            git_run(
                [
                    "config",
                    "user.email",
                    "41898282+github-actions[bot]@users.noreply.github.com",
                ],
                cwd=temp_path,
            )

            add = git_run(["add", "--", *copied], cwd=temp_path, capture=True)
            if add.returncode != 0:
                print(
                    f"[WATCHER][WARN] state git add failed: {add.stderr.strip()}",
                    flush=True,
                )
                continue

            staged = git_run(["diff", "--cached", "--quiet"], cwd=temp_path)
            if staged.returncode == 0:
                print("[WATCHER] forward state already current on main", flush=True)
                return True
            if staged.returncode != 1:
                print("[WATCHER][WARN] state staged diff check failed", flush=True)
                continue

            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            commit = git_run(
                ["commit", "-m", f"chore: persist LONG3 forward state {stamp}"],
                cwd=temp_path,
                capture=True,
            )
            if commit.returncode != 0:
                print(
                    f"[WATCHER][WARN] state commit failed: {commit.stderr.strip()}",
                    flush=True,
                )
                continue

            push = git_run(
                ["push", remote, f"HEAD:{branch}"],
                cwd=temp_path,
                capture=True,
            )
            if push.returncode == 0:
                print(
                    "[WATCHER] forward state persisted via isolated worktree; "
                    "active code remains pinned",
                    flush=True,
                )
                return True

            print(
                f"[WATCHER][WARN] state push failed attempt {attempt}/{max_attempts}: "
                f"{push.stderr.strip()}",
                flush=True,
            )
        finally:
            if worktree_added:
                git_run(
                    ["worktree", "remove", "--force", str(temp_path)],
                    cwd=workspace,
                    capture=True,
                )
            if temp_path.exists():
                shutil.rmtree(temp_path, ignore_errors=True)
            git_run(["worktree", "prune"], cwd=workspace, capture=True)

        if attempt < max_attempts:
            time.sleep(min(2 ** attempt, 30))

    print("[WATCHER][ERROR] forward state persistence exhausted retries", flush=True)
    return False


def persist_forward_state(max_attempts: int = 5) -> bool:
    """Persist only state/log files while keeping this session's code frozen."""
    if os.getenv("GITHUB_ACTIONS", "").lower() != "true":
        print("[WATCHER] local run: git persistence skipped", flush=True)
        return True

    return _persist_snapshot_to_branch(
        Path.cwd(),
        os.getenv("LONG3_STATE_BRANCH", "main"),
        FORWARD_PATHS,
        max_attempts=max_attempts,
    )

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

    session_sha = current_code_sha()
    print(
        f"[WATCHER] session code pinned at {session_sha}; "
        "main updates apply only to the next watcher",
        flush=True,
    )

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
