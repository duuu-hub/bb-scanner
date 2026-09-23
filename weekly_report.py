from __future__ import annotations

import csv
import json
import math
import os
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

SIGNALS = Path("signals/pending.jsonl")
STATE = Path("state/trading_state.json")
LOG = Path("logs/executions.jsonl")
PAPER = Path("paper_signals.csv")
CONFIG = Path("config/trading_config.json")
CORE = ("L1", "L2", "L3")
SHADOW = ("S1", "S2")


def iso_ms(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def pf(values: list[float]) -> float | None:
    wins = sum(x for x in values if x > 0)
    losses = abs(sum(x for x in values if x < 0))
    if losses == 0:
        return math.inf if wins > 0 else None
    return wins / losses


def max_drawdown(weighted_returns: list[float]) -> float:
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for ret in weighted_returns:
        equity *= 1.0 + ret / 100.0
        peak = max(peak, equity)
        worst = min(worst, (equity / peak - 1.0) * 100.0)
    return worst


def max_loss_streak(values: list[float]) -> int:
    best = cur = 0
    for x in values:
        cur = cur + 1 if x < 0 else 0
        best = max(best, cur)
    return best


def fnum(value, digits=2):
    if value is None:
        return "N/A"
    if value == math.inf:
        return "∞"
    return f"{value:.{digits}f}"


def telegram(text: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        return
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat, "text": text, "disable_web_page_preview": True},
        timeout=15,
    )
    r.raise_for_status()


def main() -> int:
    now_ms = int(time.time() * 1000)
    cutoff_ms = now_ms - 7 * 24 * 60 * 60 * 1000
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))

    signals = []
    for row in read_jsonl(SIGNALS):
        try:
            created = iso_ms(str(row.get("signal_created_at") or ""))
        except Exception:
            created = int(row.get("signal_time_ms") or 0)
        if created >= cutoff_ms and row.get("portfolio") == "LONG3":
            signals.append(row)

    events = []
    for row in read_jsonl(LOG):
        try:
            ts = iso_ms(row["timestamp_utc"])
        except Exception:
            continue
        if ts >= cutoff_ms:
            row["_ts_ms"] = ts
            events.append(row)

    state = {}
    if STATE.exists():
        try:
            state = json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            state = {}
    closed = [
        x for x in state.get("closed_trades", [])
        if int(x.get("closed_at_ms") or 0) >= cutoff_ms
    ]
    spread_shadow_closed = [
        x for x in state.get("spread_shadow_closed", [])
        if int(x.get("closed_at_ms") or 0) >= cutoff_ms
    ]
    spread_shadow_open = list(state.get("spread_shadow_open", []))
    signal_shadow_closed = [
        x for x in state.get("signal_shadow_closed", [])
        if int(x.get("closed_at_ms") or 0) >= cutoff_ms
    ]
    signal_shadow_open = list(state.get("signal_shadow_open", []))

    signal_counts = Counter(str(x.get("strategy")) for x in signals)
    entries = [x for x in events if x.get("event") == "ENTRY"]
    entry_counts = Counter(str(x.get("strategy")) for x in entries)
    skips = [x for x in events if x.get("event") == "SKIP"]
    reject_counts = Counter(str(x.get("reason")) for x in skips)
    spread_reject_values = [
        float(x["spread_pct"])
        for x in skips
        if x.get("reason") == "SPREAD_TOO_WIDE" and x.get("spread_pct") is not None
    ]

    closed_by_strategy = defaultdict(list)
    for trade in closed:
        ret = trade.get("return_pct")
        if ret is None:
            continue
        try:
            closed_by_strategy[str(trade.get("strategy"))].append(float(ret))
        except Exception:
            pass

    all_returns = [r for code in CORE for r in closed_by_strategy[code]]
    weight = float(cfg.get("position_size_pct", 30.0)) / 100.0
    weighted = [r * weight for r in all_returns]
    compounded = (math.prod(1.0 + r / 100.0 for r in weighted) - 1.0) * 100.0 if weighted else 0.0

    delays = [float(x["total_delay_ms"]) / 1000.0 for x in entries if x.get("total_delay_ms") is not None]
    slips = [float(x["entry_slippage_pct"]) for x in entries if x.get("entry_slippage_pct") is not None]
    exit_counts = Counter(str(x.get("close_reason") or "UNKNOWN") for x in closed)

    max_open = max([int(x.get("open_positions_after") or 0) for x in entries] or [0])
    max_exp = max([float(x.get("gross_exposure_after_pct") or 0) for x in entries] or [0.0])

    entry_by_id = {str(x.get("signal_id")): x for x in entries}
    regime_returns = defaultdict(list)
    for trade in closed:
        ret = trade.get("return_pct")
        if ret is None:
            continue
        entry = entry_by_id.get(str(trade.get("signal_id")), {})
        snap = entry.get("market_snapshot") or trade.get("market_snapshot") or {}
        regime = str(snap.get("market_regime") or "UNKNOWN")
        regime_returns[regime].append(float(ret))

    signal_shadow_returns = []
    signal_shadow_exit_counts = Counter()
    signal_shadow_by_strategy = defaultdict(list)
    for item in signal_shadow_closed:
        signal_shadow_exit_counts[str(item.get("shadow_close_reason") or "UNKNOWN")] += 1
        if item.get("shadow_return_pct") is not None:
            try:
                value = float(item["shadow_return_pct"])
                signal_shadow_returns.append(value)
                signal_shadow_by_strategy[str(item.get("strategy"))].append(value)
            except Exception:
                pass

    spread_shadow_returns = []
    spread_shadow_exit_counts = Counter()
    for item in spread_shadow_closed:
        spread_shadow_exit_counts[str(item.get("shadow_close_reason") or "UNKNOWN")] += 1
        if item.get("shadow_return_pct") is not None:
            try:
                spread_shadow_returns.append(float(item["shadow_return_pct"]))
            except Exception:
                pass

    shadow_counts = Counter()
    if PAPER.exists():
        with PAPER.open("r", encoding="utf-8", newline="") as fp:
            for row in csv.DictReader(fp):
                if str(row.get("strategy") or "").upper() not in SHADOW:
                    continue
                try:
                    if iso_ms(row["timestamp_utc"]) >= cutoff_ms:
                        shadow_counts[str(row["strategy"]).upper()] += 1
                except Exception:
                    pass

    lines = [
        "📊 LONG3 Demo 주간 요약 (최근 7일)",
        f"mode={cfg.get('trading_mode')} auto={cfg.get('demo_auto_execute')} live={cfg.get('live_trading_enabled')}",
        "",
    ]

    for code in CORE:
        vals = closed_by_strategy[code]
        lines.append(
            f"{code}: signal={signal_counts[code]} entry={entry_counts[code]} "
            f"closed={len(vals)} win={fnum((sum(x > 0 for x in vals)/len(vals)*100) if vals else None)}% "
            f"avg={fnum(statistics.mean(vals) if vals else None)}% PF={fnum(pf(vals))}"
        )

    lines += [
        "",
        f"Core closed={len(all_returns)} | PF={fnum(pf(all_returns))} | "
        f"30%-weighted compounded≈{fnum(compounded)}% | DD≈{fnum(max_drawdown(weighted))}% | "
        f"max loss streak={max_loss_streak(all_returns)}",
        f"Delay avg/max={fnum(statistics.mean(delays) if delays else None)}/{fnum(max(delays) if delays else None)}s",
        f"Slippage avg/max={fnum(statistics.mean(slips) if slips else None, 4)}/{fnum(max(slips) if slips else None, 4)}%",
        f"Max concurrent={max_open} | max actual exposure={fnum(max_exp)}%",
        "Rejects: " + (", ".join(f"{k}={v}" for k, v in reject_counts.most_common()) or "none"),
        (
            "Spread rejects actual spread avg/max="
            f"{fnum(statistics.mean(spread_reject_values) if spread_reject_values else None, 4)}/"
            f"{fnum(max(spread_reject_values) if spread_reject_values else None, 4)}%"
        ),
        "Exits: " + (", ".join(f"{k}={v}" for k, v in exit_counts.most_common()) or "none"),
        (
            f"All-signal shadow: open={len(signal_shadow_open)} closed={len(signal_shadow_closed)} "
            f"avg={fnum(statistics.mean(signal_shadow_returns) if signal_shadow_returns else None)}% "
            f"PF={fnum(pf(signal_shadow_returns))} | 30%-weighted compounded≈"
            f"{fnum((math.prod(1.0 + (r*weight)/100.0 for r in signal_shadow_returns)-1.0)*100.0 if signal_shadow_returns else 0.0)}% | outcomes="
            + (", ".join(f"{k}={v}" for k, v in signal_shadow_exit_counts.most_common()) or "none")
            + " (모든 LONG3 신호, detected_price 가상진입)"
        ),
        (
            f"Spread-filter shadow: open={len(spread_shadow_open)} closed={len(spread_shadow_closed)} "
            f"avg={fnum(statistics.mean(spread_shadow_returns) if spread_shadow_returns else None)}% "
            f"PF={fnum(pf(spread_shadow_returns))} | outcomes="
            + (", ".join(f"{k}={v}" for k, v in spread_shadow_exit_counts.most_common()) or "none")
            + " (스프레드 거부건, 당시 ask 가상진입)"
        ),
    ]

    if signal_shadow_by_strategy:
        parts = []
        for code in CORE:
            vals = signal_shadow_by_strategy.get(code, [])
            if vals:
                parts.append(
                    f"{code}: n={len(vals)} avg={fnum(statistics.mean(vals))}% PF={fnum(pf(vals))}"
                )
        if parts:
            lines.append("All-signal shadow by strategy: " + " | ".join(parts))

    if regime_returns:
        parts = []
        for regime, vals in sorted(regime_returns.items()):
            parts.append(f"{regime}: n={len(vals)} avg={fnum(statistics.mean(vals))}% PF={fnum(pf(vals))}")
        lines.append("Regime: " + " | ".join(parts))

    lines += [
        f"Shadow signal-only: S1={shadow_counts['S1']} S2={shadow_counts['S2']} "
        "(주문 없음; core와 혼합하지 않음)",
        "H1/H3/H4는 entry market_snapshot, H2는 SL_ALREADY_TOUCHED 거부, "
        "H5는 total_delay_ms로 사후 비교 가능",
    ]

    report = "\n".join(lines)
    print(report)
    telegram(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
